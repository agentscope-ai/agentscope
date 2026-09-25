# -*- coding: utf-8 -*-
"""Test cases for the :class:`OpenSandboxBackend` exit-code mapping.

``opensandbox.models.execd.Execution.exit_code`` is ``int | None``: the
SDK leaves it unset when a foreground command ends on a non-numeric error
message, or when its event stream carries no terminal event at all.

The executions below use the direct ``stdout`` / ``stderr`` shape that
``OpenSandboxBackend._execution_stream_bytes`` documents for unit tests,
so the suite needs neither a live sandbox nor the ``opensandbox``
package.
"""

from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.tool import ExecResult
from agentscope.workspace import OpenSandboxBackend


def _backend_for(exit_code: object) -> OpenSandboxBackend:
    """Build a backend whose single command reports ``exit_code``."""
    execution = SimpleNamespace(exit_code=exit_code, stdout=b"", stderr=b"")
    sandbox = SimpleNamespace(
        commands=SimpleNamespace(run=AsyncMock(return_value=execution)),
    )
    return OpenSandboxBackend(sandbox, workdir="/workspace")


class OpenSandboxExitCodeTest(IsolatedAsyncioTestCase):
    """``exec_shell`` must never read an unset exit code as success."""

    async def test_unset_exit_code_becomes_the_minus_one_sentinel(
        self,
    ) -> None:
        """An execution carrying no exit code reports -1, not 0."""
        result = await _backend_for(None).exec_shell(["python", "-c", "pass"])
        self.assertEqual(
            result,
            ExecResult(exit_code=-1, stdout=b"", stderr=b""),
        )

    async def test_unset_exit_code_is_not_a_missing_path(self) -> None:
        """``file_exists`` cannot claim a path exists on an unknown code."""
        exists = await _backend_for(None).file_exists("/absent.txt")
        self.assertEqual(exists, False)

    async def test_reported_nonzero_exit_code_is_kept(self) -> None:
        """A reported non-zero exit stays a normal, non-raising result."""
        result = await _backend_for(4).exec_shell(["false"])
        self.assertEqual(
            result,
            ExecResult(exit_code=4, stdout=b"", stderr=b""),
        )

    async def test_reported_zero_exit_code_is_a_success(self) -> None:
        """A reported zero is still the only outcome ``ok()`` accepts."""
        result = await _backend_for(0).exec_shell(["true"])
        self.assertEqual(
            result,
            ExecResult(exit_code=0, stdout=b"", stderr=b""),
        )
