# -*- coding: utf-8 -*-
"""Tests for ``agentscope._logging.setup_logger``."""
import logging
from collections.abc import Generator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from agentscope import logger, setup_logger


@pytest.fixture(autouse=True)
def _clean_logger() -> Generator[None, None, None]:
    """Start and end every test from a single console handler."""
    _reset()
    yield
    _reset()


def _reset() -> None:
    """Drop every handler and install a fresh console-only configuration."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    setup_logger("INFO")


def _file_handlers() -> list[logging.FileHandler]:
    """Return the file handlers currently attached to the module logger."""
    return [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
    ]


class TestSetupLoggerReleasesHandlers:
    """A replaced handler must be closed, not merely detached."""

    def test_replacing_a_file_handler_closes_it(self) -> None:
        """Switching to console-only closes the previous file handler."""
        with TemporaryDirectory() as directory:
            setup_logger("INFO", str(Path(directory) / "first.log"))
            old = _file_handlers()[0]

            setup_logger("INFO")

            assert old.stream is None

    def test_replacing_a_file_handler_with_another_file_closes_it(
        self,
    ) -> None:
        """Switching to a second file closes the first one."""
        with TemporaryDirectory() as directory:
            setup_logger("INFO", str(Path(directory) / "first.log"))
            old = _file_handlers()[0]

            setup_logger("INFO", str(Path(directory) / "second.log"))

            assert old.stream is None

    def test_the_replacement_handler_stays_writable(self) -> None:
        """Closing the old handler must not break the new one."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "second.log"
            setup_logger("INFO", str(Path(directory) / "first.log"))

            setup_logger("INFO", str(path))
            logger.info("after reconfiguration")

            assert "after reconfiguration" in path.read_text()

    def test_no_file_handler_survives_a_console_only_reconfiguration(
        self,
    ) -> None:
        """A console-only setup leaves no file handler behind."""
        with TemporaryDirectory() as directory:
            setup_logger("INFO", str(Path(directory) / "first.log"))

            setup_logger("INFO")

            assert _file_handlers() == []

    def test_repeated_reconfiguration_does_not_accumulate_handlers(
        self,
    ) -> None:
        """Reconfiguring repeatedly keeps the handler count stable."""
        with TemporaryDirectory() as directory:
            for index in range(3):
                setup_logger("INFO", str(Path(directory) / f"{index}.log"))

            assert len(logger.handlers) == 2


class TestSetupLoggerKeepsExistingBehavior:
    """The release fix must not change the documented behaviour."""

    def test_invalid_level_leaves_the_handlers_intact(self) -> None:
        """A rejected level is validated before any handler is touched."""
        with TemporaryDirectory() as directory:
            setup_logger("INFO", str(Path(directory) / "first.log"))
            before = list(logger.handlers)

            with pytest.raises(ValueError):
                setup_logger("NOT-A-LEVEL")

            assert logger.handlers == before
            assert all(handler.stream is not None for handler in before)

    def test_level_and_console_handler_are_applied(self) -> None:
        """The level, the console handler and propagate are still set."""
        setup_logger("DEBUG")

        console = [
            handler
            for handler in logger.handlers
            if not isinstance(handler, logging.FileHandler)
        ]
        assert logger.level == logging.DEBUG
        assert len(console) == 1
        assert not logger.propagate
