"""Thin SCM adapter around the platform-independent service lifecycle."""

import logging
import threading
import time

import win32service
import win32serviceutil

from .controller import LightController, OperationResult, OperationStatus
from .device import DeviceClient, HidApiBackend
from .lifecycle import ServiceLifecycle
from .logging_setup import configure_logging


SERVICE_NAME = "HaloSoundbarLightService"
PRESHUTDOWN_CONTROL = 15


def build_controller() -> LightController:
    """Construct the controller without opening a device until its first operation."""
    return LightController(
        lambda: DeviceClient.connect(HidApiBackend(), time.monotonic),
        time.monotonic,
        time.sleep,
    )


class HaloSoundbarLightService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "EDIFIER Halo SoundBar Light Service"
    _svc_description_ = "Turns ambient lights on at startup and off before shutdown."

    def __init__(self, args):
        # Prepare state before registering control callbacks with the SCM.
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._logger = configure_logging()
        self._lifecycle = ServiceLifecycle(
            build_controller, self._cancel_event, time.monotonic, self._cancel_event.wait
        )
        super().__init__(args)

    def GetAcceptedControls(self):
        return super().GetAcceptedControls() | win32service.SERVICE_ACCEPT_PRESHUTDOWN

    def SvcDoRun(self):
        # ServiceFramework.SvcRun already reported RUNNING before entering here.
        self._startup_thread = threading.Thread(
            target=self._run_startup, name="halo-light-startup", daemon=True
        )
        self._startup_thread.start()
        self._stop_event.wait()
        self._startup_thread.join(timeout=2.0)
        if self._startup_thread.is_alive():
            self._logger.error("Startup worker did not exit within the join timeout")

    def _run_startup(self):
        try:
            self._log_result("startup", self._lifecycle.run_startup())
        except Exception:
            self._logger.exception("Startup light operation failed")

    def SvcOtherEx(self, control, event_type, data):
        if control == PRESHUTDOWN_CONTROL:
            self._shutdown("preshutdown", self._lifecycle.handle_preshutdown)
        else:
            return super().SvcOtherEx(control, event_type, data)

    def SvcShutdown(self):
        self._shutdown("shutdown", self._lifecycle.handle_shutdown)

    def _shutdown(self, phase, action):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=15000)
        try:
            self._log_result(phase, action())
        except Exception:
            self._logger.exception("%s light operation failed", phase)
        finally:
            # Lifecycle cancellation starts before off; SCM exit must wait until after off.
            self._stop_event.set()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._lifecycle.request_stop()
        self._stop_event.set()

    def _log_result(self, phase: str, result: OperationResult):
        level = (logging.INFO if result.status in
                 (OperationStatus.SUCCESS, OperationStatus.CANCELLED) else logging.ERROR)
        self._logger.log(level, "%s: status=%s attempts=%d detail=%s",
                         phase, result.status.name, result.attempts, result.detail)
