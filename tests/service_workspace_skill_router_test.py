# -*- coding: utf-8 -*-
"""Tests for directory uploads in the workspace skill router."""

import io
import tarfile
from types import SimpleNamespace
from unittest import TestCase

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from agentscope.app._router._workspace import workspace_router
from agentscope.app.deps import (
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from agentscope.workspace._skill import validate_skill_archive


class _Storage:
    """Minimal storage stub for resolving a workspace binding."""

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SimpleNamespace:
        """Return a persisted session with a fixed workspace binding."""
        del user_id, agent_id, session_id
        return SimpleNamespace(
            config=SimpleNamespace(workspace_id="workspace-1"),
        )


class _Workspace:
    """Workspace stub that records validated skill archives."""

    def __init__(self) -> None:
        self.added_skill_archives: list[bytes] = []

    async def add_skill(self, skill_archive: bytes) -> None:
        """Revalidate and record the archive forwarded by the router."""
        validate_skill_archive(skill_archive)
        self.added_skill_archives.append(skill_archive)


class _WorkspaceManager:
    """Manager stub that always returns the same workspace."""

    def __init__(self, workspace: _Workspace) -> None:
        self.workspace = workspace

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> _Workspace:
        """Return the fixed workspace after checking its binding."""
        del user_id, agent_id, session_id
        assert workspace_id == "workspace-1"
        return self.workspace


class WorkspaceSkillRouterTest(TestCase):
    """Exercise skill directory upload validation and packaging."""

    def setUp(self) -> None:
        """Create a dependency-overridden app."""
        self._workspace = _Workspace()
        storage = _Storage()
        manager = _WorkspaceManager(self._workspace)
        self._app = FastAPI()
        self._app.include_router(workspace_router)

        async def _current_user() -> str:
            return "user-1"

        async def _storage() -> _Storage:
            return storage

        async def _workspace_manager() -> _WorkspaceManager:
            return manager

        self._app.dependency_overrides[get_current_user_id] = _current_user
        self._app.dependency_overrides[get_storage] = _storage
        self._app.dependency_overrides[
            get_workspace_manager
        ] = _workspace_manager
        self._client = TestClient(self._app)
        self._params = {"agent_id": "agent-1", "session_id": "session-1"}

    def tearDown(self) -> None:
        """Close the test client."""
        self._client.close()

    def _add_skill(self, files: list[tuple[str, bytes]]) -> Response:
        """Upload skill files while retaining directory-relative paths."""
        multipart = [
            (
                "files",
                (filename, content, "application/octet-stream"),
            )
            for filename, content in files
        ]
        return self._client.post(
            "/workspace/skill",
            params=self._params,
            files=multipart,
        )

    def test_uploads_valid_skill_directory_as_flat_archive(self) -> None:
        """The selected root is stripped before forwarding the archive."""
        response = self._add_skill(
            [
                (
                    "example/SKILL.md",
                    b"---\nname: example\ndescription: test\n---\n",
                ),
                ("example/scripts/run.py", b"print('ok')\n"),
            ],
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self._workspace.added_skill_archives), 1)
        archive = self._workspace.added_skill_archives[0]
        metadata = validate_skill_archive(archive)
        self.assertEqual(metadata.name, "example")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            self.assertEqual(
                tar.getnames(),
                ["SKILL.md", "scripts/run.py"],
            )

    def test_rejects_missing_or_invalid_root_skill_file(self) -> None:
        """SKILL.md must exist at the root with required frontmatter."""
        invalid_uploads = (
            [("example/docs/SKILL.md", b"---\nname: test\n---\n")],
            [("example/SKILL.md", b"---\nname: test\n---\n")],
        )
        for files in invalid_uploads:
            with self.subTest(files=files):
                response = self._add_skill(files)
                self.assertEqual(response.status_code, 400)

        self.assertEqual(self._workspace.added_skill_archives, [])

    def test_rejects_unsafe_or_multiple_directory_paths(self) -> None:
        """Uploads cannot traverse paths or combine multiple roots."""
        skill_md = b"---\nname: test\ndescription: test\n---\n"
        invalid_uploads = (
            [("example/../SKILL.md", skill_md)],
            [("example\\SKILL.md", skill_md)],
            [
                ("one/SKILL.md", skill_md),
                ("two/data.txt", b"data"),
            ],
        )
        for files in invalid_uploads:
            with self.subTest(files=files):
                response = self._add_skill(files)
                self.assertEqual(response.status_code, 400)

        self.assertEqual(self._workspace.added_skill_archives, [])

    def test_rejects_portable_duplicate_paths(self) -> None:
        """Case-only duplicate names cannot overwrite on extraction."""
        response = self._add_skill(
            [
                (
                    "example/SKILL.md",
                    b"---\nname: test\ndescription: test\n---\n",
                ),
                ("example/readme.txt", b"one"),
                ("example/README.TXT", b"two"),
            ],
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._workspace.added_skill_archives, [])

    def test_rejects_legacy_server_path_request(self) -> None:
        """The HTTP contract no longer accepts a host filesystem path."""
        response = self._client.post(
            "/workspace/skill",
            params=self._params,
            json={"skill_path": "/tmp/example"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self._workspace.added_skill_archives, [])
