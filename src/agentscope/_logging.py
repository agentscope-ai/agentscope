# -*- coding: utf-8 -*-
"""The logger for agentscope."""

import logging


_DEFAULT_FORMAT = (
    "%(asctime)s | %(levelname)-7s | "
    "%(module)s:%(funcName)s:%(lineno)s - %(message)s"
)

logger = logging.getLogger("as")


def setup_logger(
    level: str,
    filepath: str | None = None,
) -> None:
    """Set up the agentscope logger, closing handlers being replaced.

    Args:
        level (`str`):
            The logging level, chosen from "INFO", "DEBUG", "WARNING",
            "ERROR", "CRITICAL".
        filepath (`str | None`, optional):
            The filepath to save the logging output.
    """
    if level not in ["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"]:
        raise ValueError(
            f"Invalid logging level: {level}. Must be one of "
            f"'INFO', 'DEBUG', 'WARNING', 'ERROR', 'CRITICAL'.",
        )
    for previous_handler in logger.handlers[:]:
        logger.removeHandler(previous_handler)
        previous_handler.close()
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    logger.addHandler(handler)

    if filepath:
        handler = logging.FileHandler(filepath)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)

    logger.propagate = False


setup_logger("INFO")
