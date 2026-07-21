# -*- coding: utf-8 -*-
"""Tests for the workspace artifact router."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app._router._workspace import workspace_router
from agentscope.app.deps import (
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from agentscope.tool._builtin._backend import LocalBackend


class _Storage:
    """Minimal storage stub for resolving a persisted workspace binding."""

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SimpleNamespace:
        del user_id, agent_id, session_id
        return SimpleNamespace(
            config=SimpleNamespace(workspace_id="workspace-1"),
        )


class _Workspace:
    """Workspace stub backed by a temporary local directory."""

    def __init__(self, workdir: str) -> None:
        self.workdir = workdir
        self._backend = LocalBackend()

    def get_backend(self) -> LocalBackend:
        """Return the local filesystem backend."""
        return self._backend


class _WorkspaceManager:
    """Manager stub that always returns the test workspace."""

    def __init__(self, workspace: _Workspace) -> None:
        self._workspace = workspace

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> _Workspace:
        del user_id, agent_id, session_id
        assert workspace_id == "workspace-1"
        return self._workspace


class ArtifactRouterTest(TestCase):
    """Exercise directory listing, file reads, and path boundaries."""

    def setUp(self) -> None:
        """Create a temporary workspace and dependency-overridden app."""
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        (self._root / "reports").mkdir()
        (self._root / "reports" / "summary.md").write_text(
            "# Summary\n",
            encoding="utf-8",
        )
        (self._root / "notes.txt").write_text("hello", encoding="utf-8")

        storage = _Storage()
        manager = _WorkspaceManager(_Workspace(self._tmpdir.name))
        app = FastAPI()
        app.include_router(workspace_router)

        async def _current_user() -> str:
            return "user-1"

        async def _storage() -> _Storage:
            return storage

        async def _workspace_manager() -> _WorkspaceManager:
            return manager

        app.dependency_overrides[get_current_user_id] = _current_user
        app.dependency_overrides[get_storage] = _storage
        app.dependency_overrides[get_workspace_manager] = _workspace_manager
        self._client = TestClient(app)
        self._params = {"agent_id": "agent-1", "session_id": "session-1"}

    def tearDown(self) -> None:
        """Close the HTTP client and remove the temporary workspace."""
        self._client.close()
        self._tmpdir.cleanup()

    def test_list_directory(self) -> None:
        """Directories sort before files and paths stay workspace-relative."""
        response = self._client.get(
            "/workspace/artifacts",
            params=self._params,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entries = payload["artifacts"]
        self.assertEqual(payload["total"], 2)
        self.assertEqual(
            [
                (entry["name"], entry["path"], entry["is_directory"])
                for entry in entries
            ],
            [
                ("reports", "reports", True),
                ("notes.txt", "notes.txt", False),
            ],
        )
        self.assertEqual(entries[1]["media_type"], "text/plain")

        nested = self._client.get(
            "/workspace/artifacts",
            params={**self._params, "path": "reports"},
        )
        self.assertEqual(nested.status_code, 200)
        self.assertEqual(
            nested.json()["artifacts"][0]["path"],
            "reports/summary.md",
        )

    def test_read_file(self) -> None:
        """File content is returned inline with its detected media type."""
        response = self._client.get(
            "/workspace/artifacts/content",
            params={**self._params, "path": "reports/summary.md"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"# Summary\n")
        self.assertEqual(response.headers["content-length"], "10")
        self.assertEqual(
            response.headers["content-type"],
            "text/markdown; charset=utf-8",
        )
        self.assertIn("summary.md", response.headers["content-disposition"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_rejects_paths_outside_workspace(self) -> None:
        """Absolute paths and parent traversal cannot escape the workspace."""
        outside = Path(self._tmpdir.name).parent / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)

        for path in ("../outside-secret.txt", str(outside)):
            with self.subTest(path=path):
                response = self._client.get(
                    "/workspace/artifacts/content",
                    params={**self._params, "path": path},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejects_wrong_path_kinds(self) -> None:
        """List requires a directory and content requires a regular file."""
        list_response = self._client.get(
            "/workspace/artifacts",
            params={**self._params, "path": "notes.txt"},
        )
        read_response = self._client.get(
            "/workspace/artifacts/content",
            params={**self._params, "path": "reports"},
        )

        self.assertEqual(list_response.status_code, 400)
        self.assertEqual(read_response.status_code, 400)

    def test_missing_path_returns_not_found(self) -> None:
        """Missing directories and files return HTTP 404."""
        for endpoint in (
            "/workspace/artifacts",
            "/workspace/artifacts/content",
        ):
            with self.subTest(endpoint=endpoint):
                response = self._client.get(
                    endpoint,
                    params={**self._params, "path": "missing.txt"},
                )
                self.assertEqual(response.status_code, 404)

    def test_rejects_file_over_preview_limit(self) -> None:
        """Oversized artifacts are rejected before their content is read."""
        with patch(
            "agentscope.app._router._workspace.MAX_ARTIFACT_FILE_SIZE",
            4,
        ):
            response = self._client.get(
                "/workspace/artifacts/content",
                params={**self._params, "path": "notes.txt"},
            )

        self.assertEqual(response.status_code, 413)
