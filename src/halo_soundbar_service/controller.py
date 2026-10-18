"""Verified transactions for the two ambient-light power switches."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from threading import Event

from .device import (
    AmbiguousDeviceError,
    DeviceClient,
    DeviceNotFoundError,
    DeviceProtocolError,
    ReadTimeoutError,
)
from .protocol import AmbientState, LightEntry, PowerTarget, ProtocolError, style_fingerprint


class OperationStatus(Enum):
    SUCCESS = auto()
    CANCELLED = auto()
    DEADLINE_EXCEEDED = auto()
    DEVICE_UNAVAILABLE = auto()
    PROTOCOL_ERROR = auto()
    VERIFICATION_FAILED = auto()
    EXTERNAL_STATE_CHANGED = auto()


@dataclass(frozen=True)
class OperationResult:
    status: OperationStatus
    attempts: int
    before: AmbientState | None
    after: AmbientState | None
    detail: str


class LightController:
    def __init__(
        self,
        client_factory: Callable[[], DeviceClient],
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ):
        self._client_factory = client_factory
        self._monotonic = monotonic
        self._sleep = sleep

    def set_power(
        self, enabled: bool, deadline: float, cancel_event: Event | None = None
    ) -> OperationResult:
        before = after = None
        attempts = 0
        baseline = None

        def result(status: OperationStatus, detail: str = "") -> OperationResult:
            return OperationResult(status, attempts, before, after, detail)

        if not isinstance(enabled, bool):
            return result(OperationStatus.PROTOCOL_ERROR, "enabled must be bool")
        stopped = self._stop_status(deadline, cancel_event)
        if stopped:
            return result(stopped)
        try:
            with self._client_factory() as client:
                while attempts < 3:
                    stopped = self._stop_status(deadline, cancel_event)
                    if stopped:
                        return result(stopped)
                    after = client.query_state()
                    self._validate(after)
                    if before is None:
                        before = after
                        baseline = style_fingerprint(before)
                    else:
                        difference = self._style_difference(baseline, style_fingerprint(after))
                        if difference:
                            return result(OperationStatus.EXTERNAL_STATE_CHANGED, difference)
                    stopped = self._stop_status(deadline, cancel_event)
                    if stopped:
                        return result(stopped)
                    targets = [target for target in PowerTarget
                               if after.entries[target.value].blue != int(enabled)]
                    if not targets:
                        return result(OperationStatus.SUCCESS)
                    for index, target in enumerate(targets):
                        stopped = self._stop_status(deadline, cancel_event)
                        if stopped:
                            return result(stopped)
                        if index == 0:
                            attempts += 1
                        client.write_power(target, enabled)
                    stopped = self._stop_status(deadline, cancel_event)
                    if stopped:
                        return result(stopped)
                    after = client.query_state()
                    self._validate(after)
                    difference = self._style_difference(baseline, style_fingerprint(after))
                    if difference:
                        return result(OperationStatus.EXTERNAL_STATE_CHANGED, difference)
                    stopped = self._stop_status(deadline, cancel_event)
                    if stopped:
                        return result(stopped)
                    if all(after.entries[target.value].blue == int(enabled) for target in PowerTarget):
                        return result(OperationStatus.SUCCESS)
                return result(OperationStatus.VERIFICATION_FAILED,
                              "Switches did not reach the target after three write rounds")
        except (DeviceNotFoundError, ReadTimeoutError, OSError) as error:
            return result(OperationStatus.DEVICE_UNAVAILABLE, str(error))
        except (AmbiguousDeviceError, DeviceProtocolError, ProtocolError) as error:
            return result(OperationStatus.PROTOCOL_ERROR, str(error))

    def probe(self) -> AmbientState:
        """Read and validate one snapshot without changing either switch."""
        with self._client_factory() as client:
            snapshot = client.query_state()
            self._validate(snapshot)
            return snapshot

    @staticmethod
    def _style_difference(baseline, current) -> str:
        differences = []
        if baseline[0] != current[0]:
            differences.append(f"effect_id: {baseline[0]} -> {current[0]}")
        for index, (before, after) in enumerate(zip(baseline[1], current[1]), start=1):
            if before != after:
                differences.append(f"entry {index:#04x}: {before} -> {after}")
        return "; ".join(differences)

    def _stop_status(self, deadline: float, cancel_event: Event | None) -> OperationStatus | None:
        if cancel_event is not None and cancel_event.is_set():
            return OperationStatus.CANCELLED
        if self._monotonic() >= deadline:
            return OperationStatus.DEADLINE_EXCEEDED
        return None

    @staticmethod
    def _validate(snapshot: AmbientState) -> None:
        if not isinstance(snapshot, AmbientState) or snapshot.array_index != 0x0C:
            raise ProtocolError("Expected ambient state array 0x0c")
        try:
            for target in PowerTarget:
                entry = snapshot.entries[target.value]
                if (not isinstance(entry, LightEntry) or entry.index != target.value
                        or (entry.red, entry.green, entry.brightness, entry.speed) != (0, 0, 255, 255)
                        or entry.blue not in (0, 1)):
                    raise ProtocolError(f"Invalid switch entry {target.value:#04x}")
            style_fingerprint(snapshot)
        except (KeyError, AttributeError, TypeError) as error:
            raise ProtocolError(f"Invalid ambient state structure: {error}") from error
