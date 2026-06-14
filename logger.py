"""
iSync - Logging configuration
Sets up structured logging with console + optional file output.
"""

import sys
import logging
from typing import Optional


# ANSI color codes for log level labels
_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}
_RESET = "\033[0m"


class _ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI color to log level names."""

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in _COLORS:
            record.levelname = f"{_COLORS[levelname]}{levelname}{_RESET}"
        return super().format(record)


def setup_logging(level: str = "INFO", log_file: str = "",
                  use_color: bool = True) -> logging.Logger:
    """
    Configure the root iSync logger.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to a log file.
        use_color: Whether to emit ANSI color codes to stdout.

    Returns:
        The configured root 'isync' logger.
    """
    logger = logging.getLogger("isync")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logger.level)
    if use_color and sys.stdout.isatty():
        console.setFormatter(_ColoredFormatter(fmt, datefmt=datefmt))
    else:
        console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logger.level)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(file_handler)
        logger.info("Logging to file: %s", log_file)

    return logger
