"""Service tests isolate the SCM and HID boundaries; lifecycle coordination is real."""

import importlib
import logging
from logging.handlers import RotatingFileHandler
from threading import Event, Thread
from time import monotonic

import pytest

from halo_soundbar_service.controller import OperationResult, OperationStatus
from halo_soundbar_service.lifecycle import ServiceLifecycle


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def adapter():
    module = importlib.import_module("halo_soundbar_service.service")
    service = module.HaloSoundbarLightService.__new__(module.HaloSoundbarLightService)
    service._stop_event = Event()
    service._cancel_event = Event()
    service._logger = logging.Logger("service-test", logging.INFO)
    records = Records()
    service._logger.addHandler(records)
    statuses, powers = [], []
    service.ReportServiceStatus = lambda state, **kw: statuses.append((state, kw))

    class Controller:
        def set_power(self, enabled, deadline, cancel_event=None):
            powers.append(enabled)
            assert statuses[-1] == (module.win32service.SERVICE_STOP_PENDING,
                                    {"waitHint": 15000})
            assert not service._stop_event.is_set()
            return OperationResult(OperationStatus.SUCCESS, 1, None, None, "done")

    service._lifecycle = ServiceLifecycle(
        Controller, service._cancel_event, monotonic, service._cancel_event.wait
    )
    return module, service, statuses, powers, records.records


def test_get_accepted_controls_includes_service_accept_preshutdown(adapter, monkeypatch):
    module, service, *_ = adapter
    monkeypatch.setattr(module.win32serviceutil.ServiceFramework,
                        "GetAcceptedControls", lambda self: 0x0001 | 0x0004 | 0x0040)
    assert service.GetAcceptedControls() == 0x0001 | 0x0004 | 0x0040 | 0x0100


def test_svc_other_ex_routes_control_15_to_preshutdown(adapter):
    _, service, statuses, powers, records = adapter
    service.SvcOtherEx(15, 0, None)
    assert len(statuses) == 1
    assert powers == [False]
    assert service._stop_event.is_set()
    assert "SUCCESS" in records[-1].getMessage()


def test_svc_shutdown_uses_same_off_path(adapter):
    _, service, statuses, powers, records = adapter
    service.SvcShutdown()
    assert len(statuses) == 1
    assert powers == [False]
    assert service._stop_event.is_set()
    assert "SUCCESS" in records[-1].getMessage()


def test_svc_stop_only_requests_stop(adapter):
    module, service, statuses, powers, _ = adapter
    service.SvcStop()
    assert statuses[0][0] == module.win32service.SERVICE_STOP_PENDING
    assert powers == []
    assert service._stop_event.is_set()
    assert service._lifecycle.run_startup().status is OperationStatus.CANCELLED


def test_repeated_shutdown_notifications_do_not_repeat_off(adapter):
    _, service, statuses, powers, _ = adapter
    service.SvcOtherEx(15, 0, None)
    service.SvcShutdown()
    service.SvcOtherEx(15, 0, None)
    assert powers == [False]
    assert len(statuses) == 3


def test_unknown_control_is_forwarded_to_framework(adapter, monkeypatch):
    module, service, statuses, powers, _ = adapter
    received = []
    monkeypatch.setattr(module.win32serviceutil.ServiceFramework, "SvcOtherEx",
                        lambda self, *args: received.append(args))
    service.SvcOtherEx(128, 7, b"custom")
    assert received == [(128, 7, b"custom")]
    assert powers == statuses == []
    assert not service._stop_event.is_set()


