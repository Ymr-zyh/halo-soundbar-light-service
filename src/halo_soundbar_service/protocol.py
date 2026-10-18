"""Constrained HID frame encoding and generic frame validation."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


REPORT_SIZE = 64
REPORT_ID = 0x2E
ARRAY_INDEX = 0x0C
_HEADER = bytes((0xAA, 0xED))
_FRAME_OVERHEAD = 7  # Report ID, header, command, length, checksum
_MAX_PAYLOAD_SIZE = REPORT_SIZE - _FRAME_OVERHEAD


class ProtocolError(ValueError):
    """Raised when a protocol frame is invalid or cannot be constructed."""


class PowerTarget(Enum):
    MAIN = 0x0B
    SUBWOOFER = 0x0C


@dataclass(frozen=True)
class ParsedFrame:
    command: int
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")


@dataclass(frozen=True)
class LightEntry:
    index: int
    red: int
    green: int
    blue: int
    brightness: int
    speed: int


@dataclass(frozen=True)
class AmbientState:
    array_index: int
    effect_id: int
    entries: Mapping[int, LightEntry]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", MappingProxyType(dict(self.entries)))


def build_frame(command: int, payload: bytes) -> bytes:
    """Build a zero-padded 64-byte report with an 8-bit additive checksum."""
    if isinstance(command, bool) or not isinstance(command, int) or not 0 <= command <= 0xFF:
        raise ProtocolError("command must be an integer from 0 to 255")
    if not isinstance(payload, bytes):
        raise ProtocolError("payload must be bytes")
    if len(payload) > _MAX_PAYLOAD_SIZE:
        raise ProtocolError(f"payload cannot exceed {_MAX_PAYLOAD_SIZE} bytes")

    length = len(payload).to_bytes(2, byteorder="big")
    frame = bytes((REPORT_ID,)) + _HEADER + bytes((command,)) + length + payload
    checksum = sum(frame[1:]) & 0xFF
    frame += bytes((checksum,))
    return frame.ljust(REPORT_SIZE, b"\x00")


def build_query_packet() -> bytes:
    return build_frame(0x6A, b"")


def build_power_packet(target: PowerTarget, enabled: bool) -> bytes:
    if not isinstance(target, PowerTarget):
        raise ProtocolError("target must be a supported PowerTarget")
    if not isinstance(enabled, bool):
        raise ProtocolError("enabled must be bool")

    payload = bytes((ARRAY_INDEX, target.value, 0, 0, int(enabled), 0xFF, 0xFF))
    return build_frame(0x6B, payload)


def parse_frame(report: bytes) -> ParsedFrame:
    if not isinstance(report, bytes):
        raise ProtocolError("report must be bytes")
    if len(report) != REPORT_SIZE:
        raise ProtocolError(f"report must be exactly {REPORT_SIZE} bytes")
    if report[0] != REPORT_ID or report[1:3] != _HEADER:
        raise ProtocolError("invalid report header")

    payload_length = int.from_bytes(report[4:6], byteorder="big")
    if payload_length > _MAX_PAYLOAD_SIZE:
        raise ProtocolError("declared payload length exceeds report capacity")

    checksum_index = 6 + payload_length
    expected_checksum = sum(report[1:checksum_index]) & 0xFF
    if report[checksum_index] != expected_checksum:
        raise ProtocolError("invalid checksum")
    if any(report[checksum_index + 1 :]):
        raise ProtocolError("report padding must be zero")

    return ParsedFrame(command=report[3], payload=report[6:checksum_index])


