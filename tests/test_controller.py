from dataclasses import replace
from threading import Event

import pytest

from fakes import FakeClock, FakeHidHandle
from halo_soundbar_service.controller import LightController, OperationStatus
from halo_soundbar_service.device import (
    AmbiguousDeviceError, DeviceClient, DeviceNotFoundError, DeviceProtocolError,
    ReadTimeoutError,
)
from halo_soundbar_service.protocol import AmbientState, LightEntry, ProtocolError, build_frame


def state(main=1, sub=1, effect=7):
    entries = {i: LightEntry(i, 10, 20, 30, 90, 80) for i in range(1, 11)}
    entries[0x0B] = LightEntry(0x0B, 0, 0, main, 0xFF, 0xFF)
    entries[0x0C] = LightEntry(0x0C, 0, 0, sub, 0xFF, 0xFF)
    return AmbientState(0x0C, effect, entries)


def frames(snapshot):
    return [
        build_frame(
            0xE3,
            bytes((2, part, snapshot.array_index, snapshot.effect_id))
            + b"".join(
                bytes((entry.index, entry.red, entry.green, entry.blue,
                       entry.brightness, entry.speed))
                for index, entry in snapshot.entries.items() if index in indices
            ),
        )
        for part, indices in ((1, range(1, 7)), (2, range(7, 13)))
    ]


def setup_controller(*snapshots):
    clock = FakeClock()
    handle = FakeHidHandle(clock, query_responses=[frames(s) for s in snapshots])
    client = DeviceClient(handle, clock)
    return LightController(lambda: client, clock, clock.advance), handle, clock


def calls(handle):
    return [
        ("query_state",) if packet[3] == 0x6A
        else ("write_power", packet[7], bool(packet[10]))
        for packet in handle.writes
    ]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("main,sub", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_set_power_writes_only_switches_not_already_at_target_in_main_then_sub_order(
    enabled, main, sub
):
    before, after = state(main, sub), state(int(enabled), int(enabled))
    controller, handle, _ = setup_controller(before, after)

    result = controller.set_power(enabled, deadline=10)

    writes = [("write_power", index, enabled) for index, value in ((11, main), (12, sub))
              if bool(value) != enabled]
    assert calls(handle) == [("query_state",)] + writes + ([("query_state",)] if writes else [])
    assert result.status is OperationStatus.SUCCESS
    assert result.attempts == int(bool(writes))
    assert result.before == before
    assert result.after == after
    assert handle.closed


def test_set_power_is_noop_when_both_switches_match():
    snapshot = state(0, 0)
    controller, handle, _ = setup_controller(snapshot)

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.SUCCESS
    assert result.attempts == 0
    assert result.before == result.after == snapshot
    assert calls(handle) == [("query_state",)]
    assert handle.closed


@pytest.mark.parametrize("field,value", [
    ("red", 1), ("green", 1), ("brightness", 0), ("speed", 0), ("blue", 2),
    ("index", 1),
])
@pytest.mark.parametrize("index", [11, 12])
def test_invalid_switch_fields_fail_closed_before_any_write(field, value, index):
    valid = state()
    entries = dict(valid.entries)
    entries[index] = replace(entries[index], **{field: value})
    invalid = replace(valid, entries=entries)
    handle = FakeHidHandle()
    client = DeviceClient(handle, handle.clock)
    client.query_state = lambda: invalid
    controller = LightController(lambda: client, handle.clock, handle.clock.advance)

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.PROTOCOL_ERROR
    assert result.attempts == 0
    assert handle.writes == []
    assert handle.closed


@pytest.mark.parametrize("invalid", [
    replace(state(), array_index=11),
    replace(state(), entries={i: e for i, e in state().entries.items() if i != 12}),
    replace(state(), entries={i: e for i, e in state().entries.items() if i != 1}),
])
def test_wrong_array_or_missing_entries_fail_closed(invalid):
    handle = FakeHidHandle()
    client = DeviceClient(handle, handle.clock)
    client.query_state = lambda: invalid
    controller = LightController(lambda: client, handle.clock, handle.clock.advance)

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.PROTOCOL_ERROR
    assert result.attempts == 0
    assert handle.writes == []


def test_retries_only_remaining_switches_up_to_three_attempts():
    controller, handle, _ = setup_controller(
        state(), state(0, 1), state(0, 1), state(0, 1), state(0, 1), state(0, 0)
    )

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.SUCCESS
    assert result.attempts == 3
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",), ("query_state",), ("write_power", 12, False),
        ("query_state",), ("query_state",), ("write_power", 12, False),
        ("query_state",),
    ]


