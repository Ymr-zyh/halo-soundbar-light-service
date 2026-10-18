"""Windows Application Event Log plus best-effort rotating UTF-8 file logs."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

import servicemanager


class EventLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if record.levelno >= logging.ERROR:
                servicemanager.LogErrorMsg(message)
            elif record.levelno >= logging.WARNING:
                servicemanager.LogWarningMsg(message)
            else:
                servicemanager.LogInfoMsg(message)
        except Exception:
            # Logging failures must neither interrupt controls nor log recursively.
            pass


def configure_logging(program_data: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("HaloSoundbarLightService")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if not any(isinstance(handler, EventLogHandler) for handler in logger.handlers):
        event_handler = EventLogHandler()
        event_handler.setFormatter(formatter)
        logger.addHandler(event_handler)

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        root = program_data if program_data is not None else Path(
            os.environ.get("ProgramData", r"C:\ProgramData")
        )
        try:
            directory = root / "HaloSoundbarLightService"
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                directory / "service.log", maxBytes=1 * 1024 * 1024,
                backupCount=3, encoding="utf-8",
            )
        except OSError as error:
            logger.error("File logging unavailable: %s", error)
        else:
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    return logger
