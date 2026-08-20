# -*- coding: utf-8 -*-
"""``GET /workspace/status`` test case.

The endpoint's whole job beyond composing a response is refusing to fail
loudly. ``exec_shell`` reports trouble inconsistently across backends —
Docker and K8s raise, the rest fold everything into a non-zero
``ExecResult`` — and a directory that is simply not a repository is the
common case, not an error. Every one of those paths is asserted to yield
``git: null`` with the rest of the response intact.

Dependencies are injected by calling the endpoint coroutine directly, so
no app, no git binary and no real filesystem are involved.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from fastapi import HTTPException, status

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._router._workspace import get_workspace_status
from agentscope.app._service import WorkspaceService
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
)
from agentscope.state import AgentState
from agentscope.tool import ExecResult

_STATUS_STDOUT = (
    b"# branch.oid abc123\0"
    b"# branch.head main\0"
    b"# branch.upstream origin/main\0"
    b"# branch.ab +2 -1\0"
    b"1 .M N... 100644 100644 100644 111 222 a.py\0"
    b"? notes.md\0"
)
_SHORTSTAT_STDOUT = b" 1 file changed, 15 insertions(+), 20 deletions(-)\n"


class _FakeBackend:
    """Answers the two git invocations from a canned script."""

    def __init__(self, *results: Any) -> None:
        # Each entry is either an ExecResult to return or an exception
        # to raise, consumed in call order.
        self._results = list(results)
        self.calls: list[tuple[list[str], str | None]] = []

    def abspath(self, path: str, *, cwd: str) -> str:
        """Resolve like posixpath, which is what the real backends use."""
        if path.startswith("/"):
            return path
        return f"{cwd}/{path}".rstrip("/") if path else cwd

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Return or raise the next scripted outcome."""
        _ = timeout
        self.calls.append((command, cwd))
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeWorkspace:
    def __init__(self, backend: _FakeBackend) -> None:
        self.workdir = "/workspace"
        self._backend = backend

    def get_backend(self) -> _FakeBackend:
        """Expose the injected backend."""
        return self._backend


class _FakeWorkspaceManager:
    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._ws = workspace

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> _FakeWorkspace:
        """Return the pre-seeded workspace, ignoring identifier args."""
        _ = (user_id, agent_id, session_id, workspace_id)
        return self._ws


