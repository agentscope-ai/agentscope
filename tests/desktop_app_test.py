# -*- coding: utf-8 -*-
"""Tests for the self-contained AgentScope Desktop backend."""
import json
import os
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentscope._version import __version__
from agentscope.tool._builtin._glob import _default_glob_helper_path
from agentscope.workspace._base import _MIGRATE_SKILLS_SHIM
from apps.desktop.build_backend import resolve_ripgrep_executable
from apps.desktop.main import (
    DESKTOP_SHUTDOWN_COMMAND,
    build_packaged_tool_path,
    build_sqlite_url,
    configure_desktop_logging,
    create_desktop_app,
    monitor_shutdown_stream,
    run_packaged_helper,
)


class FakeShutdownTarget:
    """Minimal server state used by shutdown protocol tests."""

    def __init__(self) -> None:
        self.should_exit = False


def desktop_headers(token: str) -> dict[str, str]:
    """Return the authenticated headers used by desktop API tests."""
    return {"Authorization": f"Bearer {token}"}


def knowledge_base_body(
    credential_id: str,
    name: str,
) -> dict:
    """Return a deterministic knowledge-base creation payload."""
    return {
        "name": name,
        "description": "Desktop persistence test",
        "embedding_model_config": {
            "type": "openai_credential",
            "credential_id": credential_id,
            "model": "text-embedding-3-small",
            "dimensions": 3,
            "parameters": {},
        },
    }


def test_build_sqlite_url_uses_the_complete_absolute_path(
    tmp_path: Path,
) -> None:
    """The SQLite URL must preserve the full platform-specific path."""
    database_path = tmp_path / "nested" / "agentscope.db"
    assert build_sqlite_url(database_path) == (
        f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}"
    )


def test_build_packaged_tool_path_prepends_the_bundle(tmp_path: Path) -> None:
    """Bundled command-line tools must take precedence over the host PATH."""
    assert build_packaged_tool_path(tmp_path, "host-path") == (
        f"{tmp_path}{os.pathsep}host-path"
    )
    assert build_packaged_tool_path(tmp_path, "") == f"{tmp_path}"


def test_configure_desktop_logging_uses_data_directory(
    tmp_path: Path,
) -> None:
    """Desktop logs must be stored beside its persistent application data."""
    data_dir = tmp_path / "nested"
    with patch("apps.desktop.main.setup_logger") as setup:
        configure_desktop_logging(data_dir)

    assert data_dir.is_dir()
    setup.assert_called_once_with(
        "INFO",
        str(data_dir / "agentscope.log"),
    )


def test_packaged_helper_runs_an_allowlisted_workspace_shim(
    tmp_path: Path,
) -> None:
    """The frozen backend must handle its known workspace shims."""
    skills_dir = tmp_path / "skills"
    legacy_dir = skills_dir / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "SKILL.md").write_text(
        "---\nname: legacy\ndescription: test\n---\n",
        encoding="utf-8",
    )

    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "argv", ["agentscope-backend"]),
    ):
        handled = run_packaged_helper(
            [
                "-c",
                _MIGRATE_SKILLS_SHIM,
                str(skills_dir),
                ".seed",
            ],
        )
        assert sys.argv == ["agentscope-backend"]

    assert handled is True
    assert sorted(
        path.relative_to(skills_dir).as_posix()
        for path in skills_dir.rglob("*")
    ) == [
        ".seed",
        ".seed/legacy",
        ".seed/legacy/SKILL.md",
    ]


def test_packaged_helper_rejects_unknown_python_code() -> None:
    """The frozen backend must not expose a general Python command."""
    with patch.object(sys, "frozen", True, create=True):
        assert run_packaged_helper(["-c", "print('unexpected')"]) is False


def test_packaged_helper_runs_the_bundled_glob_helper(
    tmp_path: Path,
) -> None:
    """The frozen backend must dispatch only the bundled Glob script."""
    matched_file = tmp_path / "matched.txt"
    matched_file.write_text("content", encoding="utf-8")
    output = StringIO()

    with (
        patch.object(sys, "frozen", True, create=True),
        patch.object(sys, "argv", ["agentscope-backend"]),
        redirect_stdout(output),
    ):
        handled = run_packaged_helper(
            [
                _default_glob_helper_path(),
                "--pattern",
                "*.txt",
                "--base-dir",
                str(tmp_path),
            ],
        )
        assert sys.argv == ["agentscope-backend"]

    assert handled is True
    assert json.loads(output.getvalue()) == [str(matched_file)]


def test_resolve_ripgrep_executable_uses_python_scripts_dir(
    tmp_path: Path,
) -> None:
    """The build must source ripgrep from the active Python environment."""
    executable_name = "rg.exe" if os.name == "nt" else "rg"
    executable = tmp_path / executable_name
    executable.touch()

    assert resolve_ripgrep_executable(tmp_path) == executable
    with pytest.raises(
        FileNotFoundError,
        match="ripgrep executable not found",
    ):
        resolve_ripgrep_executable(tmp_path / "missing")


def test_monitor_shutdown_stream_requests_graceful_exit() -> None:
    """Only the desktop shutdown command should stop the Uvicorn server."""
    target = FakeShutdownTarget()
    monitor_shutdown_stream(
        StringIO(f"ignored\n{DESKTOP_SHUTDOWN_COMMAND}\n"),
        target,
    )
    assert target.should_exit is True


