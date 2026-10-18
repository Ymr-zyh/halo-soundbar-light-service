"""Platform-independent startup retries and service shutdown coordination."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, Event

from .controller import LightController, OperationResult, OperationStatus


@dataclass(frozen=True)
class StartupRetryPolicy:
    initial_delay_s: float = 1.0
    max_delay_s: float = 5.0
    total_timeout_s: float = 120.0


class ServiceLifecycle:
    def __init__(
        self,
        controller_factory: Callable[[], LightController],
        stop_event: Event,
        monotonic: Callable[[], float],
        wait: Callable[[float], bool],
    ):
        self._controller_factory = controller_factory
        self._stop_event = stop_event
        self._monotonic = monotonic
        self._wait = wait
        self._retry_policy = StartupRetryPolicy()
        self._condition = Condition()
        self._transaction_active = False
        self._stop_requested = False
        self._shutdown_requested = False
        self._shutdown_result: OperationResult | None = None
        self._shutdown_error: BaseException | None = None

    def run_startup(self) -> OperationResult:
        deadline = self._monotonic() + self._retry_policy.total_timeout_s
        with self._condition:
            while self._transaction_active and not self._cancelled():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return self._result(OperationStatus.DEADLINE_EXCEEDED)
                self._condition.wait(remaining)
            if self._cancelled():
                return self._result(OperationStatus.CANCELLED)
            self._transaction_active = True
        try:
            return self._run_startup(deadline)
        finally:
            with self._condition:
                self._transaction_active = False
                self._condition.notify_all()

    def _run_startup(self, deadline: float) -> OperationResult:
        delay = self._retry_policy.initial_delay_s
        while True:
            if self._cancelled():
                return self._result(OperationStatus.CANCELLED)
            if self._monotonic() >= deadline:
                return self._result(OperationStatus.DEADLINE_EXCEEDED)
            outcome = self._controller_factory().set_power(
                True, deadline, cancel_event=self._stop_event
            )
            if self._cancelled():
                return self._result(OperationStatus.CANCELLED)
            if outcome.status is not OperationStatus.DEVICE_UNAVAILABLE:
                return outcome
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return self._result(OperationStatus.DEADLINE_EXCEEDED)
            if self._wait(min(delay, remaining)):
                return self._result(OperationStatus.CANCELLED)
            delay = min(delay * 2, self._retry_policy.max_delay_s)

    def request_stop(self) -> None:
        """Cancel startup permanently; manual STOP does not change the lights."""
        with self._condition:
            self._stop_requested = True
            self._stop_event.set()
            self._condition.notify_all()

    def handle_preshutdown(self, budget_s: float = 12.0) -> OperationResult:
        """Cancel startup first, then perform exactly one uncancelled off call."""
        with self._condition:
            if self._shutdown_requested:
                while self._shutdown_result is None and self._shutdown_error is None:
                    self._condition.wait()
                if self._shutdown_error is not None:
                    raise self._shutdown_error
                return self._shutdown_result
            self._shutdown_requested = True
            self._stop_event.set()
            self._condition.notify_all()
            while self._transaction_active:
                self._condition.wait()
            self._transaction_active = True

        try:
            deadline = self._monotonic() + budget_s
            outcome = self._controller_factory().set_power(
                False, deadline, cancel_event=None
            )
        except BaseException as error:
            # Publish exceptional completion too, so duplicate handlers cannot hang.
            with self._condition:
                self._shutdown_error = error
                self._transaction_active = False
                self._condition.notify_all()
            raise
        with self._condition:
            self._shutdown_result = outcome
            self._transaction_active = False
            self._condition.notify_all()
        return outcome

    def handle_shutdown(self, budget_s: float = 12.0) -> OperationResult:
        return self.handle_preshutdown(budget_s)

    def _cancelled(self) -> bool:
        with self._condition:
            return (self._stop_requested or self._shutdown_requested
                    or self._stop_event.is_set())

    @staticmethod
    def _result(status: OperationStatus) -> OperationResult:
        return OperationResult(status, 0, None, None, "")
