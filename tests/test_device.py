import pytest

from fakes import FakeClock, FakeHidBackend, FakeHidHandle
from halo_soundbar_service.device import DeviceClient, DeviceNotFoundError
from halo_soundbar_service.protocol import ProtocolError, PowerTarget, build_frame


def _identity(path=b"halo", **overrides):
    return {
        "path": path,
        "vendor_id": 0x2D99,
        "product_id": 0xA073,
        "interface_number": 4,
        "usage_page": 0xFF14,
        "usage": 1,
        "product_string": "EDIFIER Halo Soundbar Max",
        **overrides,
    }


def test_connect_ignores_wrong_pid_interface_usage_and_product():
    incorrect = {
        "vendor_id": 0x2D98,
        "product_id": 0xA074,
        "interface_number": 3,
        "usage_page": 0xFF13,
        "usage": 2,
        "product_string": "EDIFIER Halo Soundbar",
    }
    backend = FakeHidBackend(
        [_identity(**{field: value}) for field, value in incorrect.items()]
    )

    with pytest.raises(DeviceNotFoundError):
        DeviceClient.connect(backend, FakeClock())

    assert backend.opened_paths == []
    assert backend.enumerate_calls == [(0x2D99, 0xA073)]


def _state_frames(effect=7):
    return [
        build_frame(
            0xE3,
            bytes((2, part, 0x0C, effect))
            + b"".join(bytes((index, 1, 2, 3, 0xFF, 0xFF)) for index in indices),
        )
        for part, indices in ((1, range(1, 7)), (2, range(7, 13)))
    ]


@pytest.fixture
def session():
    clock = FakeClock()
    handle = FakeHidHandle(clock)
    client = DeviceClient(handle, clock)
    yield client, handle, clock
    client.close()
    assert handle.closed


def test_query_drains_ack_and_stale_packets_then_collects_current_e3_pair(session):
    client, handle, _ = session
    handle.reads.extend([build_frame(0x6B, b""), *_state_frames(2)])
    handle.query_responses.append([build_frame(0x6A, b""), *_state_frames(9)[::-1]])

    state = client.query_state()

    assert state.effect_id == 9
    assert set(state.entries) == set(range(1, 13))
    assert len(handle.writes) == 1
    assert handle.writes[0] == bytes.fromhex("2EAAED6A000001") + bytes(57)


def test_query_times_out_without_complete_pair(session):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, clock = session
    handle.query_responses.append(_state_frames()[:1])

    with pytest.raises(ReadTimeoutError):
        client.query_state(timeout_ms=80)

    assert 0.08 <= clock.now <= 0.091
    assert all(0 < timeout <= 80 for _, timeout in handle.read_calls)


def test_query_default_budget_includes_draining_63_stale_packets(session):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, clock = session
    handle.reads.extend([b"stale"] * 63)

    with pytest.raises(ReadTimeoutError):
        client.query_state()

    assert clock.now <= 0.8
    assert len(handle.writes) == 1


def test_query_drain_exhausts_shared_budget_without_sending_query(session):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, clock = session
    handle.reads.extend([b"stale"] * 63)

    with pytest.raises(ReadTimeoutError):
        client.query_state(timeout_ms=25)

    assert clock.now <= 0.025
    assert len(handle.read_calls) <= 3
    assert handle.writes == []


def test_query_does_not_send_query_after_drain_read_returns_late(session, monkeypatch):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, clock = session
    original_read = handle.read

    def late_empty_read(size, timeout_ms):
        result = original_read(size, timeout_ms)
        clock.advance(0.1)
        return result

    monkeypatch.setattr(handle, "read", late_empty_read)

    with pytest.raises(ReadTimeoutError):
        client.query_state(timeout_ms=80)

    assert len(handle.read_calls) == 1
    assert handle.writes == []


def test_each_query_has_a_new_collector(session):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, _ = session
    first, second = _state_frames()
    handle.query_responses.extend([[first], [second]])

    for _ in range(2):
        with pytest.raises(ReadTimeoutError):
            client.query_state(timeout_ms=80)

    assert len(handle.writes) == 2


def test_drain_stops_at_max_reads(session):
    client, handle, _ = session
    handle.reads.extend([b"stale"] * 5)

    assert client.drain(max_reads=3, read_timeout_ms=4) == 3
    assert len(handle.reads) == 2
    assert handle.read_calls == [(64, 4)] * 3


