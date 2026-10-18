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


class AmbientStateCollector:
    """Reassemble the two-part ambient state response without mixing generations."""

    def __init__(self, expected_array_index: int = ARRAY_INDEX) -> None:
        self.expected_array_index = expected_array_index
        self.reset()

    def reset(self) -> None:
        self._effect_id: int | None = None
        self._array_index: int | None = None
        self._parts: dict[int, dict[int, LightEntry]] = {}
        self._has_part_one = False

    def feed(self, frame: ParsedFrame) -> AmbientState | None:
        if frame.command != 0xE3:
            return None
        if len(frame.payload) < 4:
            raise ProtocolError("ambient state payload must include its four-byte header")

        total_parts, part_number, array_index, effect_id = frame.payload[:4]
        records = frame.payload[4:]
        if total_parts != 2:
            raise ProtocolError("ambient state must contain exactly two parts")
        if part_number not in (1, 2):
            raise ProtocolError("ambient state part number must be 1 or 2")
        if array_index != self.expected_array_index:
            raise ProtocolError("ambient state array index does not match the expected array")
        if len(records) % 6:
            raise ProtocolError("ambient state entries must be six bytes each")

        entries: dict[int, LightEntry] = {}
        for offset in range(0, len(records), 6):
            index, red, green, blue, brightness, speed = records[offset : offset + 6]
            if index in entries:
                raise ProtocolError(f"duplicate ambient state entry index {index:#04x}")
            entries[index] = LightEntry(index, red, green, blue, brightness, speed)

        if part_number == 1:
            if self._has_part_one:
                self.reset()
            elif self._effect_id is not None and self._effect_id != effect_id:
                self.reset()
            self._effect_id = effect_id
            self._array_index = array_index
            self._has_part_one = True
        elif self._effect_id is None:
            self._effect_id = effect_id
            self._array_index = array_index
        elif self._effect_id != effect_id or self._array_index != array_index:
            return None

        existing_indices = {
            index for part_entries in self._parts.values() for index in part_entries
        }
        if existing_indices.intersection(entries):
            raise ProtocolError("duplicate ambient state entry index across parts")
        self._parts[part_number] = entries

        if not self._has_part_one or set(self._parts) != {1, 2}:
            return None

        combined = {**self._parts[1], **self._parts[2]}
        state = AmbientState(
            array_index=self._array_index,
            effect_id=self._effect_id,
            entries=combined,
        )
        self.reset()
        return state


def style_fingerprint(state: AmbientState) -> tuple[int, tuple[LightEntry, ...]]:
    """Return the style identity, excluding the two switch-only array entries."""
    return state.effect_id, tuple(state.entries[index] for index in range(0x01, 0x0B))


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


