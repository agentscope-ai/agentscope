# -*- coding: utf-8 -*-
"""Tests for the workspace file-browsing endpoints.

These are lightweight unit tests that do not require ``create_app``.
Dependencies are injected by calling the endpoint coroutines directly,
which avoids pulling a full FastAPI / Redis stack.
"""

from __future__ import annotations

import posixpath
import time
from typing import AsyncIterator
from unittest import IsolatedAsyncioTestCase

from fastapi import HTTPException, status

from agentscope.app._router._workspace import (
    create_download_token,
    list_workspace_directory,
    read_workspace_file,
)
from agentscope.app._service import WorkspaceService
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.state import AgentState
from agentscope.tool import DirEntry

SECRET = "test-signing-secret"

# ---------------------------------------------------------------------------
# Fake backend + workspace used by endpoint-level tests
# ---------------------------------------------------------------------------


class _FakeBackend:
    """A tiny in-memory filesystem — posix paths only."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = {"/workspace"}
        self.read_paths: list[str] = []
        for p, data in (files or {}).items():
            self._files[p] = data
            self._ensure_parent_dirs(p)

    def join_path(self, path: str, *paths: str) -> str:
        """Join posix-style path components for the in-memory backend."""
        return posixpath.join(path, *paths)

    def _ensure_parent_dirs(self, file_path: str) -> None:
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
        out: set[str] = set()
        for tracked in list(self._files) + list(self._dirs):
            if posixpath.dirname(tracked) == path:
                name = posixpath.basename(tracked)
                if name:
                    out.add(name)
        return sorted(out)

    async def read_file(self, path: str) -> bytes:
        """Return the raw bytes stored for *path*."""
        self.read_paths.append(path)
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    async def read_stream(
        self,
        path: str,
        chunk_size: int = 4,
    ) -> AsyncIterator[bytes]:
        """Yield the stored bytes in small chunks, like a real backend."""
        data = await self.read_file(path)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    def isabs(self, path: str) -> bool:
        """Return whether *path* is absolute under POSIX semantics."""
        return posixpath.isabs(path)

    def normpath(self, path: str) -> str:
        """Normalize a POSIX path."""
        return posixpath.normpath(path)

    def abspath(self, path: str, *, cwd: str) -> str:
        """Resolve a relative POSIX path against *cwd*."""
        return posixpath.normpath(
            path if posixpath.isabs(path) else posixpath.join(cwd, path),
        )

    def basename(self, path: str) -> str:
        """Return the final POSIX path component."""
        return posixpath.basename(path)

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

    async def stat(self, path: str) -> DirEntry | None:
        """Return one path's metadata, or None when it is not tracked."""
        is_dir = path in self._dirs
        if not is_dir and path not in self._files:
            return None
        return DirEntry(
            name=posixpath.basename(path),
            is_dir=is_dir,
            size_bytes=None if is_dir else len(self._files[path]),
            mtime=1_700_000_000.0,
        )

    async def scandir(self, path: str) -> list[DirEntry]:
        """Return each child with its metadata, as one batch would."""
        entries = []
        for name in await self.list_dir(path):
            child = posixpath.join(path, name)
            is_dir = child in self._dirs
            entries.append(
                DirEntry(
                    name=name,
                    is_dir=is_dir,
                    size_bytes=(
                        None if is_dir else len(self._files.get(child, b""))
                    ),
                    mtime=1_700_000_000.0,
                ),
            )
        return entries


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


async def _collect(response: object) -> bytes:
    """Drain a StreamingResponse body into bytes."""
    chunks = [chunk async for chunk in response.body_iterator]
    return b"".join(
        c.encode("utf-8") if isinstance(c, str) else c for c in chunks
    )


# ---------------------------------------------------------------------------
# Download tokens
# ---------------------------------------------------------------------------