def test_three_unverified_write_rounds_return_verification_failed():
    controller, handle, _ = setup_controller(*(state() for _ in range(6)))

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.VERIFICATION_FAILED
    assert result.attempts == 3
    assert len([call for call in calls(handle) if call[0] == "write_power"]) == 6
    assert result.before == result.after == state()


def test_retry_queries_current_state_before_deciding_to_write():
    controller, handle, _ = setup_controller(state(), state(), state(0, 0))

    result = controller.set_power(False, deadline=10)

    assert result.status is OperationStatus.SUCCESS
    assert result.attempts == 1
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",), ("query_state",),
    ]


def test_deadline_stops_retry_without_extra_write():
    controller, handle, _ = setup_controller(state(), state())

    result = controller.set_power(False, deadline=0.2)

    assert result.status is OperationStatus.DEADLINE_EXCEEDED
    assert result.attempts == 1
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",),
    ]


@pytest.mark.parametrize("responds", [False, True])
def test_stale_backlog_query_is_bounded_and_never_writes_past_controller_deadline(responds):
    controller, handle, clock = setup_controller(*([state()] if responds else []))
    handle.reads.extend([b"stale"] * 63)

    result = controller.set_power(False, deadline=0.1)

    assert result.status is (OperationStatus.DEADLINE_EXCEEDED if responds
                            else OperationStatus.DEVICE_UNAVAILABLE)
    assert 0.1 < clock.now <= 0.8
    assert result.attempts == 0
    assert calls(handle) == [("query_state",)]
    assert handle.closed


@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_stop_before_start_never_opens_a_device(reason):
    event = Event()
    if reason == "cancel":
        event.set()

    def forbidden_factory():
        pytest.fail("device opened after operation had already stopped")

    controller = LightController(forbidden_factory, lambda: 1.0, lambda seconds: None)
    result = controller.set_power(False, 0 if reason == "deadline" else 10, event)

    assert result.status is (OperationStatus.CANCELLED if reason == "cancel"
                            else OperationStatus.DEADLINE_EXCEEDED)
    assert result.attempts == 0
    assert result.before is result.after is None


def test_cancel_before_write_returns_cancelled(monkeypatch):
    event = Event()
    controller, handle, _ = setup_controller(state(), state(0, 0))
    original_read = handle.read

    def cancel_when_state_arrives(size, timeout_ms):
        report = original_read(size, timeout_ms)
        if report and report[7] == 2:
            event.set()
        return report

    monkeypatch.setattr(handle, "read", cancel_when_state_arrives)
    result = controller.set_power(False, 10, event)

    assert result.status is OperationStatus.CANCELLED
    assert result.attempts == 0
    assert calls(handle) == [("query_state",)]
    assert result.before == result.after == state()


@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_stop_between_switch_writes_does_not_write_subwoofer(monkeypatch, reason):
    event = Event()
    controller, handle, clock = setup_controller(state(), state(0, 0))
    original_write = handle.write

    def stop_after_main(report):
        result = original_write(report)
        if report[3] == 0x6B and report[7] == 11:
            if reason == "cancel":
                event.set()
            else:
                clock.advance(20)
        return result

    monkeypatch.setattr(handle, "write", stop_after_main)
    result = controller.set_power(False, 10, event)

    assert result.status is (OperationStatus.CANCELLED if reason == "cancel"
                            else OperationStatus.DEADLINE_EXCEEDED)
    assert result.attempts == 1
    assert calls(handle) == [("query_state",), ("write_power", 11, False)]
    assert handle.closed


def test_success_requires_style_fingerprint_unchanged():
    before, after = state(), state(0, 0)
    controller, handle, _ = setup_controller(before, after)

    result = controller.set_power(False, 10)

    assert result.status is OperationStatus.SUCCESS
    assert result.before == before
    assert result.after == after
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",),
    ]


