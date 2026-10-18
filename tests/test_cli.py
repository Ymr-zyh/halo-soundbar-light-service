"""Exercise command outcomes with isolated HID, logging and service boundaries."""

import importlib
import logging
from pathlib import Path
import runpy
import sys

import pytest

from halo_soundbar_service.controller import OperationResult, OperationStatus
from halo_soundbar_service.device import DeviceNotFoundError
from halo_soundbar_service.protocol import AmbientState, LightEntry, ProtocolError


@pytest.fixture
def cli(monkeypatch):
    module = importlib.import_module("halo_soundbar_service.cli")
    monkeypatch.setattr(module, "configure_logging", lambda: logging.getLogger("cli-test"))
    return module


def test_probe_prints_identity_effect_and_two_switch_states_without_writes(cli, monkeypatch, capsys):
    class Controller:
        def probe(self):
            return AmbientState(12, 7, {
                11: LightEntry(11, 0, 0, 1, 255, 255),
                12: LightEntry(12, 0, 0, 0, 255, 255),
            })

        def set_power(self, *args, **kwargs):
            pytest.fail("probe must not change power")

    monkeypatch.setattr(cli, "build_controller", Controller)
    assert cli.main(["probe"]) == 0
    output = capsys.readouterr().out
    for field in ("VID=0x2D99", "PID=0xA073", "interface=4", "usage_page=0xFF14",
                  "usage=0x0001", "EDIFIER Halo Soundbar Max", "array=0x0C",
                  "effect=7", "MAIN=ON", "SUBWOOFER=OFF"):
        assert field in output


@pytest.mark.parametrize("command,enabled", [("on", True), ("off", False)])
@pytest.mark.parametrize("status", list(OperationStatus))
def test_on_off_return_zero_only_after_verified_success(cli, monkeypatch, capsys, command, enabled, status):
    calls = []

    class Controller:
        def set_power(self, target, deadline):
            calls.append((target, deadline))
            return OperationResult(status, 1, None, None, "operation detail")

    monkeypatch.setattr(cli, "build_controller", Controller)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 100.0)
    code = cli.main([command])
    assert (code == 0) == (status is OperationStatus.SUCCESS)
    assert calls == [(enabled, 112.0)]
    output = capsys.readouterr()
    assert status.name in output.out + output.err
    assert "operation detail" in output.out + output.err


@pytest.mark.parametrize("error", [ProtocolError("bad frame"), DeviceNotFoundError("missing"), RuntimeError("broken")])
def test_protocol_or_device_failure_returns_nonzero(cli, monkeypatch, capsys, caplog, error):
    def fail():
        raise error

    monkeypatch.setattr(cli, "build_controller", fail)
    assert cli.main(["probe"]) != 0
    assert str(error) in capsys.readouterr().err
    assert str(error) in caplog.text


def test_debug_uses_real_lifecycle_and_stop_does_not_turn_lights_off(cli, monkeypatch, capsys):
    calls, lifecycles = [], []
    real_lifecycle = cli.ServiceLifecycle

    class Controller:
        def set_power(self, enabled, deadline, cancel_event=None):
            calls.append(enabled)
            return OperationResult(OperationStatus.SUCCESS, 0, None, None, "startup done")

    def lifecycle(*args):
        result = real_lifecycle(*args)
        lifecycles.append(result)
        return result

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "build_controller", Controller)
    monkeypatch.setattr(cli, "ServiceLifecycle", lifecycle)
    monkeypatch.setattr(cli.time, "sleep", interrupt)
    assert cli.main(["debug"]) == 0
    assert calls == [True]
    assert lifecycles[0].run_startup().status is OperationStatus.CANCELLED
    assert "startup done" in capsys.readouterr().out


def test_debug_startup_exception_still_requests_stop(cli, monkeypatch, caplog):
    calls = []

    class Lifecycle:
        def __init__(self, *args):
            pass

        def run_startup(self):
            raise RuntimeError("startup failed")

        def request_stop(self):
            calls.append("stop")

    monkeypatch.setattr(cli, "ServiceLifecycle", Lifecycle)
    assert cli.main(["debug"]) != 0
    assert calls == ["stop"]
    assert "startup failed" in caplog.text


