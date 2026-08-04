# -*- coding: utf-8 -*-
"""Tests for the workspace artifact endpoints (list_dir + read_file).

These are lightweight unit tests that do not require ``create_app``.
Dependencies are injected by wrapping the endpoint coroutines in a small
``Depends``-free shim, which avoids pulling a full FastAPI / Redis stack.
"""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from fastapi import HTTPException, status

from agentscope.app._router._workspace import (
    ArtifactEntry,
    _confine_existing_artifact_path,
    _safe_resolve_artifact_path,
)
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.state import AgentState

# ---------------------------------------------------------------------------
# Fake backend + workspace used by endpoint-level tests
# ---------------------------------------------------------------------------


class _FakeBackend:
    """A tiny in-memory filesystem — posix paths only."""

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        realpaths: dict[str, str] | None = None,
    ) -> None:
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = {"/workspace"}
        self._realpaths = realpaths or {}
        self.read_paths: list[str] = []
        for p, data in (files or {}).items():
            self._files[p] = data
            self._ensure_parent_dirs(p)

    def join_path(self, path: str, *paths: str) -> str:
        """Join posix-style path components for the in-memory backend."""
        import posixpath

        return posixpath.join(path, *paths)

    def _ensure_parent_dirs(self, file_path: str) -> None:
        import posixpath

        cur = posixpath.dirname(file_path)
        while cur and cur not in self._dirs:
            self._dirs.add(cur)
            cur = posixpath.dirname(cur)

    async def is_dir(self, path: str) -> bool:
        """Return True when *path* is a tracked directory."""
        return path in self._dirs

    async def file_exists(self, path: str) -> bool:
        """Return True when *path* is a tracked file or directory."""
        return path in self._files or path in self._dirs

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """Return direct children of *path* sorted lexicographically."""
        _ = recursive
        import posixpath

        out: list[str] = []
        seen: set[str] = set()
        for f in self._files:
            parent = posixpath.dirname(f)
            if parent == path:
                name = posixpath.basename(f)
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
        for d in self._dirs:
            parent = posixpath.dirname(d)
            if parent == path:
                name = posixpath.basename(d)
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
        return sorted(out)

    async def read_file(self, path: str) -> bytes:
        """Return the raw bytes stored for *path*."""
        self.read_paths.append(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def isabs(self, path: str) -> bool:
        """Return whether *path* is absolute under POSIX semantics."""
        import posixpath

        return posixpath.isabs(path)

    def normpath(self, path: str) -> str:
        """Normalize a POSIX path."""
        import posixpath

        return posixpath.normpath(path)

    def abspath(self, path: str, *, cwd: str) -> str:
        """Resolve a relative POSIX path against *cwd*."""
        import posixpath

        return posixpath.normpath(
            path if posixpath.isabs(path) else posixpath.join(cwd, path),
        )

    def basename(self, path: str) -> str:
        """Return the final POSIX path component."""
        import posixpath

        return posixpath.basename(path)

    async def realpath(self, path: str) -> str:
        """Resolve a seeded symbolic-link target."""
        if path not in self._files and path not in self._dirs:
            raise FileNotFoundError(path)
        return self._realpaths.get(path, path)

    def add_symlink(self, path: str, target: str) -> None:
        """Seed a symbolic link for confinement tests."""
        self._files[path] = b"symlink-placeholder"
        self._realpaths[path] = target
        self._ensure_parent_dirs(path)

    async def write_file(self, path: str, data: bytes) -> bytes | None:
        """Write *data* to *path* and ensure parent dirs exist."""
        self._files[path] = data
        self._ensure_parent_dirs(path)
        return None

    async def stat_mtime(self, path: str) -> float | None:
        """Return a deterministic mtime value for tracked paths."""
        if path in self._files or path in self._dirs:
            return 1_700_000_000.0
        return None

    async def stat_size(self, path: str) -> int | None:
        """Return the byte length of a tracked file."""
        data = self._files.get(path)
        return None if data is None else len(data)


class _FakeWorkspace:
    def __init__(self, backend: _FakeBackend) -> None:
        self.workdir = "/workspace"
        self._backend = backend

    def get_backend(self) -> _FakeBackend:
        """Expose the injected in-memory backend."""
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
    def __init__(self, records: list[SessionRecord]) -> None:
        self._records = {(r.user_id, r.agent_id, r.id): r for r in records}

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Look up a seeded session record by composite key."""
        return self._records.get((user_id, agent_id, session_id))


def _make_session(
    *,
    user_id: str = "u",
    agent_id: str = "a",
    session_id: str = "s",
    workspace_id: str = "ws-1",
) -> SessionRecord:
    return SessionRecord(
        user_id=user_id,
        agent_id=agent_id,
        id=session_id,
        source=SessionSource.USER,
        state=AgentState(),
        config=SessionConfig(
            workspace_id=workspace_id,
            name="test session",
        ),
        agent_snapshot=AgentRecord(
            user_id=user_id,
            source="user",
            data=AgentData(
                name="A",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Tests for the path-confinement helper
# ---------------------------------------------------------------------------


class SafeArtifactPathTests(IsolatedAsyncioTestCase):
    """Unit tests for :func:`_safe_resolve_artifact_path`."""

    def setUp(self) -> None:
        self._backend = _FakeBackend()

    def test_root_is_accepted(self) -> None:
        """Verify empty / dot paths resolve to the workspace root."""
        self.assertEqual(
            _safe_resolve_artifact_path(self._backend, "/workspace", ""),
            "/workspace",
        )
        self.assertEqual(
            _safe_resolve_artifact_path(self._backend, "/workspace", "."),
            "/workspace",
        )

    def test_nested_directory(self) -> None:
        """A nested relative path stays within the workspace root."""
        self.assertEqual(
            _safe_resolve_artifact_path(
                self._backend,
                "/workspace",
                "sessions/s1/context.jsonl",
            ),
            "/workspace/sessions/s1/context.jsonl",
        )

    def test_absolute_path_raises(self) -> None:
        """Absolute paths are rejected rather than silently re-rooted."""
        with self.assertRaises(HTTPException) as ctx:
            _safe_resolve_artifact_path(
                self._backend,
                "/workspace",
                "/etc/passwd",
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_dotdot_escape_raises(self) -> None:
        """A single ``..`` escape step raises a 400 HTTP error."""
        with self.assertRaises(HTTPException) as ctx:
            _safe_resolve_artifact_path(
                self._backend,
                "/workspace",
                "../etc/passwd",
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("escapes", ctx.exception.detail.lower())

    def test_deeper_dotdot_escape_raises(self) -> None:
        """Deep ``../`` sequences must also be rejected with HTTP 400."""
        with self.assertRaises(HTTPException):
            _safe_resolve_artifact_path(
                self._backend,
                "/workspace",
                "a/../../../../etc/passwd",
            )

    def test_dot_components_are_collapsed_without_escape(self) -> None:
        """In-workspace ``.`` / ``..`` components collapse normally."""
        self.assertEqual(
            _safe_resolve_artifact_path(
                self._backend,
                "/workspace",
                "a/./b/../c.txt",
            ),
            "/workspace/a/c.txt",
        )

    def test_backslash_traversal_is_stripped(self) -> None:
        """Windows-style backslash paths cannot escape the workspace."""
        resolved = _safe_resolve_artifact_path(
            self._backend,
            "/workspace",
            "\\windows\\system32",
        )
        self.assertTrue(resolved.startswith("/workspace/"))

    async def test_symlink_escape_raises(self) -> None:
        """An in-root symlink cannot redirect reads outside the workspace."""
        backend = _FakeBackend(
            files={"/workspace/link": b"secret"},
            realpaths={
                "/workspace": "/workspace",
                "/workspace/link": "/etc/passwd",
            },
        )
        with self.assertRaises(HTTPException) as ctx:
            await _confine_existing_artifact_path(
                backend,
                "/workspace",
                "/workspace/link",
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )


# ---------------------------------------------------------------------------
# Endpoint-level tests (invoke endpoint coroutines with dependency args)
# ---------------------------------------------------------------------------


class ArtifactEndpointTests(IsolatedAsyncioTestCase):
    """Call the two artifact endpoints directly, bypassing FastAPI."""

    async def asyncSetUp(self) -> None:
        files = {
            "/workspace/notes.txt": b"hello world",
            "/workspace/subdir/report.md": b"# report\n\nbody\n",
        }
        self._backend = _FakeBackend(files=files)
        self._workspace = _FakeWorkspace(self._backend)
        self._wm = _FakeWorkspaceManager(self._workspace)
        self._session = _make_session()
        self._storage = _FakeStorage([self._session])

    # ------------------------------------------------------------------
    # list_dir
    # ------------------------------------------------------------------

    async def test_list_dir_root(self) -> None:
        """``list_dir`` on the workspace root returns seed files + dirs."""
        from agentscope.app._router._workspace import list_workspace_dir

        entries: list[ArtifactEntry] = await list_workspace_dir(
            agent_id="a",
            session_id="s",
            path="",
            user_id="u",
            storage=self._storage,
            workspace_manager=self._wm,
        )
        names = sorted(e.name for e in entries)
        self.assertIn("notes.txt", names)
        self.assertIn("subdir", names)
        notes = next(e for e in entries if e.name == "notes.txt")
        self.assertFalse(notes.is_dir)
        self.assertEqual(notes.size_bytes, len(b"hello world"))
        self.assertEqual(self._backend.read_paths, [])
        subdir = next(e for e in entries if e.name == "subdir")
        self.assertTrue(subdir.is_dir)

    async def test_list_dir_missing_session_raises_404(self) -> None:
        """An unknown session id must return a 404 HTTP error."""
        from agentscope.app._router._workspace import list_workspace_dir

        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_dir(
                agent_id="a",
                session_id="does-not-exist",
                path="",
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_list_dir_nonexistent_path_raises_404(self) -> None:
        """Listing a path that is not a directory raises a 404 error."""
        from agentscope.app._router._workspace import list_workspace_dir

        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_dir(
                agent_id="a",
                session_id="s",
                path="missing-dir",
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_list_dir_on_file_raises_400(self) -> None:
        """``list_dir`` over a regular file raises a 400 HTTP error."""
        from agentscope.app._router._workspace import list_workspace_dir

        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_dir(
                agent_id="a",
                session_id="s",
                path="notes.txt",
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    async def test_list_dir_path_escape_raises_400(self) -> None:
        """``list_dir`` must reject ``../`` escapes with a 400 error."""
        from agentscope.app._router._workspace import list_workspace_dir

        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_dir(
                agent_id="a",
                session_id="s",
                path="../",
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    async def test_list_dir_omits_symlink_escape(self) -> None:
        """A child symlink resolving outside the workspace is not listed."""
        self._backend.add_symlink("/workspace/outside-link", "/etc/passwd")

        from agentscope.app._router._workspace import list_workspace_dir

        entries = await list_workspace_dir(
            agent_id="a",
            session_id="s",
            path="",
            user_id="u",
            storage=self._storage,
            workspace_manager=self._wm,
        )
        self.assertNotIn("outside-link", [entry.name for entry in entries])

    # ------------------------------------------------------------------
    # read_file
    # ------------------------------------------------------------------

    async def test_read_file_returns_bytes_and_inferred_content_type(
        self,
    ) -> None:
        """Text assets come back as bytes with a ``text/plain`` mime type."""
        from agentscope.app._router._workspace import read_workspace_file

        resp = await read_workspace_file(
            agent_id="a",
            session_id="s",
            path="notes.txt",
            download=False,
            user_id="u",
            storage=self._storage,
            workspace_manager=self._wm,
        )
        self.assertEqual(resp.body, b"hello world")
        self.assertEqual(resp.media_type, "text/plain")

    async def test_read_file_unknown_extension_uses_octet_stream(
        self,
    ) -> None:
        """Unknown extensions fall back to ``application/octet-stream``."""
        from agentscope.app._router._workspace import read_workspace_file

        await self._backend.write_file(
            "/workspace/weird.unknown_ext_zzz",
            b"\x00\x01",
        )
        resp = await read_workspace_file(
            agent_id="a",
            session_id="s",
            path="weird.unknown_ext_zzz",
            download=False,
            user_id="u",
            storage=self._storage,
            workspace_manager=self._wm,
        )
        self.assertEqual(resp.media_type, "application/octet-stream")

    async def test_read_file_download_sets_content_disposition(
        self,
    ) -> None:
        """``download=True`` produces an ``attachment`` Content-Disposition."""
        from agentscope.app._router._workspace import read_workspace_file

        resp = await read_workspace_file(
            agent_id="a",
            session_id="s",
            path="notes.txt",
            download=True,
            user_id="u",
            storage=self._storage,
            workspace_manager=self._wm,
        )
        disp = resp.headers.get("content-disposition") or resp.headers.get(
            "Content-Disposition",
        )
        self.assertIsNotNone(disp)
        self.assertIn("notes.txt", disp)
        self.assertIn("attachment", disp)

    async def test_read_file_missing_raises_404(self) -> None:
        """Reading a file that does not exist must raise a 404 error."""
        from agentscope.app._router._workspace import read_workspace_file

        with self.assertRaises(HTTPException) as ctx:
            await read_workspace_file(
                agent_id="a",
                session_id="s",
                path="nope.txt",
                download=False,
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_read_file_on_directory_raises_400(self) -> None:
        """Reading a directory path raises a 400 HTTP error."""
        from agentscope.app._router._workspace import read_workspace_file

        with self.assertRaises(HTTPException) as ctx:
            await read_workspace_file(
                agent_id="a",
                session_id="s",
                path="subdir",
                download=False,
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    async def test_read_file_escape_raises_400(self) -> None:
        """``read_workspace_file`` must reject ``../`` escapes (HTTP 400)."""
        from agentscope.app._router._workspace import read_workspace_file

        with self.assertRaises(HTTPException) as ctx:
            await read_workspace_file(
                agent_id="a",
                session_id="s",
                path="../etc/shadow",
                download=False,
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    async def test_read_file_symlink_escape_raises_400(self) -> None:
        """A symlink cannot redirect artifact reads outside the workspace."""
        self._backend.add_symlink("/workspace/outside-link", "/etc/passwd")

        from agentscope.app._router._workspace import read_workspace_file

        with self.assertRaises(HTTPException) as ctx:
            await read_workspace_file(
                agent_id="a",
                session_id="s",
                path="outside-link",
                download=False,
                user_id="u",
                storage=self._storage,
                workspace_manager=self._wm,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(self._backend.read_paths, [])
