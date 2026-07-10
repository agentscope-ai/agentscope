# -*- coding: utf-8 -*-
"""Workspace router — manage MCPs, skills, and artifacts."""

import asyncio
import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import (
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from ..workspace_manager import WorkspaceManagerBase
from ..storage import StorageBase
from ._schema import ArtifactEntry, ListArtifactsResponse
from ...mcp import MCPClient
from ...skill import Skill
from ...tool._builtin._backend import BackendBase
from ...workspace import WorkspaceBase

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])

MAX_ARTIFACT_FILE_SIZE = 50 * 1024 * 1024
ARTIFACT_CHUNK_SIZE = 64 * 1024


class AddSkillRequest(BaseModel):
    """The request to add skill."""

    skill_path: str


class ToolInfo(BaseModel):
    """The tool info."""

    name: str
    description: str | None = None


class MCPClientStatus(MCPClient):
    """MCPClient enriched with live tool list and health status."""

    is_healthy: bool = False
    tools: list[ToolInfo] = Field(default_factory=list)


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
) -> WorkspaceBase:
    """Resolve the workspace for the given session, raising 404 if not
    found."""
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    return await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )


def _resolve_artifact_path(
    workspace: WorkspaceBase,
    path: str,
) -> tuple[BackendBase, str, str]:
    """Resolve a workspace-relative path and reject lexical traversal.

    Artifact paths are client-visible identifiers rooted at the workspace.
    Accepting absolute paths would let a local workspace read arbitrary host
    files and would make paths non-portable across workspace backends.
    """
    if "\x00" in path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact path cannot contain null bytes.",
        )

    backend = workspace.get_backend()
    relative_path = backend.normpath(path.strip() or ".")
    if backend.isabs(relative_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact path must be relative to the workspace.",
        )

    root = backend.normpath(workspace.workdir)
    resolved = backend.abspath(relative_path, cwd=root)
    root_prefix = backend.join_path(root, "")
    if resolved != root and not resolved.startswith(root_prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact path must stay inside the workspace.",
        )
    return backend, resolved, relative_path


async def _artifact_entry(
    backend: BackendBase,
    parent_path: str,
    relative_parent: str,
    name: str,
) -> ArtifactEntry:
    """Build one artifact entry from a backend directory child."""
    child_path = backend.join_path(parent_path, name)
    relative_path = (
        name
        if relative_parent == "."
        else backend.join_path(relative_parent, name)
    )
    is_directory, modified_at = await asyncio.gather(
        backend.is_dir(child_path),
        backend.stat_mtime(child_path),
    )
    media_type = None
    if not is_directory:
        media_type = (
            mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
    return ArtifactEntry(
        name=name,
        path=relative_path,
        is_directory=is_directory,
        media_type=media_type,
        modified_at=modified_at,
    )


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/mcp")
async def list_mcps(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    clients = await workspace.list_mcps()

    results = []
    for client in clients:
        base = client.model_dump()
        try:
            mcp_tools = await client.list_tools()
            tools = [
                ToolInfo(name=t.name, description=t.description)
                for t in mcp_tools
            ]
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=True,
                    tools=tools,
                ),
            )
        except Exception:
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=False,
                ),
            )

    return results


@workspace_router.post("/mcp", status_code=status.HTTP_201_CREATED)
async def add_mcp(
    mcp: MCPClient,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Add an MCP client to the session's workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.add_mcp(mcp)


@workspace_router.delete(
    "/mcp/{mcp_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_mcp(
    mcp_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Remove an MCP client from the session's workspace by name."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.remove_mcp(mcp_name)


# ---------------------------------------------------------------------------
# Skill endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/skill")
async def list_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    return await workspace.list_skills()


@workspace_router.post("/skill", status_code=status.HTTP_201_CREATED)
async def add_skill(
    body: AddSkillRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Add a skill to the session's workspace from the given path."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.add_skill(body.skill_path)


@workspace_router.delete(
    "/skill/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_skill(
    skill_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Remove a skill from the session's workspace by name."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.remove_skill(skill_name)


# ---------------------------------------------------------------------------
# Artifact endpoints
# ---------------------------------------------------------------------------


@workspace_router.get(
    "/artifacts",
    response_model=ListArtifactsResponse,
)
async def list_artifacts(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query("."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> ListArtifactsResponse:
    """List immediate children of a directory in the session workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    backend, resolved, relative_path = _resolve_artifact_path(workspace, path)
    if not await backend.file_exists(resolved):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact directory {path!r} not found.",
        )
    if not await backend.is_dir(resolved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Artifact path {path!r} is not a directory.",
        )

    names = await backend.list_dir(resolved)
    entries = await asyncio.gather(
        *(
            _artifact_entry(
                backend,
                resolved,
                relative_path,
                name,
            )
            for name in names
        ),
    )
    entries = sorted(
        entries,
        key=lambda entry: (not entry.is_directory, entry.name.lower()),
    )
    return ListArtifactsResponse(artifacts=entries, total=len(entries))


@workspace_router.get(
    "/artifacts/content",
    response_class=StreamingResponse,
    responses={
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "Artifact exceeds the preview size limit.",
        },
    },
)
async def read_artifact(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> StreamingResponse:
    """Stream a size-limited file from the session workspace."""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    backend, resolved, _ = _resolve_artifact_path(workspace, path)
    if not await backend.file_exists(resolved):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact file {path!r} not found.",
        )
    if await backend.is_dir(resolved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Artifact path {path!r} is a directory.",
        )

    file_size = await backend.stat_size(resolved)
    if file_size is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not determine the size of artifact {path!r}.",
        )
    if file_size > MAX_ARTIFACT_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Artifact file exceeds the {MAX_ARTIFACT_FILE_SIZE}-byte "
                "preview limit."
            ),
        )

    media_type = (
        mimetypes.guess_type(resolved)[0] or "application/octet-stream"
    )
    filename = backend.basename(resolved)
    return StreamingResponse(
        backend.iter_file(resolved, chunk_size=ARTIFACT_CHUNK_SIZE),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                "inline; filename*=UTF-8''" + quote(filename, safe="")
            ),
            "Content-Length": str(file_size),
            "X-Content-Type-Options": "nosniff",
        },
    )
