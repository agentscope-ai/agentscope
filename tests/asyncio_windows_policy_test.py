# -*- coding: utf-8 -*-
"""Tests for Windows asyncio subprocess compatibility helpers."""
import asyncio
import sys
import unittest

from agentscope._utils._asyncio import ensure_windows_proactor_event_loop_policy


class TestWindowsProactorEventLoopPolicy(unittest.TestCase):
    """Ensure subprocess-capable loop policy is selected on Windows."""

    def test_idempotent_on_windows(self) -> None:
        ensure_windows_proactor_event_loop_policy()
        ensure_windows_proactor_event_loop_policy()
        if sys.platform == "win32":
            self.assertIsInstance(
                asyncio.get_event_loop_policy(),
                asyncio.WindowsProactorEventLoopPolicy,
            )

    @unittest.skipUnless(sys.platform == "win32", "Windows-only")
    def test_local_backend_exec_shell_runs_subprocess(self) -> None:
        """Regression for agent-service workspace routes on Windows."""

        async def run() -> None:
            from agentscope.tool import LocalBackend

            backend = LocalBackend()
            result = await backend.exec_shell(
                [sys.executable, "-c", "print('ok')"],
            )
            self.assertTrue(result.ok())
            self.assertIn(b"ok", result.stdout)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