def test_query_rejects_a_drain_that_never_reaches_empty(session):
    from halo_soundbar_service.device import DeviceProtocolError

    client, handle, _ = session
    handle.reads.extend([b"stale"] * 65)

    with pytest.raises(DeviceProtocolError, match="drain"):
        client.query_state()

    assert handle.writes == []


def test_query_keeps_malformed_frame_reason_when_deadline_expires(session):
    from halo_soundbar_service.device import DeviceProtocolError

    client, handle, _ = session
    handle.query_responses.append([b"bad"])

    with pytest.raises(DeviceProtocolError, match="exactly 64") as caught:
        client.query_state(timeout_ms=80)

    assert isinstance(caught.value.__cause__, ProtocolError)


def test_query_recovers_after_malformed_frame(session):
    client, handle, _ = session
    handle.query_responses.append([b"bad", *_state_frames(3)])

    assert client.query_state().effect_id == 3


def test_context_manager_closes_handle_after_exception():
    handle = FakeHidHandle()

    with pytest.raises(RuntimeError, match="consumer failure"):
        with DeviceClient(handle, handle.clock) as client:
            assert client is not None
            raise RuntimeError("consumer failure")

    assert handle.closed


def test_write_power_accepts_only_power_target_enum_and_writes_exactly_64_bytes(session):
    client, handle, _ = session

    client.write_power(PowerTarget.MAIN, False)
    client.write_power(PowerTarget.SUBWOOFER, True)

    assert handle.writes == [
        bytes.fromhex("2EAAED6B00070C0B000000FFFF1E") + bytes(50),
        bytes.fromhex("2EAAED6B00070C0C000001FFFF20") + bytes(50),
    ]
    for target in (0x0B, "MAIN", True, None):
        with pytest.raises(ProtocolError):
            client.write_power(target, False)
    with pytest.raises(ProtocolError):
        client.write_power(PowerTarget.MAIN, 1)
    assert len(handle.writes) == 2
    assert not any(hasattr(client, name) for name in ("write", "send", "write_raw", "write_command"))


def test_short_write_is_a_device_protocol_error(session):
    from halo_soundbar_service.device import DeviceProtocolError

    client, handle, _ = session
    handle.write_result = 63

    with pytest.raises(DeviceProtocolError, match="64"):
        client.write_power(PowerTarget.MAIN, False)


def test_connect_rejects_two_identity_matches_that_both_probe_successfully():
    from halo_soundbar_service.device import AmbiguousDeviceError

    clock = FakeClock()
    handles = {
        path: FakeHidHandle(clock, query_responses=[_state_frames()])
        for path in (b"first", b"second")
    }
    backend = FakeHidBackend([_identity(path) for path in handles], handles)

    with pytest.raises(AmbiguousDeviceError):
        DeviceClient.connect(backend, clock)

    assert all(handle.closed for handle in handles.values())
    assert all(len(handle.writes) == 1 for handle in handles.values())


def test_connect_selects_only_candidate_with_valid_protocol_state():
    clock = FakeClock()
    invalid = FakeHidHandle(clock, query_responses=[[b"bad"]])
    valid = FakeHidHandle(clock, query_responses=[_state_frames(4)])
    backend = FakeHidBackend(
        [_identity(b"invalid"), _identity(b"valid")],
        {b"invalid": invalid, b"valid": valid},
    )

    with DeviceClient.connect(backend, clock) as client:
        assert invalid.closed
        assert not valid.closed
        client.write_power(PowerTarget.MAIN, False)
        assert valid.writes[-1][3] == 0x6B

    assert valid.closed


def test_connect_closes_single_candidate_that_cannot_provide_complete_state():
    clock = FakeClock()
    handle = FakeHidHandle(clock, query_responses=[_state_frames()[:1]])
    backend = FakeHidBackend([_identity()], {b"halo": handle})

    with pytest.raises(DeviceNotFoundError):
        DeviceClient.connect(backend, clock)

    assert handle.closed


def test_connect_continues_after_open_or_read_failure():
    clock = FakeClock()
    broken = FakeHidHandle(clock, reads=[OSError("disconnected")])
    valid = FakeHidHandle(clock, query_responses=[_state_frames()])
    backend = FakeHidBackend(
        [_identity(b"denied"), _identity(b"broken"), _identity(b"valid")],
        {b"denied": OSError("access denied"), b"broken": broken, b"valid": valid},
    )

    with DeviceClient.connect(backend, clock):
        assert broken.closed

    assert valid.closed


