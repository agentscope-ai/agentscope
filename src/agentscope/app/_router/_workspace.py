# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError

from ..deps import (
    get_current_user_id,
    get_skill_hubs,
    get_storage,
    get_workspace_manager,
)
from ..hub import SkillHubBase
from .._service._skill_upload import (
    SkillUploadError,
    UploadManifest,
    _install_slots,
    _tar_stream,
    _validate_manifest,
)
from .._service import resolve_agent_seeds
from ..workspace_manager import WorkspaceManagerBase
from ..storage import MCPRecord, StorageBase
from ...mcp import MCPClient
from ...workspace import WorkspaceBase
from ._schema import (
    AddFromLibraryRequest,
    AddFromLibraryResponse,
    AddSkillRequest,
    AddSkillsFromLibraryRequest,
    ListWorkspaceMCPsResponse,
    ListWorkspaceSkillsResponse,
    MCPClientStatus,
    ToolInfo,
)
from ..._utils._common import _describe_exception

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    skill_hubs: dict[str, SkillHubBase] | None = None,
) -> tuple[WorkspaceBase, dict[str, str]]:
    """Return the workspace backing the given session.

    The agent's own MCPs and skills ride along as seeds. They are only
    consumed if this call is what brings the workspace into existence —
    see :meth:`WorkspaceManagerBase.get_workspace`.

    Args:
        user_id (`str`):
            The authenticated user ID.
        agent_id (`str`):
            The agent owning the session.
        session_id (`str`):
            The session whose workspace is wanted.
        storage (`StorageBase`):
            The storage used to look the session record up.
        workspace_manager (`WorkspaceManagerBase`):
            The manager that opens or reattaches the workspace.
        skill_hubs (`dict[str, SkillHubBase] | None`, optional):
            Registered skill hubs, needed to fetch the agent's skills.
            Omit on the endpoints that do not report seeding problems.

    Returns:
        `tuple[WorkspaceBase, dict[str, str]]`:
            The session's workspace, and whatever of the agent's own
            MCPs and skills could not be put into it, mapped to why.

    Raises:
        `HTTPException`:
            ``404`` when the session does not exist.
    """
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    seeds = await resolve_agent_seeds(
        storage,
        skill_hubs or {},
        user_id,
        agent_id,
    )
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
        seed_mcps=seeds.mcps,
        seed_skills=seeds.skills,
    )
    # Unresolvable bindings are recomputed every call; seeding failures
    # happened once and are remembered by the workspace itself.
    return workspace, {**workspace.seed_errors, **seeds.errors}


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
    skill_hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> ListWorkspaceMCPsResponse:
    """Return all MCP clients with live tool list and health status."""
    workspace, seed_errors = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        skill_hubs,
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
        except Exception as e:
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=False,
                    error=_describe_exception(e),
                ),
            )

    # A seeded MCP that never connected is absent rather than red, so
    # the reason has to travel beside the list.
    present = {client.name for client in clients}
    return ListWorkspaceMCPsResponse(
        mcps=results,
        seed_errors={
            name: why
            for name, why in seed_errors.items()
            if name not in present
        },
    )


