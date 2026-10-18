"""Strict discovery and bounded sessions for the Halo HID interface."""

from collections.abc import Callable
import importlib
from typing import Protocol

from .protocol import (
    REPORT_SIZE,
    AmbientState,
    AmbientStateCollector,
    PowerTarget,
    ProtocolError,
    build_power_packet,
    build_query_packet,
    parse_frame,
)


class DeviceNotFoundError(RuntimeError):
    """No usable Halo device could be found."""


class AmbiguousDeviceError(RuntimeError):
    """Multiple identity matches answered the ambient state probe."""


class DeviceProtocolError(RuntimeError):
    """The device did not provide a valid protocol exchange."""


class ReadTimeoutError(TimeoutError):
    """No complete state arrived before the read deadline."""


class HidHandle(Protocol):
    def read(self, size: int, timeout_ms: int) -> bytes: ...
    def write(self, report: bytes) -> int: ...
    def close(self) -> None: ...


class HidBackend(Protocol):
    def enumerate(self, vid: int, pid: int) -> list[dict]: ...
    def open_path(self, path: bytes) -> HidHandle: ...


class _HidApiHandle:
    """Normalize hidapi's list reads to immutable reports."""

    def __init__(self, native):
        self._native = native

    def read(self, size: int, timeout_ms: int) -> bytes:
        return bytes(self._native.read(size, timeout_ms))

    def write(self, report: bytes) -> int:
        return self._native.write(report)

    def close(self) -> None:
        self._native.close()


class HidApiBackend:
    """Minimal production backend; importing this module does not require hidapi."""

    def enumerate(self, vid: int, pid: int) -> list[dict]:
        return importlib.import_module("hid").enumerate(vid, pid)

    def open_path(self, path: bytes) -> HidHandle:
        native = importlib.import_module("hid").device()
        try:
            native.open_path(path)
        except BaseException:
            native.close()
            raise
        return _HidApiHandle(native)


_IDENTITY = {
    "vendor_id": 0x2D99,
    "product_id": 0xA073,
    "interface_number": 4,
    "usage_page": 0xFF14,
    "usage": 1,
    "product_string": "EDIFIER Halo Soundbar Max",
}


class DeviceClient:
    def __init__(self, handle: HidHandle, monotonic: Callable[[], float]):
        self._handle = handle
        self._monotonic = monotonic
        self._closed = False

    @classmethod
    def connect(cls, backend: HidBackend, monotonic: Callable[[], float]) -> "DeviceClient":
        opened: list[DeviceClient] = []
        valid: list[DeviceClient] = []
        selected: DeviceClient | None = None
        last_error: Exception | None = None
        try:
            for item in backend.enumerate(0x2D99, 0xA073):
                if not all(item.get(key) == value for key, value in _IDENTITY.items()):
                    continue
                try:
                    client = cls(backend.open_path(item["path"]), monotonic)
                    opened.append(client)
                    client.query_state()
                except (OSError, DeviceProtocolError, ReadTimeoutError) as error:
                    last_error = error
                    continue
                valid.append(client)
            if not valid:
                raise DeviceNotFoundError("No matching Halo interface passed the state probe") from last_error
            if len(valid) > 1:
                raise AmbiguousDeviceError("Multiple Halo interfaces passed the state probe")
            selected = valid[0]
            return selected
        finally:
            for client in opened:
                if client is not selected:
                    try:
                        client.close()
                    except OSError:
                        # A disconnected HID handle must not prevent the remaining cleanup.
                        pass

    def __enter__(self) -> "DeviceClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._handle.close()

    def drain(self, max_reads: int = 64, read_timeout_ms: int = 10) -> int:
        """Discard at most max_reads queued reports, stopping at the first empty read."""
        self._require_open()
        if type(max_reads) is not int or max_reads < 0:
            raise ValueError("max_reads must be a nonnegative integer")
        if type(read_timeout_ms) is not int or read_timeout_ms <= 0:
            raise ValueError("read_timeout_ms must be a positive integer")
        for count in range(max_reads):
            if not self._handle.read(REPORT_SIZE, read_timeout_ms):
                return count
        return max_reads

    def query_state(self, timeout_ms: int = 800) -> AmbientState:
        self._require_open()
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        if self.drain() == 64:
            raise DeviceProtocolError("drain limit reached before an empty read")
        self._write_packet(build_query_packet())
        collector = AmbientStateCollector()
        deadline = self._monotonic() + timeout_ms / 1000
        last_error: ProtocolError | None = None
        while (remaining := deadline - self._monotonic()) > 0:
            report = self._handle.read(REPORT_SIZE, max(1, min(50, int(remaining * 1000))))
            if self._monotonic() >= deadline:
                break
            if not report:
                continue
            try:
                state = collector.feed(parse_frame(report))
            except ProtocolError as error:
                last_error = error
                continue
            if state is not None:
                return state
        if last_error is not None:
            raise DeviceProtocolError(f"No valid state before deadline: {last_error}") from last_error
        raise ReadTimeoutError("No complete ambient state before deadline")

    def write_power(self, target: PowerTarget, enabled: bool) -> None:
        self._write_packet(build_power_packet(target, enabled))

    def _write_packet(self, packet: bytes) -> None:
        self._require_open()
        if self._handle.write(packet) != REPORT_SIZE:
            raise DeviceProtocolError("HID write did not send exactly 64 bytes")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Device session is closed")