@pytest.mark.parametrize("achieved", [False, True])
@pytest.mark.parametrize("field,value", [
    ("effect_id", 9), ("red", 99), ("green", 99), ("blue", 99),
    ("brightness", 99), ("speed", 99),
])
def test_external_style_change_is_reported_without_restore_write(achieved, field, value):
    before = state()
    changed = state(0, 0) if achieved else state()
    if field == "effect_id":
        changed = replace(changed, effect_id=value)
    else:
        entries = dict(changed.entries)
        entries[1] = replace(entries[1], **{field: value})
        changed = replace(changed, entries=entries)
    controller, handle, _ = setup_controller(before, changed)

    result = controller.set_power(False, 10)

    assert result.status is OperationStatus.EXTERNAL_STATE_CHANGED
    assert result.attempts == 1
    assert result.before == before
    assert result.after == changed
    assert ("effect_id" if field == "effect_id" else "0x01") in result.detail
    assert str(value) in result.detail
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",),
    ]


def test_external_style_change_in_retry_query_stops_before_further_write():
    controller, handle, _ = setup_controller(state(), state(), state(effect=9))

    result = controller.set_power(False, 10)

    assert result.status is OperationStatus.EXTERNAL_STATE_CHANGED
    assert result.attempts == 1
    assert calls(handle) == [
        ("query_state",), ("write_power", 11, False), ("write_power", 12, False),
        ("query_state",), ("query_state",),
    ]


@pytest.mark.parametrize("stage", ["factory", "query", "write"])
@pytest.mark.parametrize("error,status", [
    (DeviceNotFoundError("missing"), OperationStatus.DEVICE_UNAVAILABLE),
    (ReadTimeoutError("timeout"), OperationStatus.DEVICE_UNAVAILABLE),
    (OSError("unplugged"), OperationStatus.DEVICE_UNAVAILABLE),
    (AmbiguousDeviceError("multiple"), OperationStatus.PROTOCOL_ERROR),
    (DeviceProtocolError("bad exchange"), OperationStatus.PROTOCOL_ERROR),
    (ProtocolError("bad frame"), OperationStatus.PROTOCOL_ERROR),
])
def test_device_errors_are_mapped_without_additional_writes(stage, error, status, monkeypatch):
    clock = FakeClock()
    handle = FakeHidHandle(clock, query_responses=[frames(state())])
    client = DeviceClient(handle, clock)

    def fail(*args, **kwargs):
        raise error

    if stage == "query":
        monkeypatch.setattr(client, "query_state", fail)
    elif stage == "write":
        original_write = handle.write

        def fail_power(packet):
            if packet[3] == 0x6B:
                raise error
            return original_write(packet)

        monkeypatch.setattr(handle, "write", fail_power)
    factory = fail if stage == "factory" else lambda: client
    controller = LightController(factory, clock, clock.advance)

    result = controller.set_power(False, 10)

    assert result.status is status
    assert result.attempts == int(stage == "write")
    assert str(error) in result.detail
    assert calls(handle) == ([("query_state",)] if stage == "write" else [])
    if stage != "factory":
        assert handle.closed


def test_probe_is_read_only_and_closes_session():
    controller, handle, _ = setup_controller(state())

    assert controller.probe() == state()
    assert calls(handle) == [("query_state",)]
    assert handle.closed


@pytest.mark.parametrize("error", [DeviceNotFoundError("missing"), ProtocolError("bad")])
def test_probe_preserves_device_and_protocol_errors(error):
    def fail():
        raise error

    controller = LightController(fail, FakeClock(), lambda seconds: None)
    with pytest.raises(type(error), match=str(error)):
        controller.probe()


def test_probe_rejects_invalid_switch_snapshot_without_writes():
    controller, handle, _ = setup_controller(state(main=2))

    with pytest.raises(ProtocolError, match="switch"):
        controller.probe()
    assert calls(handle) == [("query_state",)]
    assert handle.closed


@pytest.mark.parametrize("enabled", [0, 1, "off", None])
def test_non_boolean_target_fails_closed_before_device_access(enabled):
    def forbidden_factory():
        pytest.fail("invalid target opened a device")

    controller = LightController(forbidden_factory, FakeClock(), lambda seconds: None)
    result = controller.set_power(enabled, 10)

    assert result.status is OperationStatus.PROTOCOL_ERROR
    assert result.attempts == 0
    assert result.before is result.after is None
