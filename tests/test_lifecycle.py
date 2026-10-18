"""Lifecycle scheduling tests use controlled controller I/O and monotonic time."""

from threading import Event, Thread

import pytest

from halo_soundbar_service.controller import OperationResult, OperationStatus
from halo_soundbar_service.lifecycle import ServiceLifecycle, StartupRetryPolicy


def result(status=OperationStatus.SUCCESS):
    return OperationResult(status, 0, None, None, "")


class Clock:
    def __init__(self, now=30.0):
        self.now = now
        self.waits = []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return False


class Controllers:
    def __init__(self, action):
        self.action = action
        self.calls = []
        self.instances = []

    def __call__(self):
        owner = self

        class Controller:
            def set_power(self, enabled, deadline, cancel_event=None):
                owner.calls.append((enabled, deadline, cancel_event))
                return owner.action(enabled, deadline, cancel_event)

        controller = Controller()
        self.instances.append(controller)
        return controller


def test_startup_retries_device_not_ready_with_capped_backoff_until_success():
    clock, stop = Clock(), Event()
    outcomes = iter([result(OperationStatus.DEVICE_UNAVAILABLE)] * 5 + [result()])
    factory = Controllers(lambda *_: next(outcomes))
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)

    outcome = lifecycle.run_startup()

    assert outcome.status is OperationStatus.SUCCESS
    assert clock.waits == [1.0, 2.0, 4.0, 5.0, 5.0]
    assert [call[:2] for call in factory.calls] == [(True, 150.0)] * 6
    assert all(call[2] is not None and not call[2].is_set() for call in factory.calls)
    assert len(factory.instances) == 6


def test_startup_gives_up_at_total_timeout_but_lifecycle_remains_alive():
    clock, stop = Clock(), Event()
    factory = Controllers(lambda *_: result(OperationStatus.DEVICE_UNAVAILABLE))
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)

    outcome = lifecycle.run_startup()

    assert outcome.status is OperationStatus.DEADLINE_EXCEEDED
    assert clock.now == 150.0
    assert clock.waits[:4] == [1.0, 2.0, 4.0, 5.0]
    assert clock.waits[-1] == 3.0
    assert max(clock.waits) <= 5.0
    assert not stop.is_set()
    assert all(deadline == 150.0 for _, deadline, _ in factory.calls)


@pytest.mark.parametrize("status", [
    OperationStatus.SUCCESS, OperationStatus.PROTOCOL_ERROR,
    OperationStatus.EXTERNAL_STATE_CHANGED, OperationStatus.VERIFICATION_FAILED,
    OperationStatus.CANCELLED, OperationStatus.DEADLINE_EXCEEDED,
])
def test_startup_returns_terminal_controller_result_without_retry(status):
    clock, terminal = Clock(), result(status)
    factory = Controllers(lambda *_: terminal)
    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)

    assert lifecycle.run_startup() is terminal
    assert clock.waits == []
    assert len(factory.calls) == 1


def test_startup_limits_wait_to_budget_remaining_after_controller_work():
    clock = Clock()

    def unavailable(*_):
        clock.now += 119.75
        return result(OperationStatus.DEVICE_UNAVAILABLE)

    factory = Controllers(unavailable)
    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)

    assert lifecycle.run_startup().status is OperationStatus.DEADLINE_EXCEEDED
    assert clock.waits == [0.25]
    assert len(factory.calls) == 1


def test_startup_interrupted_wait_returns_cancelled_without_another_attempt():
    clock = Clock()
    factory = Controllers(lambda *_: result(OperationStatus.DEVICE_UNAVAILABLE))
    lifecycle = ServiceLifecycle(factory, Event(), clock, lambda _: True)

    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    assert len(factory.calls) == 1


def test_startup_counts_controller_factory_time_in_absolute_deadline():
    clock = Clock()
    controllers = Controllers(lambda *_: result(OperationStatus.DEVICE_UNAVAILABLE))

    def factory():
        clock.now += 120.0
        return controllers()

    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)

    assert lifecycle.run_startup().status is OperationStatus.DEADLINE_EXCEEDED
    assert controllers.calls[0][1] == 150.0
    assert clock.waits == []


@pytest.fixture
def workers():
    """Release every test gate and join every thread even on assertion failure."""
    threads, gates, errors = [], [], []

    def gate():
        event = Event()
        gates.append(event)
        return event

    def spawn(action):
        outcomes = []

        def run():
            try:
                outcomes.append(action())
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=run, daemon=True)
        threads.append(thread)
        thread.start()
        return thread, outcomes

    yield gate, spawn
    for event in gates:
        event.set()
    for thread in threads:
        thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads), "Lifecycle leaked a thread"
    assert errors == []


def test_preshutdown_cancels_startup_then_runs_one_off_transaction_without_deadlock(workers):
    gate, spawn = workers
    entered, release, off_started = gate(), gate(), gate()
    stop, clock = gate(), Clock()

    def transaction(enabled, deadline, cancel_event):
        if enabled:
            entered.set()
            assert cancel_event.wait(2)
            assert release.wait(2)
            clock.now = 47.0
            return result(OperationStatus.DEVICE_UNAVAILABLE)
        off_started.set()
        assert cancel_event is None
        return result()

    factory = Controllers(transaction)
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)
    startup, startup_results = spawn(lifecycle.run_startup)
    assert entered.wait(2)
    shutdown, shutdown_results = spawn(lifecycle.handle_preshutdown)
    assert stop.wait(2)
    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    assert not off_started.is_set()
    release.set()
    startup.join(2)
    shutdown.join(2)

    assert startup_results[0].status is OperationStatus.CANCELLED
    assert shutdown_results[0].status is OperationStatus.SUCCESS
    assert [call[:2] for call in factory.calls] == [(True, 150.0), (False, 59.0)]
    assert len(factory.instances) == 2
    assert clock.waits == []