def test_service_keeps_running_until_shutdown_off_finishes(adapter, monkeypatch):
    module, service, _, _, records = adapter
    started, off_started, release_off, exited = Event(), Event(), Event(), Event()
    errors = []

    class Controller:
        def set_power(self, enabled, deadline, cancel_event=None):
            if enabled:
                started.set()
                assert cancel_event.wait(2)
                return OperationResult(OperationStatus.CANCELLED, 0, None, None, "")
            off_started.set()
            assert release_off.wait(2)
            return OperationResult(OperationStatus.SUCCESS, 1, None, None, "")

    service._lifecycle = ServiceLifecycle(
        Controller, service._cancel_event, monotonic, service._cancel_event.wait
    )

    def run(action):
        try:
            action()
        except BaseException as error:
            errors.append(error)

    def main():
        service.SvcDoRun()
        exited.set()

    main_thread = Thread(target=lambda: run(main), daemon=True)
    shutdown_thread = Thread(target=lambda: run(service.SvcShutdown), daemon=True)
    try:
        main_thread.start()
        assert started.wait(2)
        shutdown_thread.start()
        assert off_started.wait(2)
        assert service._cancel_event.is_set()
        assert not service._stop_event.is_set()
        assert not exited.is_set()
    finally:
        release_off.set()
        service._cancel_event.set()
        main_thread.join(3)
        if shutdown_thread.ident is not None:
            shutdown_thread.join(3)
    assert errors == []
    assert exited.is_set()
    assert any("CANCELLED" in record.getMessage() for record in records)
    assert any("SUCCESS" in record.getMessage() for record in records)


def test_startup_worker_is_daemon_and_join_is_bounded(adapter, monkeypatch):
    module, service, *_ = adapter
    calls = []

    class Worker:
        def __init__(self, *, target, daemon, name):
            assert daemon is True
            self.target = target

        def start(self):
            calls.append("start")
            service.SvcStop()

        def join(self, timeout=None):
            assert timeout is not None and 0 < timeout <= 5
            calls.append("join")

        def is_alive(self):
            return True

    monkeypatch.setattr(module.threading, "Thread", Worker)
    service.SvcDoRun()
    assert calls == ["start", "join"]


@pytest.mark.parametrize("method", ["SvcShutdown", "_run_startup"])
def test_lifecycle_exception_is_logged_without_breaking_service_control(adapter, method):
    _, service, _, _, records = adapter

    def fail():
        raise RuntimeError("controller failed")

    service._lifecycle = ServiceLifecycle(
        fail, service._cancel_event, monotonic, service._cancel_event.wait
    )
    getattr(service, method)()
    assert records[-1].levelno == logging.ERROR
    assert records[-1].exc_info[1].args == ("controller failed",)
    assert service._stop_event.is_set() == (method == "SvcShutdown")


def test_build_controller_defers_hid_until_operation(adapter, monkeypatch):
    module, *_ = adapter
    from halo_soundbar_service.device import DeviceNotFoundError

    calls = []

    def connect(backend, clock):
        assert isinstance(backend, module.HidApiBackend)
        assert clock is module.time.monotonic
        calls.append("connect")
        raise DeviceNotFoundError("test device absent")

    monkeypatch.setattr(module.DeviceClient, "connect", connect)
    controller = module.build_controller()
    assert isinstance(controller, module.LightController)
    assert calls == []
    outcome = controller.set_power(True, monotonic() + 5)
    assert outcome.status is OperationStatus.DEVICE_UNAVAILABLE
    assert calls == ["connect"]


@pytest.fixture
def log_setup(monkeypatch):
    module = importlib.import_module("halo_soundbar_service.logging_setup")
    events = []
    for method, level in (("LogInfoMsg", logging.INFO), ("LogWarningMsg", logging.WARNING),
                          ("LogErrorMsg", logging.ERROR)):
        monkeypatch.setattr(module.servicemanager, method,
                            lambda message, level=level: events.append((level, message)))
    logger = logging.getLogger("HaloSoundbarLightService")
    original = logger.handlers[:]
    logger.handlers.clear()
    yield module, events
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.handlers[:] = original


