"""SCM tests record native call arguments without opening real SCM handles."""

from collections import deque
import importlib

import pytest
import win32service

from fakes import FakeClock


class ScmError(OSError):
    def __init__(self, code):
        super().__init__(f"SCM error {code}")
        self.winerror = code


class FakeSCM:
    def __init__(self, fail_at=None, error=None, states=(4, 3, 1)):
        self.calls = []
        self.fail_at = fail_at
        self.error = error or ScmError(5)
        self.states = deque(states)

    def record(self, name, *args):
        self.calls.append((name, *args))
        if name == self.fail_at:
            raise self.error

    def OpenSCManager(self, machine, database, access):
        self.record("open_manager", machine, database, access)
        return "manager"

    def CreateService(self, *args):
        self.record("create", *args)
        return "service"

    def OpenService(self, manager, name, access):
        self.record("open_service", manager, name, access)
        return "service"

    def ChangeServiceConfig2(self, service, level, value):
        self.record(f"configure_{level}", service, value)

    def QueryServiceStatus(self, service):
        self.record("query", service)
        state = self.states.popleft() if len(self.states) > 1 else self.states[0]
        return (16, state, 0, 0, 0, 0, 0)

    def ControlService(self, service, control):
        self.record("control", service, control)

    def DeleteService(self, service):
        self.record("delete", service)

    def CloseServiceHandle(self, handle):
        self.record("close", handle)


@pytest.fixture
def installer():
    return importlib.import_module("halo_soundbar_service.installer")


def test_install_registers_localsystem_automatic_service_and_configures_15000ms_preshutdown(installer, tmp_path):
    api = FakeSCM()
    executable = tmp_path / "with spaces" / "Halo.exe"
    installer.configure_installed_service(api, executable)
    create, = [call for call in api.calls if call[0] == "create"]
    assert create[1:] == (
        "manager", "HaloSoundbarLightService", "EDIFIER Halo SoundBar Light Service",
        win32service.SERVICE_ALL_ACCESS, 16, 2, 1,
        f'"{executable.resolve()}"', None, 0, None, None, None,
    )
    assert ("configure_7", "service", 15000) in api.calls
    assert api.calls[-2:] == [("close", "service"), ("close", "manager")]


def test_install_sets_description_and_restart_failure_actions(installer, tmp_path):
    api = FakeSCM()
    installer.configure_installed_service(api, tmp_path / "Halo.exe")
    description, = [call[2] for call in api.calls if call[0] == "configure_1"]
    assert "ambient lights" in description.lower()
    assert ("configure_2", "service", {
        "ResetPeriod": 86400, "RebootMsg": None, "Command": None,
        "Actions": [(1, 60000), (1, 60000), (1, 60000)],
    }) in api.calls


@pytest.mark.parametrize("stage", ["configure_1", "configure_2", "configure_7"])
def test_partial_install_rolls_back_and_closes_both_handles(installer, tmp_path, stage):
    api = FakeSCM(fail_at=stage)
    with pytest.raises(ScmError):
        installer.configure_installed_service(api, tmp_path / "Halo.exe")
    assert api.calls[-3:] == [("delete", "service"), ("close", "service"), ("close", "manager")]


def test_failed_create_does_not_delete_existing_service(installer, tmp_path):
    api = FakeSCM(fail_at="create", error=ScmError(1073))
    with pytest.raises(ScmError):
        installer.configure_installed_service(api, tmp_path / "Halo.exe")
    assert api.calls[-1] == ("close", "manager")
    assert not any(call[0] == "delete" for call in api.calls)


def test_install_service_uses_native_api_adapter(installer, tmp_path, monkeypatch):
    api = FakeSCM()
    monkeypatch.setattr(installer, "win32service", api)
    installer.install_service(tmp_path / "Halo.exe")
    assert any(call[0] == "create" for call in api.calls)


def test_uninstall_stops_then_removes_service_but_keeps_logs(installer, tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path))
    directory = tmp_path / "HaloSoundbarLightService"
    directory.mkdir()
    log = directory / "service.log"
    log.write_text("preserved", encoding="utf-8")
    api, clock = FakeSCM(), FakeClock()
    installer.uninstall_service(api=api, monotonic=clock, sleep=clock.advance)
    assert api.calls[1][0:3] == ("open_service", "manager", "HaloSoundbarLightService")
    names = [call[0] for call in api.calls]
    assert names.index("control") < names.index("delete")
    assert ("control", "service", 1) in api.calls
    assert names.count("query") >= 3
    assert api.calls[-2:] == [("close", "service"), ("close", "manager")]
    assert log.read_text("utf-8") == "preserved"


@pytest.mark.parametrize("states,error", [((1,), None), ((4, 1), ScmError(1062)), ((3, 1), None)])
def test_uninstall_accepts_stopped_and_stop_pending_services(installer, states, error):
    api = FakeSCM(states=states, fail_at="control" if error else None, error=error)
    clock = FakeClock()
    installer.uninstall_service(api=api, monotonic=clock, sleep=clock.advance)
    assert ("delete", "service") in api.calls
    if states[0] in (1, 3):
        assert not any(call[0] == "control" for call in api.calls)


def test_uninstall_missing_service_is_idempotent(installer):
    api = FakeSCM(fail_at="open_service", error=ScmError(1060))
    installer.uninstall_service(api=api)
    assert api.calls[-1] == ("close", "manager")
    assert not any(call[0] == "delete" for call in api.calls)


def test_uninstall_stop_timeout_is_bounded_and_does_not_delete(installer):
    api, clock = FakeSCM(states=(4, 3)), FakeClock()
    with pytest.raises(TimeoutError):
        installer.uninstall_service(api=api, monotonic=clock, sleep=clock.advance, timeout_s=1)
    assert clock.now == 1
    assert not any(call[0] == "delete" for call in api.calls)
    assert api.calls[-2:] == [("close", "service"), ("close", "manager")]


@pytest.mark.parametrize("stage", ["open_service", "query", "control", "delete"])
def test_uninstall_other_errors_propagate_and_close_handles(installer, stage):
    api = FakeSCM(fail_at=stage)
    clock = FakeClock()
    with pytest.raises(ScmError):
        installer.uninstall_service(api=api, monotonic=clock, sleep=clock.advance)
    assert api.calls[-1] == ("close", "manager")
    if stage != "open_service":
        assert api.calls[-2] == ("close", "service")


def test_failed_rollback_preserves_original_error_and_closes_handles(installer, tmp_path, monkeypatch, caplog):
    api = FakeSCM(fail_at="configure_2")

    def fail_delete(service):
        raise RuntimeError("rollback failure")

    monkeypatch.setattr(api, "DeleteService", fail_delete)
    with pytest.raises(ScmError):
        installer.configure_installed_service(api, tmp_path / "Halo.exe")
    assert api.calls[-2:] == [("close", "service"), ("close", "manager")]
    assert "rollback failure" in caplog.text