class _FakeStorage:
    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return the seeded record, or None for any other id."""
        _ = user_id, agent_id
        return self._record if session_id == self._record.id else None


def _make_session(cwd: str | None = None) -> SessionRecord:
    """Build a session record anchored at ``cwd``."""
    return SessionRecord(
        user_id="u",
        agent_id="a",
        id="s",
        source=SessionSource.USER,
        state=AgentState(),
        config=SessionConfig(workspace_id="ws-1", name="t", cwd=cwd),
        agent_snapshot=AgentRecord(
            user_id="u",
            source="user",
            data=AgentData(
                name="A",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        ),
    )


class WorkspaceStatusTest(IsolatedAsyncioTestCase):
    """Compose the response and degrade on every git failure mode."""

    def setUp(self) -> None:
        """Hold the backend so tests can assert on the calls it saw."""
        self.backend = _FakeBackend()

    async def _call(
        self,
        *results: Any,
        cwd: str | None = None,
    ) -> Any:
        """Invoke the endpoint against a scripted backend."""
        self.backend = _FakeBackend(*results)
        workspace = _FakeWorkspace(self.backend)
        return await get_workspace_status(
            agent_id="a",
            session_id="s",
            user_id="u",
            workspace_service=WorkspaceService(
                _FakeStorage(_make_session(cwd)),
                _FakeWorkspaceManager(workspace),
                "secret",
            ),
        )

    async def test_reports_branch_and_line_counts(self) -> None:
        """Both commands succeeding yields a fully populated status."""
        result = await self._call(
            ExecResult(exit_code=0, stdout=_STATUS_STDOUT, stderr=b""),
            ExecResult(exit_code=0, stdout=_SHORTSTAT_STDOUT, stderr=b""),
        )

        self.assertEqual(result.workdir, "/workspace")
        self.assertEqual(result.cwd, "/workspace")
        self.assertIsNotNone(result.git)
        self.assertEqual(result.git.branch, "main")
        self.assertEqual(result.git.ahead, 2)
        self.assertEqual(result.git.behind, 1)
        self.assertEqual(result.git.unstaged, 1)
        self.assertEqual(result.git.untracked, 1)
        self.assertEqual(result.git.insertions, 15)
        self.assertEqual(result.git.deletions, 20)

    async def test_cwd_anchors_the_git_invocation(self) -> None:
        """A relative cwd resolves against the workspace root.

        Both commands must run there — reporting the root's branch while
        the UI names a subdirectory would be quietly wrong.
        """
        result = await self._call(
            ExecResult(exit_code=0, stdout=_STATUS_STDOUT, stderr=b""),
            ExecResult(exit_code=0, stdout=_SHORTSTAT_STDOUT, stderr=b""),
            cwd="sub/project",
        )

        self.assertEqual(result.cwd, "/workspace/sub/project")
        self.assertEqual(
            [call_cwd for _, call_cwd in self.backend.calls],
            ["/workspace/sub/project", "/workspace/sub/project"],
        )

    async def test_absolute_cwd_is_used_verbatim(self) -> None:
        """A cwd outside the workspace root is honoured, not clamped."""
        result = await self._call(
            ExecResult(exit_code=0, stdout=_STATUS_STDOUT, stderr=b""),
            ExecResult(exit_code=0, stdout=_SHORTSTAT_STDOUT, stderr=b""),
            cwd="/elsewhere/repo",
        )

        self.assertEqual(result.cwd, "/elsewhere/repo")

    async def test_not_a_repository(self) -> None:
        """Exit 128 is the ordinary "no repo here" answer."""
        result = await self._call(
            ExecResult(
                exit_code=128,
                stdout=b"",
                stderr=b"fatal: not a git repository",
            ),
        )

        self.assertIsNone(result.git)
        self.assertEqual(result.workdir, "/workspace")

    async def test_git_binary_missing(self) -> None:
        """Exit 127 covers both a missing binary and a bad cwd."""
        result = await self._call(
            ExecResult(exit_code=127, stdout=b"", stderr=b"No such file"),
        )

        self.assertIsNone(result.git)

    async def test_timeout(self) -> None:
        """A timed-out command reports -1 rather than raising."""
        result = await self._call(
            ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out"),
        )

        self.assertIsNone(result.git)

    async def test_backend_raises(self) -> None:
        """Docker and K8s raise transport errors out of ``exec_shell``.

        Every other backend converts failures into a result, so this is
        the path that would 500 without an explicit guard.
        """
        result = await self._call(RuntimeError("container is gone"))

        self.assertIsNone(result.git)
        self.assertEqual(result.cwd, "/workspace")

    async def test_shortstat_failure_keeps_the_branch(self) -> None:
        """An unborn HEAD breaks ``diff`` but not ``status``.

        Running the two separately is what lets the branch survive; the
        line counts fall back to zero.
        """
        result = await self._call(
            ExecResult(exit_code=0, stdout=_STATUS_STDOUT, stderr=b""),
            ExecResult(
                exit_code=128,
                stdout=b"",
                stderr=b"fatal: bad revision 'HEAD'",
            ),
        )

        self.assertIsNotNone(result.git)
        self.assertEqual(result.git.branch, "main")
        self.assertEqual(result.git.insertions, 0)
        self.assertEqual(result.git.deletions, 0)

    async def test_shortstat_raises_keeps_the_branch(self) -> None:
        """Same, when the second call raises instead of exiting."""
        result = await self._call(
            ExecResult(exit_code=0, stdout=_STATUS_STDOUT, stderr=b""),
            RuntimeError("connection reset"),
        )

        self.assertIsNotNone(result.git)
        self.assertEqual(result.git.branch, "main")

    async def test_unparseable_output_reports_nothing(self) -> None:
        """Exiting zero is not enough — the output has to name a branch.

        Real git always gives one or a commit, so neither means we are
        not reading git, and a badge with no branch on it would be
        worse than no badge.
        """
        result = await self._call(
            ExecResult(exit_code=0, stdout=b"something else\0", stderr=b""),
        )

        self.assertIsNone(result.git)

    async def test_missing_session_raises_404(self) -> None:
        """An unknown session is a client error, not an empty status."""
        with self.assertRaises(HTTPException) as ctx:
            await get_workspace_status(
                agent_id="a",
                session_id="does-not-exist",
                user_id="u",
                workspace_service=WorkspaceService(
                    _FakeStorage(_make_session()),
                    _FakeWorkspaceManager(_FakeWorkspace(_FakeBackend())),
                    "secret",
                ),
            )

        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)
