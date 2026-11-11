# -*- coding: utf-8 -*-
"""Unit tests for the index worker entry point."""
import logging
import os
import unittest
from unittest.mock import patch

from agentscope.app.rag.index_worker.__main__ import main


class IndexWorkerMainTest(unittest.TestCase):
    """Tests for the index worker entry point."""

    def test_lowercase_log_level(self) -> None:
        """A lowercase ``LOG_LEVEL`` configures logging instead of crashing."""
        # basicConfig is a no-op while the root logger has handlers
        handlers, level = logging.root.handlers[:], logging.root.level
        logging.root.handlers.clear()
        try:
            with patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=True):
                with self.assertRaises(SystemExit) as ctx:
                    main()
            self.assertListEqual(
                [ctx.exception.code, logging.root.level],
                [2, logging.DEBUG],
            )
        finally:
            logging.root.handlers[:] = handlers
            logging.root.setLevel(level)


if __name__ == "__main__":
    unittest.main()
