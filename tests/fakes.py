"""Deterministic HID boundary doubles; no hidapi dependency required."""

from collections import deque


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeHidHandle:
    def __init__(self, clock=None, reads=(), query_responses=()):
        self.clock = clock or FakeClock()
        self.reads = deque(reads)
        self.query_responses = deque(query_responses)
        self.writes = []
        self.read_calls = []
        self.closed = False
        self.write_result = 64

    def read(self, size, timeout_ms):
        if self.closed:
            raise OSError("handle closed")
        self.read_calls.append((size, timeout_ms))
        self.clock.advance(timeout_ms / 1000)
        result = self.reads.popleft() if self.reads else b""
        if isinstance(result, Exception):
            raise result
        return result

    def write(self, report):
        if self.closed:
            raise OSError("handle closed")
        self.writes.append(report)
        if report[3] == 0x6A and self.query_responses:
            self.reads.extend(self.query_responses.popleft())
        return self.write_result

    def close(self):
        self.closed = True


class FakeHidBackend:
    def __init__(self, devices=(), handles=None):
        self.devices = list(devices)
        self.handles = handles or {}
        self.opened_paths = []
        self.enumerate_calls = []

    def enumerate(self, vid, pid):
        self.enumerate_calls.append((vid, pid))
        return self.devices

    def open_path(self, path):
        self.opened_paths.append(path)
        result = self.handles[path]
        if isinstance(result, Exception):
            raise result
        return result