class DownloadTokenTests(IsolatedAsyncioTestCase):
    """Unit tests for the capability token itself."""

    def setUp(self) -> None:
        """A service bound to the test secret; storage is never touched."""
        self.service = WorkspaceService(None, None, SECRET)
        self.other = WorkspaceService(None, None, "other-secret")

    def test_roundtrip_returns_the_user(self) -> None:
        """A freshly minted token verifies back to who it was for."""
        token, expires_at = self.service.sign_download_token(
            "alice",
            "/w/a.txt",
        )
        self.assertGreater(expires_at, time.time())
        self.assertEqual(
            self.service.verify_download_token(token, "/w/a.txt"),
            "alice",
        )

    def test_token_does_not_cover_another_path(self) -> None:
        """The path is re-derived from the request, so replay fails."""
        token, _ = self.service.sign_download_token("alice", "/w/a.txt")
        with self.assertRaises(ValueError):
            self.service.verify_download_token(token, "/w/secret.txt")

    def test_another_secret_is_rejected(self) -> None:
        """A token minted elsewhere must not verify here."""
        token, _ = self.other.sign_download_token("alice", "/w/a.txt")
        with self.assertRaises(ValueError):
            self.service.verify_download_token(token, "/w/a.txt")

    def test_expired_token_is_rejected(self) -> None:
        """A TTL that has already passed makes the token useless."""
        token, _ = self.service.sign_download_token(
            "alice",
            "/w/a.txt",
            ttl=-1,
        )
        with self.assertRaises(ValueError):
            self.service.verify_download_token(token, "/w/a.txt")

    def test_garbage_is_rejected(self) -> None:
        """Malformed input must raise, not crash with an index error."""
        for bad in ("", "not-a-token", "1.2", "x.y.z", "9999999999.YQ.YQ"):
            with self.assertRaises(ValueError):
                self.service.verify_download_token(bad, "/w/a.txt")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class WorkspaceFileEndpointTests(IsolatedAsyncioTestCase):
    """Call the file-browsing endpoints directly, bypassing FastAPI."""

    async def asyncSetUp(self) -> None:
        files = {
            "/workspace/notes.txt": b"hello world",
            "/workspace/subdir/report.md": b"# report\n\nbody\n",
            "/elsewhere/outside.txt": b"outside the workspace",
        }
        self._backend = _FakeBackend(files=files)
        self._workspace = _FakeWorkspace(self._backend)
        self._wm = _FakeWorkspaceManager(self._workspace)
        self._storage = _FakeStorage([_make_session()])
        self._service = WorkspaceService(self._storage, self._wm, SECRET)

    # ------------------------------------------------------------------
    # directories
    # ------------------------------------------------------------------

    async def test_list_root(self) -> None:
        """The workspace root lists its seeded files and directories."""
        listing = await list_workspace_directory(
            agent_id="a",
            session_id="s",
            path="",
            user_id="u",
            workspace_service=self._service,
        )
        self.assertEqual(listing.path, "/workspace")
        by_name = {e.name: e for e in listing.entries}
        self.assertEqual(sorted(by_name), ["notes.txt", "subdir"])
        self.assertFalse(by_name["notes.txt"].is_dir)
        self.assertEqual(by_name["notes.txt"].size_bytes, len(b"hello world"))
        self.assertEqual(by_name["notes.txt"].updated_at, 1_700_000_000.0)
        self.assertTrue(by_name["subdir"].is_dir)
        self.assertIsNone(by_name["subdir"].size_bytes)
        # Listing must not read file contents, or a directory of large
        # files would pull all of them into memory.
        self.assertEqual(self._backend.read_paths, [])

    async def test_relative_path_resolves_against_workdir(self) -> None:
        """A relative path is still convenient, and still works."""
        listing = await list_workspace_directory(
            agent_id="a",
            session_id="s",
            path="subdir",
            user_id="u",
            workspace_service=self._service,
        )
        self.assertEqual([e.name for e in listing.entries], ["report.md"])

    async def test_absolute_path_outside_workspace_is_allowed(self) -> None:
        """Browsing is not confined to the workspace root."""
        listing = await list_workspace_directory(
            agent_id="a",
            session_id="s",
            path="/elsewhere",
            user_id="u",
            workspace_service=self._service,
        )
        self.assertEqual([e.name for e in listing.entries], ["outside.txt"])

    async def test_parent_traversal_is_allowed(self) -> None:
        """``..`` is an ordinary path component now, not an attack."""
        listing = await list_workspace_directory(
            agent_id="a",
            session_id="s",
            path="../elsewhere",
            user_id="u",
            workspace_service=self._service,
        )
        self.assertEqual([e.name for e in listing.entries], ["outside.txt"])

    async def test_missing_session_raises_404(self) -> None:
        """An unknown session id must return a 404 HTTP error."""
        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_directory(
                agent_id="a",
                session_id="does-not-exist",
                path="",
                user_id="u",
                workspace_service=self._service,
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_missing_directory_raises_404(self) -> None:
        """Listing a path that does not exist raises a 404 error."""
        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_directory(
                agent_id="a",
                session_id="s",
                path="missing-dir",
                user_id="u",
                workspace_service=self._service,
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_listing_a_file_raises_400(self) -> None:
        """Listing a regular file is a client mistake, not a 404."""
        with self.assertRaises(HTTPException) as ctx:
            await list_workspace_directory(
                agent_id="a",
                session_id="s",
                path="notes.txt",
                user_id="u",
                workspace_service=self._service,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------

    async def _read(self, **kwargs: object) -> object:
        """Call the file endpoint with the shared fixtures filled in."""
        return await read_workspace_file(
            agent_id="a",
            session_id="s",
            workspace_service=self._service,
            **kwargs,
        )

    async def test_read_streams_content_with_inferred_type(self) -> None:
        """The body arrives in chunks, typed from the file extension."""
        response = await self._read(
            path="notes.txt",
            download=False,
            token=None,
            x_user_id="u",
        )
        self.assertEqual(response.media_type, "text/plain")
        # Without this the browser can only show an indeterminate bar.
        self.assertEqual(response.headers["content-length"], "11")
        self.assertNotIn("content-disposition", response.headers)
        self.assertEqual(await _collect(response), b"hello world")

    async def test_unknown_extension_falls_back_to_octet_stream(self) -> None:
        """An unguessable type must not become ``text/plain``."""
        await self._backend.write_file("/workspace/blob.zzz", b"\x00\x01")
        response = await self._read(
            path="blob.zzz",
            download=False,
            token=None,
            x_user_id="u",
        )
        self.assertEqual(response.media_type, "application/octet-stream")

    async def test_download_sets_content_disposition(self) -> None:
        """``download=true`` is what turns a preview into a save."""
        response = await self._read(
            path="notes.txt",
            download=True,
            token=None,
            x_user_id="u",
        )
        self.assertEqual(
            response.headers["content-disposition"],
            "attachment; filename*=UTF-8''notes.txt",
        )

    async def test_absolute_path_outside_workspace_is_readable(self) -> None:
        """Reading, like listing, is not confined to the workspace."""
        response = await self._read(
            path="/elsewhere/outside.txt",
            download=False,
            token=None,
            x_user_id="u",
        )
        self.assertEqual(await _collect(response), b"outside the workspace")

    async def test_missing_file_raises_404(self) -> None:
        """A path with nothing behind it is a 404."""
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="nope.txt",
                download=False,
                token=None,
                x_user_id="u",
            )
        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)

    async def test_reading_a_directory_raises_400(self) -> None:
        """Reading a directory is a client mistake, not a 404."""
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="subdir",
                download=False,
                token=None,
                x_user_id="u",
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    # ------------------------------------------------------------------
    # files — authentication
    # ------------------------------------------------------------------

    async def test_neither_header_nor_token_raises_401(self) -> None:
        """The endpoint takes an optional header, not an absent guard."""
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="notes.txt",
                download=False,
                token=None,
                x_user_id=None,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    async def test_token_stands_in_for_the_header(self) -> None:
        """A minted token authorizes the download it was minted for."""
        minted = await create_download_token(
            agent_id="a",
            session_id="s",
            path="notes.txt",
            user_id="u",
            workspace_service=self._service,
        )
        # Sent back exactly as minted — the token binds the query
        # string, since resolving it needs the user it has yet to yield.
        response = await self._read(
            path="notes.txt",
            download=True,
            token=minted.token,
            x_user_id=None,
        )
        self.assertEqual(await _collect(response), b"hello world")

    async def test_token_does_not_cover_the_resolved_path(self) -> None:
        """Minting relative and downloading absolute is not the same."""
        minted = await create_download_token(
            agent_id="a",
            session_id="s",
            path="notes.txt",
            user_id="u",
            workspace_service=self._service,
        )
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="/workspace/notes.txt",
                download=True,
                token=minted.token,
                x_user_id=None,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    async def test_token_is_bound_to_its_path(self) -> None:
        """A token for one file cannot be replayed against another."""
        minted = await create_download_token(
            agent_id="a",
            session_id="s",
            path="notes.txt",
            user_id="u",
            workspace_service=self._service,
        )
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="/elsewhere/outside.txt",
                download=True,
                token=minted.token,
                x_user_id=None,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    async def test_tampered_token_raises_401(self) -> None:
        """An edited signature must not pass."""
        token, _ = self._service.sign_download_token("u", "notes.txt")
        # Edit the signature's first character, not its last: a 32-byte
        # digest ends on a base64 char carrying only 4 significant bits,
        # so editing that one can decode back to the same digest.
        expiry, user, signature = token.split(".")
        tampered = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaises(HTTPException) as ctx:
            await self._read(
                path="notes.txt",
                download=True,
                token=f"{expiry}.{user}.{tampered}",
                x_user_id=None,
            )
        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