def test_install_requires_frozen_executable(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "install_service", lambda executable: pytest.fail("source install"))
    assert cli.main(["install"], frozen=False) != 0
    assert "frozen" in capsys.readouterr().err.lower()


def test_install_passes_current_executable_and_uninstall_works_in_source(cli, monkeypatch):
    calls = []
    monkeypatch.setattr(cli.sys, "executable", "C:/Tools/Halo Service.exe")
    monkeypatch.setattr(cli, "install_service", lambda executable: calls.append(executable))
    monkeypatch.setattr(cli, "uninstall_service", lambda: calls.append("uninstall"))
    assert cli.main(["install"], frozen=True) == 0
    assert cli.main(["uninstall"], frozen=False) == 0
    assert calls == [Path("C:/Tools/Halo Service.exe"), "uninstall"]


@pytest.mark.parametrize("command", ["install", "uninstall"])
def test_scm_error_returns_nonzero_and_is_logged(cli, monkeypatch, capsys, caplog, command):
    def fail(*args):
        raise OSError("access denied")

    monkeypatch.setattr(cli, command + "_service", fail)
    assert cli.main([command], frozen=True) != 0
    assert "access denied" in capsys.readouterr().err
    assert "access denied" in caplog.text


@pytest.mark.parametrize("argv,code", [([], 2), (["--help"], 0), (["unknown"], 2)])
def test_cli_help_and_invalid_arguments_return_codes(cli, argv, code):
    assert cli.main(argv) == code


@pytest.fixture
def entrypoint(cli, monkeypatch):
    module = importlib.import_module("halo_soundbar_service.__main__")
    monkeypatch.setattr(module, "configure_logging", lambda: logging.getLogger("entrypoint-test"))
    return module


def test_frozen_no_args_enters_service_dispatcher(entrypoint, monkeypatch):
    calls = []
    manager = entrypoint.servicemanager
    monkeypatch.setattr(manager, "Initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(manager, "PrepareToHostSingle", lambda service: calls.append(service))
    monkeypatch.setattr(manager, "StartServiceCtrlDispatcher", lambda: calls.append("dispatch"))
    monkeypatch.setattr(entrypoint, "main", lambda *a, **kw: pytest.fail("entered CLI"))
    assert entrypoint.dispatch([], frozen=True) == 0
    assert calls == ["initialize", entrypoint.HaloSoundbarLightService, "dispatch"]


@pytest.mark.parametrize("command", ["probe", "on", "off", "debug", "install", "uninstall", "--help", "invalid"])
@pytest.mark.parametrize("frozen", [True, False])
def test_any_user_command_enters_cli(entrypoint, monkeypatch, command, frozen):
    calls = []

    def main(argv, *, frozen):
        calls.append((argv, frozen))
        return 7

    monkeypatch.setattr(entrypoint, "main", main)
    monkeypatch.setattr(entrypoint.servicemanager, "Initialize", lambda: pytest.fail("SCM initialized"))
    assert entrypoint.dispatch([command], frozen=frozen) == 7
    assert calls == [([command], frozen)]


def test_source_module_no_args_shows_help_instead_of_dispatcher(entrypoint, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.servicemanager, "Initialize", lambda: pytest.fail("SCM initialized"))
    assert entrypoint.dispatch([], frozen=False) == 2
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["Initialize", "PrepareToHostSingle", "StartServiceCtrlDispatcher"])
def test_dispatcher_failures_return_nonzero_and_log(entrypoint, monkeypatch, capsys, caplog, stage):
    for name in ("Initialize", "PrepareToHostSingle", "StartServiceCtrlDispatcher"):
        monkeypatch.setattr(entrypoint.servicemanager, name, lambda *args: None)

    def fail(*args):
        raise RuntimeError("dispatcher failed")

    monkeypatch.setattr(entrypoint.servicemanager, stage, fail)
    assert entrypoint.dispatch([], frozen=True) != 0
    assert "dispatcher failed" in capsys.readouterr().err
    assert "dispatcher failed" in caplog.text


def test_entry_script_is_usable_as_pyinstaller_input(entrypoint, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [entrypoint.__file__, "--help"])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(entrypoint.__file__, run_name="__main__")
    assert result.value.code == 0
    assert "usage:" in capsys.readouterr().out
