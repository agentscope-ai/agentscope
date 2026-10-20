# -*- coding: utf-8 -*-
"""Tests for the workspace file-browsing endpoints.

These are lightweight unit tests that do not require ``create_app``.
Dependencies are injected by calling the endpoint coroutines directly,
which avoids pulling a full FastAPI / Redis stack.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import ntpath
import os
import posixpath
import sys
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from agentscope.app._router._workspace import (
    create_download_token,
    list_workspace_directory,
    read_workspace_file,
    read_workspace_text,
    save_workspace_text,
    workspace_router,
)
from agentscope.app._service import WorkspaceService
from agentscope.app._service._workspace import (
    MAX_EDIT_BYTES,
    TextFileSaveRequest,
    TextFileSnapshot,
)
from agentscope.app.deps import get_workspace_service
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    UserOrigin,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.state import AgentState
from agentscope.tool import BackendBase, DirEntry, ExecResult, LocalBackend
from agentscope.tool._builtin._backend import PathResolutionUnavailable
from agentscope.workspace import LocalWorkspace

SECRET = "test-signing-secret"

# ---------------------------------------------------------------------------
# Fake backend + workspace used by endpoint-level tests
# ---------------------------------------------------------------------------


class _FakeBackend:
    """A tiny in-memory filesystem — posix paths only."""

    _path_module = posixpath
    home = "/home/test"

    async def realpath(self, path: str) -> str:
        """Return the path; symlink behavior is covered with real backends."""
        return self.normpath(path)

    async def realpaths(
        self,
        paths: list[str],
        *,
        include_home: bool = False,
    ) -> list[str]:
        """Permit individual alias overrides in filesystem tests."""
        if include_home and self.isabs(self.home):
            paths = [*paths, self.home]
        return [await self.realpath(path) for path in paths]

    normpath = BackendBase.normpath
    path_key = BackendBase.path_key
    is_within = BackendBase.is_within
    dirname = BackendBase.dirname
    isabs = BackendBase.isabs
    abspath = BackendBase.abspath

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = {"/workspace"}
        self.read_paths: list[str] = []
        for p, data in (files or {}).items():
            self._files[p] = data
            self._ensure_parent_dirs(p)

    def join_path(self, path: str, *paths: str) -> str:
        """Use the configured target OS path rules."""
        return self._path_module.join(path, *paths)

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

    def basename(self, path: str) -> str:
        """Return the final POSIX path component."""
        return posixpath.basename(path)

    async def write_file(self, path: str, data: bytes) -> bytes | None:
        """Write *data* to *path* and ensure parent dirs exist."""
        self._files[path] = data
        self._ensure_parent_dirs(path)
        return None

    async def delete_file(self, path: str) -> None:
        """Remove a seeded file to simulate an external deletion."""
        del self._files[path]

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
        self.workspace_id = "ws-1"
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
        origin=UserOrigin(),
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


class _WorkspaceFileFixture(IsolatedAsyncioTestCase):
    """Shared isolated workspace for browsing and editing endpoints."""

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


class WorkspaceFileEndpointTests(_WorkspaceFileFixture):
    """Call the file-browsing endpoints directly, bypassing FastAPI."""

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


class _WorkspaceTextFixture(_WorkspaceFileFixture):
    """Shared helpers for editor endpoint tests."""

    async def _text(
        self,
        path: str = "notes.txt",
        **kwargs: str,
    ) -> TextFileSnapshot:
        """Read an editor snapshot using the test session."""
        return await read_workspace_text(
            agent_id=kwargs.get("agent_id", "a"),
            session_id=kwargs.get("session_id", "s"),
            path=path,
            user_id=kwargs.get("user_id", "u"),
            workspace_service=self._service,
        )

    async def _save(
        self,
        text: str,
        revision: str,
        **kwargs: str,
    ) -> TextFileSnapshot:
        """Save through the endpoint with a required revision."""
        return await save_workspace_text(
            body=TextFileSaveRequest(text=text, revision=revision),
            agent_id=kwargs.get("agent_id", "a"),
            session_id=kwargs.get("session_id", "s"),
            path=kwargs.get("path", "notes.txt"),
            user_id=kwargs.get("user_id", "u"),
            workspace_service=self._service,
        )


class WorkspaceTextEndpointTests(_WorkspaceTextFixture):
    """Validate revision-checked editing with an in-memory backend."""

    async def test_http_text_contract(self) -> None:
        """HTTP validation, authorization and conflict codes are preserved."""
        app = FastAPI()
        app.include_router(workspace_router)
        app.dependency_overrides[get_workspace_service] = lambda: self._service
        params = {"agent_id": "a", "session_id": "s", "path": "notes.txt"}
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/workspace/files/text", params=params)
            # Existing required-header validation returns 422 before auth.
            self.assertEqual(response.status_code, 422)
            headers = {"X-User-ID": "u"}
            response = await client.get(
                "/workspace/files/text",
                params=params,
                headers=headers,
            )
            original = response.json()
            self.assertEqual(
                (response.status_code, original),
                (200, (await self._text()).model_dump()),
            )
            for body, expected in (
                ({"text": "draft"}, 422),
                ({"text": "draft", "revision": "bad"}, 422),
                ({"text": "draft", "revision": original["revision"]}, 200),
                ({"text": "late", "revision": original["revision"]}, 409),
            ):
                response = await client.put(
                    "/workspace/files/text",
                    params=params,
                    headers=headers,
                    json=body,
                )
                self.assertEqual(response.status_code, expected)
                if expected == 409:
                    self.assertEqual(
                        response.json(),
                        {
                            "detail": {
                                "code": "file_changed",
                                "message": (
                                    "File changed since it was opened. "
                                    "Reload before saving."
                                ),
                            },
                        },
                    )
            response = await client.put(
                "/workspace/files/text",
                params={**params, "token": "download-only"},
                json={"text": "bad", "revision": original["revision"]},
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(
            await self._backend.read_file("/workspace/notes.txt"),
            b"draft",
        )

    async def test_editor_uses_backend_windows_path_rules(self) -> None:
        """Resolve Windows paths through the backend, not the API host."""
        path = "C:\\work\\报告 #1%.txt"
        await self._backend.write_file(path, b"before")
        with (
            patch.object(self._workspace, "workdir", "C:\\work"),
            patch.object(self._backend, "_path_module", ntpath),
            patch.object(
                self._backend,
                "abspath",
                side_effect=lambda value, *, cwd: ntpath.normpath(
                    ntpath.join(cwd, value),
                ),
            ),
        ):
            original = await self._text("报告 #1%.txt")
            saved = await self._save(
                "after",
                original.revision,
                path=original.path,
            )
        self.assertEqual(
            saved.model_dump(),
            {
                "path": path,
                "text": "after",
                "revision": hashlib.sha256(b"after").hexdigest(),
            },
        )

    async def test_text_snapshot_and_repeated_saves(self) -> None:
        """A successful save returns the next usable content revision."""
        original = await self._text()
        self.assertEqual(
            original.model_dump(),
            {
                "path": "/workspace/notes.txt",
                "text": "hello world",
                "revision": hashlib.sha256(b"hello world").hexdigest(),
            },
        )
        for text in ("你好\r\n", ""):
            saved = await self._save(text, original.revision)
            self.assertEqual(
                saved.model_dump(),
                {
                    "path": "/workspace/notes.txt",
                    "text": text,
                    "revision": hashlib.sha256(
                        text.encode("utf-8"),
                    ).hexdigest(),
                },
            )
            self.assertEqual(
                await self._backend.read_file("/workspace/notes.txt"),
                text.encode("utf-8"),
            )
            original = saved

    async def test_external_edit_conflicts_without_overwrite(self) -> None:
        """A same-length edit with unchanged mtime still conflicts."""
        original = await self._text()
        await self._backend.write_file(
            "/workspace/notes.txt",
            b"other text!",
        )
        with self.assertRaises(HTTPException) as ctx:
            await self._save("my draft", original.revision)
        self.assertEqual(
            (
                ctx.exception.status_code,
                await self._backend.read_file("/workspace/notes.txt"),
            ),
            (409, b"other text!"),
        )

    async def test_same_service_editor_saves_conflict(self) -> None:
        """Sessions sharing storage serialize checks within one service."""
        self._service = WorkspaceService(
            _FakeStorage([_make_session(), _make_session(session_id="s2")]),
            self._wm,
            SECRET,
        )
        original = await self._text()
        results = await asyncio.gather(
            self._save("first", original.revision),
            self._save("second", original.revision, session_id="s2"),
            return_exceptions=True,
        )
        self.assertEqual(
            [
                r.status_code if isinstance(r, HTTPException) else r.text
                for r in results
            ],
            ["first", 409],
        )
        self.assertEqual(
            await self._backend.read_file("/workspace/notes.txt"),
            b"first",
        )

    async def test_text_access_requires_owned_session(self) -> None:
        """Both editor endpoints enforce user, agent and session access."""
        original = await self._text()
        for kwargs in (
            {"user_id": "other"},
            {"agent_id": "other"},
            {"session_id": "other"},
        ):
            for operation in (
                self._text(**kwargs),
                self._save("draft", original.revision, **kwargs),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await operation
                self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(
            await self._backend.read_file("/workspace/notes.txt"),
            b"hello world",
        )

    async def test_text_rejects_missing_directory_and_binary(self) -> None:
        """Invalid targets never become editable text."""
        for path, data, expected in (
            ("missing.txt", None, 404),
            ("subdir", None, 400),
            ("invalid.txt", b"\xff", 415),
            ("binary.txt", b"a\0b", 415),
            ("large.txt", b"a" * (MAX_EDIT_BYTES + 1), 413),
        ):
            if data is not None:
                await self._backend.write_file(
                    f"/workspace/{path}",
                    data,
                )
            with self.assertRaises(HTTPException) as ctx:
                await self._text(path)
            self.assertEqual(ctx.exception.status_code, expected)

    async def test_text_preserves_bom_and_special_path(self) -> None:
        """Snapshots preserve BOM, CRLF and URL-special path characters."""
        path = "/workspace/报告 #1%25.txt"
        text = "\ufeff报告\r\n"
        await self._backend.write_file(path, text.encode("utf-8"))
        original = await self._text(path)
        saved = await self._save(
            f"{text}new\r\n",
            original.revision,
            path=path,
        )
        self.assertEqual(
            saved.model_dump(),
            {
                "path": path,
                "text": f"{text}new\r\n",
                "revision": hashlib.sha256(
                    f"{text}new\r\n".encode("utf-8"),
                ).hexdigest(),
            },
        )

    async def test_save_rejects_invalid_content_and_deleted_file(self) -> None:
        """Failed saves preserve content and do not recreate deleted files."""
        original = await self._text()
        for text, expected in (
            ("a\0b", 415),
            ("中" * (MAX_EDIT_BYTES // 2), 413),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._save(text, original.revision)
            self.assertEqual(ctx.exception.status_code, expected)
            self.assertEqual(
                await self._backend.read_file("/workspace/notes.txt"),
                b"hello world",
            )
        await self._backend.delete_file("/workspace/notes.txt")
        with self.assertRaises(HTTPException) as ctx:
            await self._save("draft", original.revision)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertFalse(
            await self._backend.file_exists("/workspace/notes.txt"),
        )

    async def test_text_bounds_stream_when_size_is_unknown(self) -> None:
        """Streaming stops at the limit and closes the reader."""
        closed = []

        async def oversized(*_args: object) -> AsyncIterator[bytes]:
            try:
                yield b"a" * MAX_EDIT_BYTES
                yield b"b"
                self.fail("Read past the editing limit.")
            finally:
                closed.append(True)

        entry = DirEntry(
            name="notes.txt",
            is_dir=False,
            size_bytes=None,
            mtime=None,
        )
        with (
            patch.object(
                self._backend,
                "stat",
                AsyncMock(return_value=entry),
            ),
            patch.object(self._backend, "read_stream", oversized),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._text()
        self.assertEqual((ctx.exception.status_code, closed), (413, [True]))

    async def test_save_permission_failure_preserves_content(self) -> None:
        """A rejected backend write is reported without a success response."""
        original = await self._text()
        with patch.object(
            self._backend,
            "write_file",
            AsyncMock(side_effect=PermissionError),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._save("draft", original.revision)
        self.assertEqual(
            (
                ctx.exception.status_code,
                await self._backend.read_file("/workspace/notes.txt"),
            ),
            (403, b"hello world"),
        )

    def test_text_save_schema_rejects_invalid_input(self) -> None:
        """The HTTP request schema rejects invalid revisions and text."""
        for text, revision in (
            ("new", ""),
            ("new", "invalid"),
            ("\ud800", "a" * 64),
            ("a" * (MAX_EDIT_BYTES + 1), "a" * 64),
        ):
            with self.assertRaises(ValidationError):
                TextFileSaveRequest(text=text, revision=revision)


class WorkspaceEditorSafetyTests(_WorkspaceTextFixture):
    """Exercise editor boundaries and process-local concurrency."""

    async def test_broad_cwd_roots_do_not_add_edit_grants(self) -> None:
        """Reject POSIX, drive, UNC and home cwd roots across target OSes."""
        record = await self._storage.get_session("u", "a", "s")
        cases = (
            (posixpath, "/", "/home/test", "/elsewhere/outside.txt"),
            (posixpath, "/home/test/", "/home/test", "/home/test/.bashrc"),
            (ntpath, "C:\\", "C:\\Users\\test", "C:\\Windows\\config"),
            (
                ntpath,
                "\\\\server\\share\\",
                "C:\\Users\\test",
                "\\\\server\\share\\config",
            ),
            (
                ntpath,
                "c:\\users\\TEST\\",
                "C:\\Users\\test",
                "C:\\Users\\test\\config",
            ),
        )
        for module, cwd, home, path in cases:
            record.config.cwd = cwd
            root = "/workspace" if module is posixpath else "C:\\workspace"
            with (
                patch.object(self._backend, "_path_module", module),
                patch.object(self._backend, "home", home),
                patch.object(self._workspace, "workdir", root),
            ):
                access = await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    path,
                )
                self.assertEqual(
                    access.model_dump(),
                    {"editable": False, "reason": "outside_edit_roots"},
                )
                with self.assertRaises(HTTPException) as ctx:
                    await self._save("bad", "a" * 64, path=path)
                self.assertEqual(
                    (ctx.exception.status_code, ctx.exception.detail["code"]),
                    (403, "outside_edit_roots"),
                )
                # Excluding cwd must not revoke the configured workspace.
                inside = await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    self._backend.join_path(root, "notes.txt"),
                )
                self.assertEqual(
                    inside.model_dump(),
                    {"editable": True, "reason": None},
                )

    async def test_specific_project_and_configured_root_remain_allowed(
        self,
    ) -> None:
        """The guard prevents mistakes; it does not isolate host access."""
        record = await self._storage.get_session("u", "a", "s")
        for cwd, path in (
            ("/home/test/project", "/home/test/project/main.py"),
            ("/etc", "/etc/config"),
        ):
            record.config.cwd = cwd
            access = await self._service.file_edit_access("u", "a", "s", path)
            self.assertEqual(
                access.model_dump(),
                {"editable": True, "reason": None},
            )
        for root in ("/", "/home/test"):
            record.config.cwd = None
            with patch.object(self._workspace, "workdir", root):
                access = await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    f"{root.rstrip('/')}/config",
                )
                self.assertEqual(
                    access.model_dump(),
                    {"editable": True, "reason": None},
                )

    async def test_cwd_alias_to_home_does_not_grant_access(self) -> None:
        """Apply the home guard after resolving directory symlinks."""
        record = await self._storage.get_session("u", "a", "s")
        record.config.cwd = "/project-alias"
        with patch.object(
            self._backend,
            "realpath",
            AsyncMock(
                side_effect=lambda path: (
                    "/home/test" if path == "/project-alias" else path
                ),
            ),
        ):
            access = await self._service.file_edit_access(
                "u",
                "a",
                "s",
                "/home/test/.bashrc",
            )
        self.assertEqual(
            access.model_dump(),
            {"editable": False, "reason": "outside_edit_roots"},
        )

    async def test_equivalent_cwd_is_not_resolved_twice(self) -> None:
        """Trailing separators do not add a root or a home lookup."""
        record = await self._storage.get_session("u", "a", "s")
        record.config.cwd = "/workspace"
        with (
            patch.object(self._workspace, "workdir", "/workspace/"),
            patch.object(
                self._backend,
                "realpaths",
                wraps=self._backend.realpaths,
            ) as resolve,
        ):
            self.assertEqual((await self._text()).text, "hello world")
        resolve.assert_awaited_once_with(
            ["/workspace/", "/workspace/notes.txt"],
            include_home=False,
        )

    async def test_unknown_home_preserves_access_and_root_guard(self) -> None:
        """Unknown home must not break workdir or bypass other restrictions."""
        record = await self._storage.get_session("u", "a", "s")
        with patch.object(self._backend, "home", "~"):
            for cwd, allowed in (("/elsewhere", True), ("/", False)):
                record.config.cwd = cwd
                original = await self._text()
                saved = await self._save(f"cwd={cwd}", original.revision)
                self.assertEqual(
                    saved.model_dump(),
                    {
                        "path": "/workspace/notes.txt",
                        "text": f"cwd={cwd}",
                        "revision": hashlib.sha256(
                            f"cwd={cwd}".encode(),
                        ).hexdigest(),
                    },
                )
                access = await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    "/elsewhere/outside.txt",
                )
                self.assertEqual(
                    access.model_dump(),
                    {
                        "editable": allowed,
                        "reason": None if allowed else "outside_edit_roots",
                    },
                )
                if allowed:
                    original = await self._text("/elsewhere/outside.txt")
                    saved = await self._save(
                        "project",
                        original.revision,
                        path="/elsewhere/outside.txt",
                    )
                    self.assertEqual(saved.text, "project")
                else:
                    with self.assertRaises(HTTPException) as ctx:
                        await self._text("/elsewhere/outside.txt")
                    self.assertEqual(ctx.exception.status_code, 403)
            record.config.cwd = "/elsewhere"
            with self.assertRaises(HTTPException) as ctx:
                await self._text("/unrelated/file.txt")
            self.assertEqual(ctx.exception.status_code, 403)

    async def test_session_cwd_grants_access_only_to_that_project(
        self,
    ) -> None:
        """A persisted external cwd is editable, with canonical containment."""
        record = await self._storage.get_session("u", "a", "s")
        record.config.cwd = "/elsewhere"
        original = await self._text("/elsewhere/outside.txt")
        saved = await self._save(
            "updated",
            original.revision,
            path="/elsewhere/outside.txt",
        )
        self.assertEqual(
            saved.model_dump(),
            {
                "path": "/elsewhere/outside.txt",
                "text": "updated",
                "revision": hashlib.sha256(b"updated").hexdigest(),
            },
        )
        # The workspace remains accessible when cwd is external.
        self.assertEqual((await self._text()).text, "hello world")
        for path in ("/elsewhere-other/a.txt", "/elsewhere/../private.txt"):
            access = await self._service.file_edit_access("u", "a", "s", path)
            self.assertEqual(
                access.model_dump(),
                {"editable": False, "reason": "outside_edit_roots"},
            )
        record.config.cwd = None
        with self.assertRaises(HTTPException) as ctx:
            await self._save(
                "late",
                saved.revision,
                path="/elsewhere/outside.txt",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_edit_access_http_contract(self) -> None:
        """Access hints share server policy and cannot grant a client cwd."""
        app = FastAPI()
        app.include_router(workspace_router)
        app.dependency_overrides[get_workspace_service] = lambda: self._service
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for path, allowed in (
                ("notes.txt", True),
                ("/elsewhere/outside.txt", False),
            ):
                response = await client.get(
                    "/workspace/files/edit-access",
                    params={
                        "agent_id": "a",
                        "session_id": "s",
                        "path": path,
                        "cwd": "/elsewhere",
                    },
                    headers={"X-User-ID": "u"},
                )
                self.assertEqual(
                    (response.status_code, response.json()),
                    (
                        200,
                        {
                            "editable": allowed,
                            "reason": None
                            if allowed
                            else "outside_edit_roots",
                        },
                    ),
                )
            response = await client.get(
                "/workspace/files/edit-access",
                params={
                    "agent_id": "a",
                    "session_id": "s",
                    "path": "notes.txt",
                },
                headers={"X-User-ID": "someone-else"},
            )
            self.assertEqual(response.status_code, 404)
            response = await client.put(
                "/workspace/files/text",
                params={
                    "agent_id": "a",
                    "session_id": "s",
                    "path": "notes.txt",
                },
                headers={"X-User-ID": "u"},
                json={"text": "a\r\nb\n", "revision": "a" * 64},
            )
            self.assertEqual(
                (response.status_code, response.json()),
                (
                    422,
                    {
                        "detail": {
                            "code": "mixed_line_endings",
                            "message": (
                                "Mixed line endings cannot be edited."
                            ),
                        },
                    },
                ),
            )

    async def test_invalid_content_does_not_call_backend(self) -> None:
        """Pure request checks run before resolving or accessing storage."""
        with patch.object(self._service, "_resolve_record") as resolve:
            for text, expected in (
                ("中" * (MAX_EDIT_BYTES // 3 + 1), 413),
                ("a\0", 415),
                ("a\r\nb\n", 422),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await self._save(text, "a" * 64)
                self.assertEqual(ctx.exception.status_code, expected)
            resolve.assert_not_called()

    async def test_path_checks_batch_roots_and_target(self) -> None:
        """Read uses one resolution batch, save uses two fresh batches."""
        record = await self._storage.get_session("u", "a", "s")
        record.config.cwd = "/elsewhere"
        with patch.object(
            self._backend,
            "realpaths",
            wraps=self._backend.realpaths,
        ) as resolve:
            original = await self._text()
            self.assertEqual(
                resolve.await_args_list[0].args,
                (["/workspace", "/elsewhere", "/workspace/notes.txt"],),
            )
            self.assertEqual(resolve.await_count, 1)
            await self._save("updated", original.revision)
            self.assertEqual(resolve.await_count, 3)

    async def test_save_rechecks_session_scope_after_reading(self) -> None:
        """A changed cwd or workspace must not retain an old edit grant."""
        for change in ("cwd", "workspace_id"):
            record = await self._storage.get_session("u", "a", "s")
            record.config.cwd = "/elsewhere"
            original = await self._text("/elsewhere/outside.txt")
            latest = record.model_copy(deep=True)
            setattr(latest.config, change, None)
            with patch.object(
                self._storage,
                "get_session",
                AsyncMock(side_effect=[record, latest]),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await self._save(
                        "bad",
                        original.revision,
                        path="/elsewhere/outside.txt",
                    )
            self.assertEqual(
                (
                    ctx.exception.status_code,
                    ctx.exception.detail,
                    await self._backend.read_file("/elsewhere/outside.txt"),
                ),
                (
                    409,
                    {
                        "code": "session_changed",
                        "message": (
                            "Session changed. Close and reopen the file."
                        ),
                    },
                    b"outside the workspace",
                ),
            )

    async def test_missing_python_has_a_structured_error(self) -> None:
        """Expose the missing prerequisite without leaking SDK details."""
        with patch.object(
            self._backend,
            "realpaths",
            AsyncMock(side_effect=PathResolutionUnavailable),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    "notes.txt",
                )
        self.assertEqual(
            (ctx.exception.status_code, ctx.exception.detail),
            (
                502,
                {
                    "code": "path_resolver_unavailable",
                    "message": "Workspace path resolution requires python3.",
                },
            ),
        )

    async def test_external_paths_and_symlink_targets_are_rejected(
        self,
    ) -> None:
        """No editor endpoint may access paths outside its canonical root."""
        revision = hashlib.sha256(b"outside the workspace").hexdigest()
        for path in (
            "/elsewhere/outside.txt",
            "../elsewhere/outside.txt",
            "/workspace-other/file.txt",
        ):
            for operation in (
                self._text(path),
                self._save("bad", revision, path=path),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await operation
                self.assertEqual(ctx.exception.status_code, 403)
        with patch.object(
            self._backend,
            "realpath",
            AsyncMock(
                side_effect=lambda path: (
                    "/elsewhere/outside.txt"
                    if path == "/workspace/link.txt"
                    else path
                ),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._save("bad", revision, path="link.txt")
        self.assertEqual(
            (
                ctx.exception.status_code,
                await self._backend.read_file("/elsewhere/outside.txt"),
            ),
            (403, b"outside the workspace"),
        )

    async def test_changed_symlink_is_checked_again_before_save(self) -> None:
        """An alias changed after loading must not redirect the write."""
        original = await self._text()
        with patch.object(
            self._backend,
            "realpath",
            AsyncMock(
                side_effect=[
                    "/workspace",
                    "/workspace/notes.txt",
                    "/workspace",
                    "/elsewhere/outside.txt",
                ],
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await self._save("bad", original.revision)
        self.assertEqual(
            (
                ctx.exception.status_code,
                ctx.exception.detail,
                await self._backend.read_file("/workspace/notes.txt"),
                await self._backend.read_file("/elsewhere/outside.txt"),
            ),
            (
                409,
                {
                    "code": "path_changed",
                    "message": "File path changed. Close and reopen the file.",
                },
                b"hello world",
                b"outside the workspace",
            ),
        )

    async def test_mixed_line_endings_remain_read_only(self) -> None:
        """Neither editor reads nor saves silently normalize mixed endings."""
        for data in (b"a\r\nb\n", b"a\rb\n", b"a\r\nb\r"):
            await self._backend.write_file("/workspace/notes.txt", data)
            for operation in (
                self._text(),
                self._save("new", hashlib.sha256(data).hexdigest()),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await operation
                self.assertEqual(
                    (ctx.exception.status_code, ctx.exception.detail),
                    (
                        422,
                        {
                            "code": "mixed_line_endings",
                            "message": (
                                "Mixed line endings cannot be edited."
                            ),
                        },
                    ),
                )
            self.assertEqual(
                await self._backend.read_file("/workspace/notes.txt"),
                data,
            )

    async def test_backend_failures_have_stable_public_errors(self) -> None:
        """SDK errors retain diagnostics in logs, not in API responses."""
        original = await self._text()
        for method in ("realpath", "stat", "write_file"):
            with patch.object(
                self._backend,
                method,
                AsyncMock(side_effect=RuntimeError("private SDK detail")),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await self._save("draft", original.revision)
            self.assertEqual(
                (ctx.exception.status_code, ctx.exception.detail),
                (502, "Could not access workspace file storage."),
            )

        async def failed_stream(*_args: object) -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("private stream detail")

        with patch.object(self._backend, "read_stream", failed_stream):
            with self.assertRaises(HTTPException) as ctx:
                await self._text()
        self.assertEqual(
            (ctx.exception.status_code, ctx.exception.detail),
            (502, "Could not access workspace file storage."),
        )

    async def test_other_files_are_not_blocked_by_a_slow_write(self) -> None:
        """A stalled write blocks only reads and saves for that file."""
        original = await self._text()
        entered, release = asyncio.Event(), asyncio.Event()
        write = self._backend.write_file

        async def slow_write(path: str, data: bytes) -> None:
            entered.set()
            await release.wait()
            await write(path, data)

        with patch.object(self._backend, "write_file", slow_write):
            saving = asyncio.create_task(self._save("new", original.revision))
            same_file = None
            try:
                await asyncio.wait_for(entered.wait(), 1)
                same_file = asyncio.create_task(self._text())
                other = await asyncio.wait_for(
                    self._text("subdir/report.md"),
                    1,
                )
                self.assertEqual(other.text, "# report\n\nbody\n")
                self.assertFalse(same_file.done())
            finally:
                release.set()
                await saving
                if same_file is not None:
                    latest = await same_file
            self.assertEqual(latest.text, "new")

    async def test_lock_registry_does_not_retain_unused_paths(self) -> None:
        """Weak locks are retained by callers, not by historical paths."""
        original = await self._text()
        await self._save("new", original.revision)
        self.assertEqual(
            len(self._service._file_locks),  # pylint: disable=protected-access
            0,
        )

    async def test_remote_workspaces_do_not_share_same_path_lock(self) -> None:
        """Equal paths in distinct sandboxes remain independent."""
        other_backend = _FakeBackend({"/workspace/notes.txt": b"other"})
        other_workspace = _FakeWorkspace(other_backend)
        other_workspace.workspace_id = "ws-2"
        entered, release = asyncio.Event(), asyncio.Event()
        original_write = self._backend.write_file

        async def slow_write(path: str, data: bytes) -> None:
            entered.set()
            await release.wait()
            await original_write(path, data)

        original = await self._text()
        with (
            patch.object(self._backend, "write_file", slow_write),
            patch.object(
                self._wm,
                "get_workspace",
                AsyncMock(side_effect=[self._workspace, other_workspace]),
            ),
        ):
            saving = asyncio.create_task(self._save("new", original.revision))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                other = await asyncio.wait_for(
                    self._save(
                        "independent",
                        hashlib.sha256(b"other").hexdigest(),
                    ),
                    1,
                )
                self.assertEqual(other.text, "independent")
            finally:
                release.set()
                await saving


class WorkspaceEditorLocalPathTests(_WorkspaceTextFixture):
    """Validate containment against the real local filesystem."""

    async def test_project_symlink_roots_and_escapes(self) -> None:
        """Allow cwd aliases and reject links escaping both roots."""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, project = base / "workspace", base / "project"
            root.mkdir()
            project.mkdir()
            target = project / "notes.txt"
            target.write_bytes(b"before")
            outside = base / "private.txt"
            outside.write_bytes(b"private")
            alias = root / "project-alias"
            try:
                alias.symlink_to(project, target_is_directory=True)
                (project / "escape.txt").symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlinks are not available: {exc}")
            record = await self._storage.get_session("u", "a", "s")
            record.config.cwd = "project-alias"
            with patch.object(
                self._wm,
                "get_workspace",
                AsyncMock(return_value=LocalWorkspace(workdir=str(root))),
            ):
                original = await self._text(str(target))
                await self._save("after", original.revision, path=str(target))
                access = await self._service.file_edit_access(
                    "u",
                    "a",
                    "s",
                    str(project / "escape.txt"),
                )
                self.assertEqual(
                    access.model_dump(),
                    {"editable": False, "reason": "outside_edit_roots"},
                )
            self.assertEqual(
                (target.read_bytes(), outside.read_bytes()),
                (b"after", b"private"),
            )

    async def test_local_symlinks_and_workspace_aliases(self) -> None:
        """Follow internal aliases, but reject aliases targeting outsiders."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            inside = root / "inside.txt"
            inside.write_text("inside", encoding="utf-8")
            try:
                (root / "escape.txt").symlink_to(outside)
                (root / "alias.txt").symlink_to(inside)
                (root / "escape-dir").symlink_to(
                    Path(directory),
                    target_is_directory=True,
                )
            except OSError as exc:
                self.skipTest(f"Symlinks are not available: {exc}")
            workspace = LocalWorkspace(workdir=str(root))
            with patch.object(
                self._wm,
                "get_workspace",
                AsyncMock(return_value=workspace),
            ):
                for path in (
                    str(outside),
                    "escape.txt",
                    "escape-dir/outside.txt",
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await self._save(
                            "bad",
                            hashlib.sha256(b"outside").hexdigest(),
                            path=path,
                        )
                    self.assertEqual(ctx.exception.status_code, 403)
                original = await self._text("alias.txt")
                saved = await self._save(
                    "updated",
                    original.revision,
                    path="alias.txt",
                )
                self.assertEqual(
                    saved.model_dump(),
                    {
                        "path": os.path.realpath(inside),
                        "text": "updated",
                        "revision": hashlib.sha256(b"updated").hexdigest(),
                    },
                )
            self.assertEqual(
                (inside.read_text(), outside.read_text()),
                ("updated", "outside"),
            )

    async def test_local_workspaces_share_file_locks(self) -> None:
        """Distinct local workspaces referencing one path share the lock."""
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_text("original")
            first = LocalWorkspace(workdir=root)
            second = LocalWorkspace(workdir=root)
            entered, release = asyncio.Event(), asyncio.Event()
            original_write = first.get_backend().write_file

            async def slow_write(path: str, data: bytes) -> None:
                entered.set()
                await release.wait()
                await original_write(path, data)

            with (
                patch.object(
                    first.get_backend(),
                    "write_file",
                    slow_write,
                ),
                patch.object(
                    self._wm,
                    "get_workspace",
                    AsyncMock(side_effect=[first, first, second]),
                ),
            ):
                original = await self._text(str(target))
                first_save = asyncio.create_task(
                    self._save(
                        "first",
                        original.revision,
                        path=str(target),
                    ),
                )
                second_save = None
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    second_save = asyncio.create_task(
                        self._save(
                            "second",
                            original.revision,
                            path=str(target),
                        ),
                    )
                    await asyncio.sleep(0)
                    self.assertFalse(second_save.done())
                finally:
                    release.set()
                    await first_save
                with self.assertRaises(HTTPException) as ctx:
                    await second_save
                self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(target.read_text(), "first")

    async def test_backend_path_helpers_respect_target_os(self) -> None:
        """Reject other Windows volumes and POSIX prefix siblings."""
        backend = LocalBackend()
        for module, cases in (
            (
                posixpath,
                [
                    ("/work/file", "/work", True),
                    ("/work-else/file", "/work", False),
                ],
            ),
            (
                ntpath,
                [
                    ("C:\\Work\\file", "c:\\work", True),
                    ("D:\\work\\file", "C:\\work", False),
                    ("\\\\server\\share\\file", "\\\\server\\share", True),
                    ("\\\\server\\other\\file", "\\\\server\\share", False),
                ],
            ),
        ):
            with patch.object(backend, "_path_module", module):
                self.assertEqual(
                    [backend.is_within(path, root) for path, root, _ in cases],
                    [expected for _, _, expected in cases],
                )

    async def test_remote_realpath_uses_argument_vector(self) -> None:
        """Backend resolution neither interpolates paths nor uses host I/O."""
        backend = LocalBackend()
        path = "/sandbox/中文 $(echo bad) #%.txt\n"
        paths = ["/sandbox", path]
        with patch.object(
            backend,
            "exec_shell",
            AsyncMock(
                return_value=ExecResult(
                    0,
                    b"AGENTSCOPE_PATHS:" + json.dumps(paths).encode("ascii"),
                    b"",
                ),
            ),
        ) as execute:
            result = await BackendBase.realpaths(backend, paths)
            self.assertEqual(result, paths)
            self.assertEqual(
                (execute.call_args.args[0][-2:], execute.call_args.kwargs),
                (paths, {"timeout": 10.0}),
            )
            self.assertEqual(
                execute.call_args.args[0][:4],
                ["python3", "-X", "utf8", "-c"],
            )
            execute.assert_awaited_once()
        for result in (
            ExecResult(1, b"", b"unavailable"),
            ExecResult(0, b'AGENTSCOPE_PATHS:["relative"]', b""),
            ExecResult(0, b'AGENTSCOPE_PATHS:["/invalid\\u0000"]', b""),
            ExecResult(0, b'AGENTSCOPE_PATHS:"/wrong shape"', b""),
            ExecResult(0, b"AGENTSCOPE_PATHS:[]", b""),
            ExecResult(0, b"AGENTSCOPE_PATHS:not JSON", b""),
            ExecResult(0, b'["/untagged"]', b""),
            ExecResult(
                0,
                b'AGENTSCOPE_PATHS:["/one"]\nAGENTSCOPE_PATHS:["/two"]',
                b"",
            ),
        ):
            with patch.object(
                backend,
                "exec_shell",
                AsyncMock(return_value=result),
            ):
                with self.assertRaises(RuntimeError):
                    await BackendBase.realpaths(backend, [path])

    async def test_remote_missing_python_is_identifiable(self) -> None:
        """Missing executable reports a prerequisite error, not a file 404."""
        backend = LocalBackend()
        for failure in (
            {"return_value": ExecResult(127, b"", b"python3: not found")},
            {"side_effect": FileNotFoundError("python3")},
        ):
            with patch.object(backend, "exec_shell", AsyncMock(**failure)):
                with self.assertRaises(PathResolutionUnavailable):
                    await BackendBase.realpaths(backend, ["/sandbox"])

    async def test_remote_path_frame_survives_merged_stderr(self) -> None:
        """Warnings before and after the result must not corrupt paths."""
        backend = LocalBackend()
        paths = ["/sandbox/中文\nAGENTSCOPE_PATHS:literal"]
        home = "/home/sandbox"
        output = (
            b"site warning: \xff\n"
            b"AGENTSCOPE_PATHS:"
            + json.dumps([*paths, home]).encode("ascii")
            + b"\nshutdown warning\n"
        )
        with (
            patch.object(backend, "_path_module", posixpath),
            patch.object(
                backend,
                "exec_shell",
                AsyncMock(return_value=ExecResult(0, output, b"")),
            ) as execute,
        ):
            self.assertEqual(
                await BackendBase.realpaths(backend, paths, include_home=True),
                [*paths, home],
            )
            execute.assert_awaited_once()
            self.assertEqual(execute.call_args.args[0][-2:], ["1", paths[0]])

    async def test_remote_encoding_is_independent_of_stdout_locale(
        self,
    ) -> None:
        """Exercise the remote argv script through an actual subprocess."""
        backend = LocalBackend()
        path = os.path.join(tempfile.gettempdir(), "中文 name\n")

        async def execute(command: list[str], **kwargs: object) -> ExecResult:
            return await LocalBackend.exec_shell(
                backend,
                [sys.executable, *command[1:]],
                **kwargs,
            )

        with (
            patch.dict(os.environ, {"PYTHONIOENCODING": "iso8859-1"}),
            patch.object(backend, "exec_shell", execute),
        ):
            self.assertEqual(
                await BackendBase.realpaths(
                    backend,
                    [path],
                    include_home=True,
                ),
                [
                    os.path.realpath(path),
                    os.path.realpath(os.path.expanduser("~")),
                ],
            )
        self.assertEqual(
            await backend.realpaths([path], include_home=True),
            [
                os.path.realpath(path),
                os.path.realpath(os.path.expanduser("~")),
            ],
        )

    async def test_unknown_home_is_omitted_before_realpath(self) -> None:
        """Exercise local and remote resolvers without inventing a home."""
        backend = LocalBackend()
        path = os.path.join(tempfile.gettempdir(), "requested-file")
        expected = [os.path.realpath(path)]
        for home in ("~", "", "relative-home"):
            with patch.object(os.path, "expanduser", return_value=home):
                self.assertEqual(
                    await backend.realpaths([path], include_home=True),
                    expected,
                )

            async def execute(
                command: list[str],
                *,
                unknown_home: str = home,
                **kwargs: object,
            ) -> ExecResult:
                # Simulate an unavailable OS user record inside the child.
                script = (
                    f"import os; os.path.expanduser = lambda _: "
                    f"{unknown_home!r}; {command[4]}"
                )
                return await LocalBackend.exec_shell(
                    backend,
                    [sys.executable, *command[1:4], script, *command[5:]],
                    **kwargs,
                )

            with patch.object(backend, "exec_shell", execute):
                self.assertEqual(
                    await BackendBase.realpaths(
                        backend,
                        [path],
                        include_home=True,
                    ),
                    expected,
                )

    async def test_optional_home_does_not_relax_requested_path_checks(
        self,
    ) -> None:
        """Only the additional home entry may be absent."""
        backend = LocalBackend()
        for paths in (
            [],
            [None],
            ["relative", "/home/test"],
            ["/requested", "~"],
            ["/requested", "/home/test", "/extra"],
        ):
            with (
                patch.object(backend, "_path_module", posixpath),
                patch.object(
                    backend,
                    "exec_shell",
                    AsyncMock(
                        return_value=ExecResult(
                            0,
                            b"AGENTSCOPE_PATHS:" + json.dumps(paths).encode(),
                            b"",
                        ),
                    ),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    await BackendBase.realpaths(
                        backend,
                        ["/requested"],
                        include_home=True,
                    )
