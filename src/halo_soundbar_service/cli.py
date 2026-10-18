"""Explicit interactive commands; importing the CLI never opens HID or SCM."""

import argparse
from collections.abc import Sequence
import logging
from pathlib import Path
import sys
from threading import Event
import time

from .controller import OperationResult, OperationStatus
from .installer import install_service, uninstall_service
from .lifecycle import ServiceLifecycle
from .logging_setup import configure_logging
from .protocol import PowerTarget
from .service import build_controller


def _print_result(result: OperationResult, logger: logging.Logger) -> int:
    success = result.status is OperationStatus.SUCCESS
    message = f"{result.status.name}: attempts={result.attempts} detail={result.detail}"
    print(message, file=sys.stdout if success else sys.stderr)
    logger.log(logging.INFO if success else logging.ERROR, "%s", message)
    return 0 if success else 1


def _debug(logger: logging.Logger) -> int:
    cancel = Event()
    lifecycle = ServiceLifecycle(build_controller, cancel, time.monotonic, cancel.wait)
    code = 0
    try:
        code = _print_result(lifecycle.run_startup(), logger)
        print("Foreground diagnostic running; press Ctrl+C to stop.")
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        return code
    finally:
        lifecycle.request_stop()


def main(argv: Sequence[str] | None = None, *, frozen: bool | None = None) -> int:
    parser = argparse.ArgumentParser(description="Halo Soundbar ambient light service")
    parser.add_argument("command", nargs="?", choices=("probe", "on", "off", "debug", "install", "uninstall"))
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if args.command is None:
        parser.print_help()
        return 2

    logger = logging.getLogger(__name__)
    try:
        logger = configure_logging()
        if args.command == "install":
            is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
            if not is_frozen:
                raise RuntimeError("install requires a frozen executable; source mode is unsupported")
            install_service(Path(sys.executable))
        elif args.command == "uninstall":
            uninstall_service()
        elif args.command == "probe":
            snapshot = build_controller().probe()
            print("VID=0x2D99 PID=0xA073 interface=4 usage_page=0xFF14 usage=0x0001")
            print("product=EDIFIER Halo Soundbar Max")
            print(f"array=0x{snapshot.array_index:02X} effect={snapshot.effect_id}")
            for target in PowerTarget:
                print(f"{target.name}={'ON' if snapshot.entries[target.value].blue else 'OFF'}")
        elif args.command == "debug":
            return _debug(logger)
        else:
            result = build_controller().set_power(args.command == "on", time.monotonic() + 12)
            return _print_result(result, logger)
        return 0
    except KeyboardInterrupt:
        logger.warning("Command interrupted")
        print("Command interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        logger.exception("Command %s failed", args.command)
        print(f"{args.command}: {error}", file=sys.stderr)
        return 1
