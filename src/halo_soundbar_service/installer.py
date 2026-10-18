"""Register the frozen executable directly, with an injectable SCM boundary."""

import logging
from pathlib import Path
import time
from typing import Protocol

import win32service
from win32service import (
    SC_ACTION_RESTART, SC_MANAGER_CONNECT, SC_MANAGER_CREATE_SERVICE,
    SERVICE_ALL_ACCESS, SERVICE_AUTO_START, SERVICE_CONFIG_DESCRIPTION,
    SERVICE_CONFIG_FAILURE_ACTIONS, SERVICE_CONFIG_PRESHUTDOWN_INFO,
    SERVICE_CONTROL_STOP, SERVICE_ERROR_NORMAL, SERVICE_QUERY_STATUS,
    SERVICE_STOP, SERVICE_STOPPED, SERVICE_STOP_PENDING, SERVICE_WIN32_OWN_PROCESS,
)

from .service import HaloSoundbarLightService, SERVICE_NAME


_DELETE_ACCESS = 0x00010000
_LOGGER = logging.getLogger(__name__)


class ScmApi(Protocol):
    """Only the native calls needed for registration and removal."""

    def OpenSCManager(self, machine, database, access): ...
    def CreateService(self, *args): ...
    def OpenService(self, manager, name, access): ...
    def ChangeServiceConfig2(self, service, level, value): ...
    def QueryServiceStatus(self, service): ...
    def ControlService(self, service, control): ...
    def DeleteService(self, service): ...
    def CloseServiceHandle(self, handle): ...


def configure_installed_service(api: ScmApi, executable: Path) -> None:
    """Create and configure one own-process LocalSystem automatic service."""
    image_path = f'"{Path(executable).resolve()}"'
    manager = api.OpenSCManager(None, None, SC_MANAGER_CREATE_SERVICE)
    try:
        service = api.CreateService(
            manager, SERVICE_NAME, HaloSoundbarLightService._svc_display_name_,
            SERVICE_ALL_ACCESS, SERVICE_WIN32_OWN_PROCESS, SERVICE_AUTO_START,
            SERVICE_ERROR_NORMAL, image_path, None, 0, None, None, None,
        )
        try:
            api.ChangeServiceConfig2(
                service, SERVICE_CONFIG_DESCRIPTION,
                HaloSoundbarLightService._svc_description_,
            )
            api.ChangeServiceConfig2(service, SERVICE_CONFIG_FAILURE_ACTIONS, {
                "ResetPeriod": 86400,
                "RebootMsg": None,
                "Command": None,
                "Actions": [(SC_ACTION_RESTART, 60000)] * 3,
            })
            api.ChangeServiceConfig2(service, SERVICE_CONFIG_PRESHUTDOWN_INFO, 15000)
        except BaseException:
            try:
                api.DeleteService(service)
            except Exception:
                _LOGGER.exception("Could not roll back incomplete service installation")
            raise
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)


def install_service(executable: Path) -> None:
    configure_installed_service(win32service, executable)


def _error_code(error: Exception):
    return getattr(error, "winerror", error.args[0] if error.args else None)


def uninstall_service(
    *, api: ScmApi | None = None, monotonic=time.monotonic, sleep=time.sleep,
    timeout_s: float = 30.0,
) -> None:
    """Stop within a bounded wait and remove only the SCM registration."""
    api = win32service if api is None else api
    manager = api.OpenSCManager(None, None, SC_MANAGER_CONNECT)
    try:
        try:
            service = api.OpenService(
                manager, SERVICE_NAME, SERVICE_STOP | SERVICE_QUERY_STATUS | _DELETE_ACCESS
            )
        except Exception as error:
            if _error_code(error) == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
                return
            raise
        try:
            deadline = monotonic() + timeout_s
            state = api.QueryServiceStatus(service)[1]
            if state not in (SERVICE_STOPPED, SERVICE_STOP_PENDING):
                try:
                    api.ControlService(service, SERVICE_CONTROL_STOP)
                except Exception as error:
                    if _error_code(error) != 1062:  # ERROR_SERVICE_NOT_ACTIVE
                        raise
            while state != SERVICE_STOPPED:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Service did not stop before uninstall deadline")
                sleep(min(0.25, remaining))
                state = api.QueryServiceStatus(service)[1]
            api.DeleteService(service)
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)