@workspace_router.post("/mcp", status_code=status.HTTP_201_CREATED)
async def add_mcp(
    mcp: MCPClient,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Add an MCP client to the session's workspace.

    The MCP is also recorded in the user's library, so one typed in by
    hand is reusable in the next session instead of being retyped. An
    existing record of the same name is left alone: the library is where
    that MCP is defined, and adding it to a second workspace must not
    silently redefine it.
    """
    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.add_mcp(mcp)

    if await storage.get_mcp_by_name(user_id, mcp.name) is None:
        # No hub_id or card_id — this one has no card behind it, which
        # is what tells the library it cannot be re-keyed or upgraded.
        await storage.upsert_mcp(
            user_id,
            MCPRecord(user_id=user_id, client=mcp),
        )


@workspace_router.post(
    "/mcp/from-library",
    status_code=status.HTTP_201_CREATED,
)
async def add_mcps_from_library(
    body: AddFromLibraryRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> AddFromLibraryResponse:
    """Put MCPs the user has already installed into this workspace.

    The rendered config never leaves the server, so the client sends ids
    rather than configs — it has no way to reconstruct one.

    Adding is per-MCP: one that fails to connect does not cancel the
    rest, and the response says which ones landed.
    """
    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    present = {client.name for client in await workspace.list_mcps()}

    added: list[str] = []
    failed: dict[str, str] = {}
    for mcp_id in body.mcp_ids:
        record = await storage.get_mcp(user_id, mcp_id)
        if record is None:
            failed[mcp_id] = "Not in your library."
            continue
        if record.client.name in present:
            # Already there: not an error, just nothing to do.
            continue
        try:
            await workspace.add_mcp(record.client)
        except Exception as e:
            failed[record.client.name] = _describe_exception(e)
            continue
        added.append(record.client.name)

    return AddFromLibraryResponse(added=added, failed=failed)


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
    workspace, _ = await _resolve_workspace(
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
    skill_hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> ListWorkspaceSkillsResponse:
    """Return all skills available in the session's workspace."""
    workspace, seed_errors = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        skill_hubs,
    )
    skills = await workspace.list_skills()
    present = {skill.name for skill in skills}
    return ListWorkspaceSkillsResponse(
        skills=skills,
        seed_errors={
            name: why
            for name, why in seed_errors.items()
            if name not in present
        },
    )


@workspace_router.post(
    "/skill",
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def add_skill(
    body: AddSkillRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Add a skill to the session's workspace from the given path.

    Deprecated: the path is resolved on the server, which only means
    anything for a single-host deployment. Use ``POST /skill/upload``
    to send a folder, or ``POST /skill/from-library`` to install one
    the user already has.
    """
    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.add_skill(body.skill_path)


@workspace_router.post(
    "/skill/upload",
    status_code=status.HTTP_201_CREATED,
)
async def upload_skill(
    manifest: str = Form(
        description=(
            "JSON ``{entries: [{path, size}]}`` describing the parts, "
            "in the order they are sent."
        ),
    ),
    files: list[UploadFile] = File(description="The folder's files."),
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Install a skill from an uploaded folder.

    The parts are re-tarred on the fly and piped into the workspace, so
    the archive is never held whole. The manifest is what the client
    claims; every limit in it is re-checked here, and the byte counts
    are verified as the tar is built.
    """
    try:
        parsed = UploadManifest.model_validate_json(manifest)
        _validate_manifest(parsed)
    except (ValidationError, SkillUploadError) as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(e),
        ) from e

    if len(files) != len(parsed.entries):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The manifest lists {len(parsed.entries)} files but "
            f"{len(files)} were sent.",
        )

    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    async with _install_slots:
        try:
            # dir_name is unused: the tar members already carry the
            # picked folder as their first path segment.
            await workspace.add_skill_archive(
                _tar_stream(parsed, files),
                "tar",
                "skill",
            )
        except (SkillUploadError, ValueError) as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                str(e),
            ) from e


@workspace_router.post(
    "/skill/from-library",
    status_code=status.HTTP_201_CREATED,
)
async def add_skills_from_library(
    body: AddSkillsFromLibraryRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    skill_hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AddFromLibraryResponse:
    """Put skills the user has already installed into this workspace.

    Each one is re-downloaded from its hub and piped into the
    workspace; the server holds no copy in between. Adding is
    per-skill, and the response says which ones landed.
    """
    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    added: list[str] = []
    failed: dict[str, str] = {}
    for skill_id in body.skill_ids:
        record = await storage.get_skill(user_id, skill_id)
        if record is None:
            failed[skill_id] = "Not in your library."
            continue
        hub = skill_hubs.get(record.hub_id or "")
        if hub is None:
            failed[
                record.name
            ] = f"Its hub {record.hub_id!r} is no longer registered."
            continue
        try:
            async with _install_slots:
                archive = await hub.download(
                    user_id,
                    record.card_id or record.name,
                    record.version,
                )
                await workspace.add_skill_archive(
                    archive.stream,
                    archive.format,
                    record.name,
                )
        except Exception as e:  # pylint: disable=broad-except
            failed[record.name] = _describe_exception(e)
            continue
        added.append(record.name)

    return AddFromLibraryResponse(added=added, failed=failed)


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
    workspace, _ = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.remove_skill(skill_name)