def test_logging_uses_programdata_utf8_rotating_file_1mib_three_backups(log_setup, tmp_path):
    module, events = log_setup
    logger = module.configure_logging(tmp_path)
    logger.info("启动完成")
    logger.error("关灯失败")

    path = tmp_path / "HaloSoundbarLightService" / "service.log"
    assert logger.name == "HaloSoundbarLightService"
    assert "启动完成" in path.read_text(encoding="utf-8")
    assert "关灯失败" in path.read_text(encoding="utf-8")
    handler, = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert handler.maxBytes == 1 * 1024 * 1024
    assert handler.backupCount == 3
    assert handler.encoding == "utf-8"
    assert [(level, "启动完成" in message, "关灯失败" in message)
            for level, message in events] == [(logging.INFO, True, False),
                                              (logging.ERROR, False, True)]


@pytest.mark.parametrize("failure", ["directory", "file"])
def test_file_log_failure_keeps_event_log_available(log_setup, tmp_path, failure):
    module, events = log_setup
    directory = tmp_path / "HaloSoundbarLightService"
    if failure == "directory":
        directory.write_text("not a directory", encoding="utf-8")
    else:
        (directory / "service.log").mkdir(parents=True)

    logger = module.configure_logging(tmp_path)
    logger.error("off unavailable")
    assert not any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
    assert events[-1][0] == logging.ERROR
    assert "off unavailable" in events[-1][1]
    assert len(events) <= 2  # At most one initialization diagnostic; no recursion.


def test_reconfiguring_logger_does_not_duplicate_handlers_or_records(log_setup, tmp_path):
    module, events = log_setup
    first = module.configure_logging(tmp_path)
    second = module.configure_logging(tmp_path)
    assert first is second
    assert len(second.handlers) == 2
    second.info("one-record")
    assert len(events) == 1
    path = tmp_path / "HaloSoundbarLightService" / "service.log"
    assert path.read_text(encoding="utf-8").count("one-record") == 1


def test_default_log_directory_comes_from_programdata(log_setup, tmp_path, monkeypatch):
    module, _ = log_setup
    monkeypatch.setenv("ProgramData", str(tmp_path))
    module.configure_logging().info("default path")
    assert (tmp_path / "HaloSoundbarLightService" / "service.log").is_file()


def test_event_log_failure_does_not_prevent_file_log(log_setup, tmp_path, monkeypatch):
    module, _ = log_setup

    def fail(message):
        raise OSError("event log unavailable")

    monkeypatch.setattr(module.servicemanager, "LogErrorMsg", fail)
    logger = module.configure_logging(tmp_path)
    logger.error("still written")
    path = tmp_path / "HaloSoundbarLightService" / "service.log"
    assert "still written" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("outcome,expected_level", [
    (OperationStatus.SUCCESS, logging.INFO),
    (OperationStatus.VERIFICATION_FAILED, logging.ERROR),
])
def test_service_initializes_without_scm_or_hid_and_logs_outcomes_to_both_channels(
    log_setup, tmp_path, monkeypatch, outcome, expected_level
):
    log_module, events = log_setup
    module = importlib.import_module("halo_soundbar_service.service")
    monkeypatch.setenv("ProgramData", str(tmp_path))
    monkeypatch.setattr(module.win32serviceutil.ServiceFramework, "__init__",
                        lambda self, args: None)
    powers = []

    class Controller:
        def set_power(self, enabled, deadline, cancel_event=None):
            powers.append(enabled)
            return OperationResult(outcome, 2, None, None, "test detail")

    monkeypatch.setattr(module, "build_controller", Controller)
    service = module.HaloSoundbarLightService(["HaloSoundbarLightService"])
    assert powers == []
    assert service._stop_event is not service._cancel_event
    service.ReportServiceStatus = lambda *args, **kwargs: None
    service._run_startup()
    service.SvcShutdown()

    assert powers == [True, False]
    assert [level for level, _ in events] == [expected_level, expected_level]
    assert all(outcome.name in message and "test detail" in message for _, message in events)
    content = (tmp_path / "HaloSoundbarLightService" / "service.log").read_text("utf-8")
    assert "startup" in content and "shutdown" in content
    assert content.count(outcome.name) == 2
