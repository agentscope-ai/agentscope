# -*- coding: utf-8 -*-
"""Unit tests for the log-level handling of the index worker entry point.

``LOG_LEVEL`` is written by deployments, so it arrives in the shapes
operators use rather than the spellings ``logging`` accepts: the empty
string, lowercase names, and numeric strings.  Before the worker
resolved them, :func:`main` crashed with ``ValueError`` inside
``logging.basicConfig`` — before the bootstrap check, so the operator
lost the actionable "AGENTSCOPE_WORKER_BOOTSTRAP is required" message
along with the process.
"""
import io
import logging
import os
import sys
import unittest
from unittest.mock import patch

from agentscope.app.rag.index_worker.__main__ import main


class IndexWorkerMainTest(unittest.TestCase):
    """Check that an unusable ``LOG_LEVEL`` never kills the worker."""

    def setUp(self) -> None:
        # ``basicConfig`` returns early when the root logger already has
        # handlers — and pytest attaches one — so it would never even
        # look at ``level``.  Clear the handlers to exercise the real
        # startup path, and put them back afterwards.
        self._saved_handlers = list(logging.root.handlers)
        self._saved_level = logging.root.level
        logging.root.handlers.clear()

    def tearDown(self) -> None:
        for handler in list(logging.root.handlers):
            if handler not in self._saved_handlers:
                handler.close()
        logging.root.handlers[:] = self._saved_handlers
        logging.root.setLevel(self._saved_level)

    def _run_main(self, raw: str, expect_warning: bool = False) -> tuple[
        int, io.StringIO
    ]:
        """Start the worker with ``LOG_LEVEL`` set to ``raw``.

        Args:
            raw (`str`):
                The value to expose to the worker, verbatim.
            expect_warning (`bool`, defaults to `False`):
                Whether ``raw`` is one the worker should refuse to read.
                Every readable spelling is also asserted to produce *no*
                warning — otherwise a value like ``"20"``, whose parsed
                level equals the fallback, would pass by being rescued
                rather than by being understood.

        Returns:
            `tuple[int, io.StringIO]`:
                The exit code the worker asked for, and what it wrote to
                stderr.  Reaching an exit code at all is the point: an
                unresolved level raises instead, so the assertion below
                fails on its own rather than swallowing the crash.

        Side effects:
            Root handlers are cleared first — ``basicConfig`` is a no-op
            once any handler exists, which would otherwise let a later
            call inherit the previous level.
        """
        logging.root.handlers.clear()
        stderr = io.StringIO()
        with patch.dict(os.environ, {"LOG_LEVEL": raw}, clear=True):
            with patch.object(sys, "stderr", stderr):
                if expect_warning:
                    ctx = self.assertLogs("as", level="WARNING")
                else:
                    ctx = self.assertNoLogs("as", level="WARNING")
                with ctx as captured:
                    with self.assertRaises(SystemExit) as caught:
                        main()
        self.captured = captured
        return caught.exception.code, stderr

    def test_lowercase_level_starts_and_configures_info(self) -> None:
        """``LOG_LEVEL=info`` is the commonest typo and must not crash."""
        code, _ = self._run_main("info")
        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.INFO)

    def test_numeric_level_starts_and_configures_that_level(self) -> None:
        """``LOG_LEVEL=20`` is how env files and log drivers write it."""
        code, _ = self._run_main("20")
        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.INFO)

    def test_empty_level_starts_on_info(self) -> None:
        """An empty ``LOG_LEVEL`` means "not configured", not "crash"."""
        code, _ = self._run_main("")
        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.INFO)

    def test_padded_level_starts_and_configures_debug(self) -> None:
        """Values read out of a YAML scalar keep their surrounding space."""
        code, _ = self._run_main("  Debug \n")
        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.DEBUG)

    def test_missing_bootstrap_is_reported_under_a_lowercase_level(
        self,
    ) -> None:
        """The operator still learns what the worker actually needed."""
        code, stderr = self._run_main("info")
        self.assertEqual(code, 2)
        self.assertIn("AGENTSCOPE_WORKER_BOOTSTRAP", stderr.getvalue())

    def test_unusable_level_falls_back_to_info_and_says_so(self) -> None:
        """A level nobody can read still starts the worker, with a warning."""
        code, _ = self._run_main("chatty", expect_warning=True)

        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.INFO)
        self.assertEqual(len(self.captured.output), 1)
        self.assertIn("chatty", self.captured.output[0])

    def test_uppercase_level_still_works(self) -> None:
        """The spelling that already worked is unchanged."""
        code, _ = self._run_main("WARNING")
        self.assertEqual(code, 2)
        self.assertEqual(logging.root.level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
