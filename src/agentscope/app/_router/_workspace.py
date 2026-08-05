# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
import os

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import ValidationError

from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_skill_hubs,
    get_storage,
    get_workspace_manager,
)
from ..hub import SkillHubBase
from .._service import ResourceAccessService
from .._service._skill_upload import (
    SkillUploadError,
    UploadManifest,
    _install_slots,
    _tar_stream,
    _validate_manifest,
)
from ..workspace_manager import WorkspaceManagerBase
from ..storage import MCPRecord, StorageBase
from ...mcp import MCPClient
from ...skill import Skill
from ...workspace import WorkspaceBase
from ..._logging import logger
from ._schema import (
    AddFromLibraryRequest,
    AddFromLibraryResponse,
    AddSkillRequest,
    AddSkillsFromLibraryRequest,
    AgentSkillsListResponse,
    MCPClientStatus,
    SkillActionResponse,
    SkillInfo,
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
) -> WorkspaceBase:
    """Return the workspace backing the given session.

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

    Returns:
        `WorkspaceBase`:
            The session's workspace.

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
    return await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
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
        except Exception as e:
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=False,
                    error=_describe_exception(e),
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
    """Add an MCP client to the session's workspace.

    The MCP is also recorded in the user's library, so one typed in by
    hand is reusable in the next session instead of being retyped. An
    existing record of the same name is left alone: the library is where
    that MCP is defined, and adding it to a second workspace must not
    silently redefine it.
    """
    workspace = await _resolve_workspace(
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
    workspace = await _resolve_workspace(
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
    workspace = await _resolve_workspace(
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

    workspace = await _resolve_workspace(
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
    workspace = await _resolve_workspace(
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
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    await workspace.remove_skill(skill_name)


@workspace_router.get(
    "/skills/external",
    response_model=AgentSkillsListResponse,
    summary="Get Agent Skills",
    description=(
        "Query the external skillhub catalog and return it to the "
        "frontend, marking as ``used`` the skills already present in "
        "the session's workspace (``agent_id`` and ``session_id`` are "
        "passed as query parameters)."
    ),
)
async def get_agent_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    page: int = Query(default=0, ge=0),
    q: str = Query(default=""),
    size: int = Query(default=10, ge=1, le=200),
    sort: str = Query(default=""),
    label: str = Query(default=""),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AgentSkillsListResponse:
    """Query the external skillhub and return the skill list.

    ``used`` reflects the skills already present in the session's
    workspace directory — resolved from the persisted session record
    (``session_id`` → ``config.workspace_id``), so the marking is exact
    under every isolation policy, including ``PER_SESSION``.

    The remote endpoint mirrors these parameters
    (``page/q/size/sort/label``, ``namespace=global``) and answers
    with ``{data: {items: [{slug, summary, ...}], total}}``.
    """
    # Validate ownership: the agent must belong to (or be shared with)
    # the caller, otherwise 404 — mirrors every other agent-scoped route.
    await access.resolve_agent(user_id, agent_id)

    # "Used" = skills already equipped in the session's workspace
    # (resolved via the persisted session record). A missing/invalid
    # session is a 404; other resolution failures fall back to marking
    # nothing as used.
    try:
        workspace = await _resolve_workspace(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        agent_skills = await workspace.list_skills()
        used_names = {s.name for s in agent_skills}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to resolve workspace for agent %s, marking no "
            "skills as used: %s",
            agent_id,
            e,
        )
        used_names = set()

    # Query the registered external hub (per-request token refresh).
    hub = hubs.get("external") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'external' skill hub is registered.",
        )
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)

    # label folds into the search query; sort is accepted but ignored.
    merged_q = " ".join(filter(None, [label, q])) or None
    cursor = f"page:{page}" if page else None
    page_result = await hub.list_skills(
        user_id,
        q=merged_q,
        cursor=cursor,
        limit=size,
    )

    skills_list = [
        SkillInfo(
            name=card.name,
            category="public",
            description=card.description or "",
            used=card.name in used_names,
        )
        for card in page_result.cards
    ]
    return AgentSkillsListResponse(
        skills=skills_list,
        total=(
            page_result.total
            if page_result.total is not None
            else len(skills_list)
        ),
    )


@workspace_router.post(
    "/skill/download/{skill_full_name}",
    response_model=SkillActionResponse,
    summary="Enable Skill for Agent",
    description=(
        "Download a skill from the remote skillhub into the session's "
        "workspace. ``skill_full_name`` follows ``category:name`` "
        "(e.g. ``public:writing``); ``agent_id`` and ``session_id`` are "
        "passed as query parameters."
    ),
)
async def enable_agent_skill(
    skill_full_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillActionResponse:
    """Enable a skill for a specific agent.

    ``skill_full_name`` follows the ``category:name`` convention (e.g.
    ``public:writing``); only ``public`` skills are downloadable. When
    the skill is already equipped in the agent's workspace the call is
    a no-op success.

    ``agent_id`` and ``session_id`` are passed as query parameters; the
    target workspace is resolved from the persisted session record so
    the skill lands in the exact session's workspace regardless of
    isolation policy.
    """
    # Ownership check: the agent must belong to (or be shared with) the
    # caller, otherwise 404 — mirrors every other agent-scoped route.
    await access.resolve_agent(user_id, agent_id)

    if ":" not in skill_full_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "skill_full_name must be in 'category:name' form, "
                "e.g. 'public:writing'."
            ),
        )
    category, skill_name = skill_full_name.split(":", 1)
    if category != "public":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only 'public' skills can be enabled.",
        )

    # Resolve the workspace from the persisted session record — under
    # PER_SESSION the workspace id is a per-session random value, so it
    # must come from the database, not from assign_workspace_id.
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    # Already equipped — no-op success. Match by agent-facing name or
    # directory basename: the extracted dir is named after the SKILL.md
    # front matter, which may differ from the requested slug.
    existing = await workspace.list_skills()
    if skill_name in {s.name for s in existing} or any(
        os.path.basename(s.dir.rstrip("/\\")) == skill_name
        for s in existing
    ):
        logger.info(
            "Skill '%s' already equipped in agent %s's workspace",
            skill_full_name,
            agent_id,
        )
        return SkillActionResponse(
            success=True,
            action="enabled",
            skill_id=skill_full_name,
        )

    # Download from the remote skillhub through the hub abstraction —
    # streaming the archive into the workspace via its backend.
    hub = hubs.get("external") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'external' skill hub is registered.",
        )
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)
    try:
        archive = await hub.download(user_id, skill_name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found on the remote skillhub.",
        ) from None
    try:
        await workspace.add_skill_archive(
            archive.stream,
            archive.format,
            skill_name,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to install skill '{skill_name}': {e}",
        ) from e

    # Verify something new actually landed (the extracted dir may be
    # named by the front matter rather than the slug).
    refreshed = await workspace.list_skills()
    new_names = {s.name for s in refreshed} - {s.name for s in existing}
    if not new_names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Downloaded skill '{skill_name}' has no valid SKILL.md "
                "(requires 'name' and 'description' fields)."
            ),
        )

    logger.info("Enabled skill '%s' for agent '%s'", skill_full_name, agent_id)
    return SkillActionResponse(
        success=True,
        action="enabled",
        skill_id=skill_full_name,
    )


@workspace_router.get(
    "/skills/uploaded",
    response_model=AgentSkillsListResponse,
    summary="Get Uploaded Skills",
    description=(
        "Query the external skillhub for the skills the caller uploaded "
        "and return them, marking as ``used`` the ones already present "
        "in the session's workspace (``agent_id`` and ``session_id`` "
        "are passed as query parameters)."
    ),
)
async def get_uploaded_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AgentSkillsListResponse:
    """Return the skills the caller uploaded to the external skillhub.

    The endpoint is per-user, so ``guwpToken`` is required — the session
    cookie derived from it carries the caller's identity on the remote
    side. ``used`` reflects the skills already present in the session's
    workspace, resolved from the persisted session record
    (``session_id`` → ``config.workspace_id``).
    """
    # Ownership check: the agent must belong to (or be shared with) the
    # caller, otherwise 404 — mirrors every other agent-scoped route.
    await access.resolve_agent(user_id, agent_id)

    if not guwp_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="guwpToken header is required for uploaded skills.",
        )

    # "Used" = skills already equipped in the session's workspace
    # (resolved via the persisted session record). A missing/invalid
    # session is a 404; other resolution failures fall back to marking
    # nothing as used.
    try:
        workspace = await _resolve_workspace(
            user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        agent_skills = await workspace.list_skills()
        used_names = {s.name for s in agent_skills}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to resolve workspace for agent %s, marking no "
            "skills as used: %s",
            agent_id,
            e,
        )
        used_names = set()

    hub = hubs.get("external") if hubs else None
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No 'external' skill hub is registered.",
        )
    set_token = getattr(hub, "set_token", None)
    if set_token is not None:
        set_token(guwp_token)

    page_result = await hub.list_uploaded_skills(user_id)
    skills_list = [
        SkillInfo(
            name=card.name,
            category="public",
            description=card.description or "",
            used=card.name in used_names,
        )
        for card in page_result.cards
    ]
    return AgentSkillsListResponse(
        skills=skills_list,
        total=(
            page_result.total
            if page_result.total is not None
            else len(skills_list)
        ),
    )