def test_preshutdown_interrupts_retry_wait_and_prevents_another_on_transaction(workers):
    gate, spawn = workers
    waiting, release = gate(), gate()
    stop, clock = gate(), Clock()

    def wait(seconds):
        assert seconds == 1.0
        waiting.set()
        assert stop.wait(2)
        assert release.wait(2)
        return False  # Cancellation still wins if a custom waiter reports timeout.

    factory = Controllers(lambda enabled, *_: result(
        OperationStatus.DEVICE_UNAVAILABLE if enabled else OperationStatus.SUCCESS
    ))
    lifecycle = ServiceLifecycle(factory, stop, clock, wait)
    startup, startup_results = spawn(lifecycle.run_startup)
    assert waiting.wait(2)
    shutdown, shutdown_results = spawn(lifecycle.handle_preshutdown)
    assert stop.wait(2)
    release.set()
    startup.join(2)
    shutdown.join(2)

    assert startup_results[0].status is OperationStatus.CANCELLED
    assert shutdown_results[0].status is OperationStatus.SUCCESS
    assert [call[0] for call in factory.calls] == [True, False]


@pytest.mark.parametrize("status", [OperationStatus.SUCCESS, OperationStatus.VERIFICATION_FAILED])
def test_duplicate_preshutdown_and_shutdown_share_result(workers, status):
    gate, spawn = workers
    entered, release = gate(), gate()
    terminal, clock = result(status), Clock()

    def off(enabled, deadline, cancel_event):
        assert enabled is False
        assert deadline == 42.0
        assert cancel_event is None
        entered.set()
        assert release.wait(2)
        return terminal

    factory = Controllers(off)
    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)
    jobs = [spawn(lifecycle.handle_preshutdown)]
    assert entered.wait(2)
    jobs += [spawn(lifecycle.handle_preshutdown), spawn(lifecycle.handle_shutdown)]
    release.set()
    for thread, outcomes in jobs:
        thread.join(2)
        assert outcomes == [terminal]
        assert outcomes[0] is terminal
    assert lifecycle.handle_shutdown(budget_s=1.0) is terminal
    assert len(factory.calls) == 1
    assert lifecycle.run_startup().status is OperationStatus.CANCELLED


def test_manual_stop_never_calls_set_power_and_permanently_blocks_startup():
    stop, clock = Event(), Clock()
    factory = Controllers(lambda *_: result())
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)

    lifecycle.request_stop()
    lifecycle.request_stop()

    assert stop.is_set()
    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    stop.clear()  # STOP is irreversible for this lifecycle, even if a caller clears it.
    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    assert factory.calls == []


def test_manual_stop_during_controller_call_wins_over_success():
    clock, stop = Clock(), Event()

    def transaction(*_):
        lifecycle.request_stop()
        return result()

    factory = Controllers(transaction)
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)

    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    assert len(factory.calls) == 1
    assert clock.waits == []


def test_shutdown_after_manual_stop_still_runs_off_with_its_own_budget():
    clock, stop = Clock(), Event()
    factory = Controllers(lambda *_: result())
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)
    lifecycle.request_stop()

    assert lifecycle.handle_shutdown(budget_s=3.0).status is OperationStatus.SUCCESS
    assert factory.calls == [(False, 33.0, None)]


def test_startup_cancelled_before_entry_never_constructs_controller():
    clock, stop = Clock(), Event()
    stop.set()
    factory = Controllers(lambda *_: result())
    lifecycle = ServiceLifecycle(factory, stop, clock, clock.wait)

    assert lifecycle.run_startup().status is OperationStatus.CANCELLED
    assert factory.instances == []


def test_shutdown_exception_wakes_duplicate_callers_without_retry(workers):
    gate, spawn = workers
    entered, release = gate(), gate()
    clock, error = Clock(), RuntimeError("unexpected controller failure")

    def off(*_):
        entered.set()
        assert release.wait(2)
        raise error

    factory = Controllers(off)
    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)

    def shutdown():
        with pytest.raises(RuntimeError) as caught:
            lifecycle.handle_preshutdown()
        return caught.value

    first = spawn(shutdown)
    assert entered.wait(2)
    second = spawn(shutdown)
    release.set()
    for thread, outcomes in (first, second):
        thread.join(2)
        assert outcomes == [error]
    assert len(factory.calls) == 1


def test_startup_exception_releases_transaction_for_later_shutdown():
    clock = Clock()

    def action(enabled, *_):
        if enabled:
            raise RuntimeError("unexpected startup failure")
        return result()

    factory = Controllers(action)
    lifecycle = ServiceLifecycle(factory, Event(), clock, clock.wait)

    with pytest.raises(RuntimeError, match="unexpected startup failure"):
        lifecycle.run_startup()
    assert lifecycle.handle_shutdown().status is OperationStatus.SUCCESS
    assert [call[0] for call in factory.calls] == [True, False]