def test_connect_closes_previous_candidate_when_enumeration_processing_raises():
    clock = FakeClock()
    handle = FakeHidHandle(clock, query_responses=[_state_frames()])
    backend = FakeHidBackend([_identity(), None], {b"halo": handle})

    with pytest.raises(AttributeError):
        DeviceClient.connect(backend, clock)

    assert handle.closed


def test_hidapi_backend_is_lazy_and_maps_native_io(monkeypatch):
    import importlib
    import sys
    from types import SimpleNamespace
    import halo_soundbar_service.device as device

    imports = []
    real_import = importlib.import_module
    native_clock = FakeClock()
    native = FakeHidHandle(native_clock)
    native.reads.append([0x2E, 0xAA, 0xED])
    opened = []
    native.open_path = opened.append
    enumerate_calls = []

    def enumerate_devices(vid, pid):
        enumerate_calls.append((vid, pid))
        return [_identity()]

    hid_module = SimpleNamespace(enumerate=enumerate_devices, device=lambda: native)

    def import_module(name, package=None):
        if name == "hid":
            imports.append(name)
            return hid_module
        return real_import(name, package)

    monkeypatch.delitem(sys.modules, "hid", raising=False)
    monkeypatch.setattr(importlib, "import_module", import_module)
    backend = device.HidApiBackend()
    assert imports == []

    assert backend.enumerate(0x2D99, 0xA073) == [_identity()]
    handle = backend.open_path(b"halo")
    try:
        assert handle.read(64, 23) == b"\x2e\xaa\xed"
        assert handle.write(bytes(64)) == 64
    finally:
        handle.close()

    assert imports
    assert opened == [b"halo"]
    assert enumerate_calls == [(0x2D99, 0xA073)]
    assert native.read_calls == [(64, 23)]
    assert native.writes == [bytes(64)]
    assert native.closed


def test_hidapi_backend_closes_native_handle_if_open_fails(monkeypatch):
    import importlib
    from types import SimpleNamespace
    from halo_soundbar_service.device import HidApiBackend

    native = FakeHidHandle()

    def fail_open(path):
        raise OSError("access denied")

    native.open_path = fail_open
    monkeypatch.setattr(
        importlib, "import_module", lambda name: SimpleNamespace(device=lambda: native)
    )

    with pytest.raises(OSError, match="access denied"):
        HidApiBackend().open_path(b"halo")

    assert native.closed


def test_query_does_not_accept_a_complete_state_after_deadline(session, monkeypatch):
    from halo_soundbar_service.device import ReadTimeoutError

    client, handle, clock = session
    handle.query_responses.append(_state_frames())
    native_read = handle.read

    def delayed_second_part(size, timeout_ms):
        report = native_read(size, timeout_ms)
        if report and report[7] == 2:
            clock.advance(0.1)  # Simulate a driver returning past its requested timeout.
        return report

    monkeypatch.setattr(handle, "read", delayed_second_part)

    with pytest.raises(ReadTimeoutError):
        client.query_state(timeout_ms=70)


@pytest.mark.parametrize("timeout", [0, -1, True, 1.5])
def test_query_rejects_invalid_timeouts_before_io(session, timeout):
    client, handle, _ = session

    with pytest.raises(ValueError):
        client.query_state(timeout_ms=timeout)

    assert handle.read_calls == []
    assert handle.writes == []


@pytest.mark.parametrize(
    "kwargs",
    [{"max_reads": -1}, {"max_reads": True}, {"read_timeout_ms": -1}, {"read_timeout_ms": 0}],
)
def test_drain_rejects_invalid_bounds_before_io(session, kwargs):
    client, handle, _ = session

    with pytest.raises(ValueError):
        client.drain(**kwargs)

    assert handle.read_calls == []


def test_closed_session_rejects_io_and_close_is_idempotent(session):
    client, handle, _ = session
    client.close()
    client.close()

    for operation in (
        client.drain,
        client.query_state,
        lambda: client.write_power(PowerTarget.MAIN, False),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()

    assert handle.read_calls == []
    assert handle.writes == []
