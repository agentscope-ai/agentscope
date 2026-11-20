# -*- coding: utf-8 -*-
"""Regression tests for resources replaced by logger reconfiguration."""

import logging
from pathlib import Path
import tempfile
import unittest

from agentscope._logging import logger, setup_logger


class LoggingTest(unittest.TestCase):
    """Reconfiguration releases removed handlers and keeps new output usable."""

    def setUp(self) -> None:
        self.saved_handlers = logger.handlers[:]
        self.saved_level = logger.level
        self.saved_propagate = logger.propagate
        logger.handlers.clear()
        self.fixture = tempfile.TemporaryDirectory(prefix="agentscope-logger-")
        self.directory = Path(self.fixture.name).resolve()
        self.assertIn(
            Path(tempfile.gettempdir()).resolve(), self.directory.parents
        )

    def tearDown(self) -> None:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.handlers[:] = self.saved_handlers
        logger.setLevel(self.saved_level)
        logger.propagate = self.saved_propagate
        self.doCleanups()
        self.assertIn(
            Path(tempfile.gettempdir()).resolve(), self.directory.parents
        )
        self.fixture.cleanup()

    def configure_file(self, name: str) -> logging.FileHandler:
        """Configure a real file and close its handle even if a test fails."""
        setup_logger("INFO", str(self.directory / name))
        handler = next(
            item
            for item in logger.handlers
            if isinstance(item, logging.FileHandler)
        )
        self.addCleanup(handler.close)
        return handler

    def test_replacing_file_closes_old_handler(self) -> None:
        old = self.configure_file("first.log")
        self.configure_file("second.log")
        self.assertIsNone(old.stream)

    def test_switching_to_console_closes_old_file(self) -> None:
        old = self.configure_file("first.log")
        setup_logger("WARNING")
        self.assertIsNone(old.stream)
        self.assertEqual(len(logger.handlers), 1)

    def test_replaced_custom_handler_is_closed(self) -> None:
        handler = logging.Handler()
        self.addCleanup(handler.close)
        logger.addHandler(handler)
        setup_logger("INFO")
        self.assertTrue(handler._closed)

    def test_reusing_file_keeps_records_and_closes_old_handler(self) -> None:
        old = self.configure_file("same.log")
        logger.info("before-reconfiguration")
        self.configure_file("same.log")
        logger.info("after-reconfiguration")
        self.assertIsNone(old.stream)
        content = (self.directory / "same.log").read_text()
        self.assertEqual(content.count("before-reconfiguration"), 1)
        self.assertEqual(content.count("after-reconfiguration"), 1)

    def test_invalid_level_preserves_existing_handlers(self) -> None:
        handler = self.configure_file("first.log")
        previous = logger.handlers[:]
        with self.assertRaises(ValueError):
            setup_logger("invalid")
        self.assertEqual(logger.handlers, previous)
        self.assertIsNotNone(handler.stream)

    def test_new_file_receives_each_message_once(self) -> None:
        self.configure_file("first.log")
        self.configure_file("second.log")
        logger.info("unique-new-file-record")
        self.assertNotIn(
            "unique-new-file-record",
            (self.directory / "first.log").read_text(),
        )
        self.assertEqual(
            (self.directory / "second.log")
            .read_text()
            .count(
                "unique-new-file-record",
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
