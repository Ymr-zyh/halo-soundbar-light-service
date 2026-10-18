"""One executable acts as SCM service host or explicit administrator CLI."""

from collections.abc import Sequence
import logging
import sys

import servicemanager

# Absolute imports also support executing this file as a PyInstaller entry script.
from halo_soundbar_service.cli import main
from halo_soundbar_service.logging_setup import configure_logging
from halo_soundbar_service.service import HaloSoundbarLightService


def dispatch(argv: Sequence[str], frozen: bool) -> int:
    if argv or not frozen:
        return main(argv, frozen=frozen)
    logger = logging.getLogger(__name__)
    try:
        logger = configure_logging()
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(HaloSoundbarLightService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    except KeyboardInterrupt:
        logger.warning("Service dispatcher interrupted")
        return 130
    except Exception as error:
        logger.exception("Service dispatcher failed")
        print(f"Service dispatcher: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(dispatch(sys.argv[1:], bool(getattr(sys, "frozen", False))))