def test_monitor_shutdown_stream_exits_when_electron_disconnects() -> None:
    """An Electron crash must not leave the desktop backend running."""
    target = FakeShutdownTarget()
    monitor_shutdown_stream(StringIO(""), target)
    assert target.should_exit is True


def test_alembic_config_is_safe_for_ascii_locales() -> None:
    """Desktop migrations must not depend on the host text encoding."""
    config_path = (
        Path(__file__).parents[1]
        / "src"
        / "agentscope"
        / "app"
        / "storage"
        / "_sql"
        / "_alembic"
        / "alembic.ini"
    )
    assert config_path.read_bytes().isascii()


def test_create_desktop_app_rejects_an_empty_token(tmp_path: Path) -> None:
    """An empty launch token must never create an unauthenticated app."""
    with pytest.raises(
        ValueError,
        match="Desktop authorization token must not be empty",
    ):
        create_desktop_app(tmp_path, "")


def test_desktop_app_authentication_and_local_storage(
    tmp_path: Path,
) -> None:
    """The desktop app must authenticate and persist entirely locally."""
    token = "test-desktop-token"
    app = create_desktop_app(tmp_path, token)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["workspaces"]

    with TestClient(app) as client:
        missing = client.get("/health")
        assert missing.status_code == 401
        assert missing.json() == {
            "detail": "Invalid desktop authorization token.",
        }

        incorrect = client.get(
            "/health",
            headers={"Authorization": "Bearer incorrect"},
        )
        assert incorrect.status_code == 401
        assert incorrect.json() == {
            "detail": "Invalid desktop authorization token.",
        }

        signed_download = client.get(
            "/workspace/files",
            params={
                "agent_id": "agent",
                "session_id": "session",
                "path": "result.txt",
                "token": "invalid-signed-token",
            },
        )
        assert signed_download.status_code == 401
        assert signed_download.json() == {
            "detail": "Malformed download token.",
        }

        response = client.get(
            "/health",
            headers=desktop_headers(token),
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "version": __version__,
            "components": {
                "storage": "ok",
                "message_bus": "ok",
                "workspace_manager": "ok",
                "background_task_manager": "ok",
                "chat_run_registry": "ok",
                "scheduler_manager": "ok",
                "resource_access_service": "ok",
                "chat_service": "ok",
                "session_service": "ok",
                "mcp_hubs": "disabled",
                "skill_hubs": "disabled",
                "knowledge_base": "ok",
            },
        }

        knowledge_bases = client.get(
            "/knowledge_bases/",
            headers=desktop_headers(token),
        )
        assert knowledge_bases.status_code == 200
        assert knowledge_bases.json() == {
            "knowledge_bases": [],
            "total": 0,
            "page": 1,
            "page_size": 30,
        }

        preflight = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == (
            "http://localhost:5173"
        )

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "agentscope.db",
        "blobs",
        "workspaces",
    ]


def test_desktop_knowledge_base_persists_across_restarts(
    tmp_path: Path,
) -> None:
    """SQLite records and Qdrant collections must survive a restart."""
    token = "test-desktop-token"
    headers = desktop_headers(token)
    credential_id = "desktop-test-credential"

    first_app = create_desktop_app(tmp_path, token)
    with TestClient(first_app) as client:
        credential = client.post(
            "/credential/",
            headers=headers,
            json={
                "data": {
                    "type": "openai_credential",
                    "id": credential_id,
                    "name": "Desktop test",
                    "api_key": "not-a-real-api-key",
                },
            },
        )
        assert credential.status_code == 201
        assert credential.json() == {"credential_id": credential_id}

        created = client.post(
            "/knowledge_bases/",
            headers=headers,
            json=knowledge_base_body(credential_id, "Persistent KB"),
        )
        assert created.status_code == 201
        knowledge_base_id = created.json()["knowledge_base_id"]
        assert created.json() == {
            "knowledge_base_id": knowledge_base_id,
        }

        first_list = client.get(
            "/knowledge_bases/",
            headers=headers,
        )
        assert first_list.status_code == 200
        first_payload = first_list.json()
        assert first_payload["total"] == 1
        assert first_payload["knowledge_bases"][0]["id"] == (knowledge_base_id)

    collection_database = (
        tmp_path
        / "qdrant"
        / "collection"
        / f"kb_{knowledge_base_id}"
        / "storage.sqlite"
    )
    assert collection_database.is_file()

    second_app = create_desktop_app(tmp_path, token)
    with TestClient(second_app) as client:
        persisted = client.get(
            "/knowledge_bases/",
            headers=headers,
        )
        assert persisted.status_code == 200
        assert persisted.json() == first_payload

        second_created = client.post(
            "/knowledge_bases/",
            headers=headers,
            json=knowledge_base_body(credential_id, "Second KB"),
        )
        assert second_created.status_code == 201
        second_knowledge_base_id = second_created.json()["knowledge_base_id"]
        assert second_created.json() == {
            "knowledge_base_id": second_knowledge_base_id,
        }

    second_collection_database = (
        tmp_path
        / "qdrant"
        / "collection"
        / f"kb_{second_knowledge_base_id}"
        / "storage.sqlite"
    )
    assert second_collection_database.is_file()
