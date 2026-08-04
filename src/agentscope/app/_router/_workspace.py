# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
import json
from pathlib import Path
from urllib.parse import quote
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


# The external skillhub catalog the agent-skills endpoint queries.
# Module constants so a deployment can override them in one place.
_SKILLHUB_BASE_URL = "http://53.12.9.18/skillhub-server"
_SKILLHUB_TIMEOUT = 30.0


def _skillhub_headers(cookie: str) -> dict[str, str]:
    """Build the request headers expected by the external skillhub."""
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
    }


async def _refresh_skillhub_cookie(guwp_token: str | None) -> str:
    """Exchange ``guwpToken`` for a fresh ``SESSION=...`` cookie.

    Returns:
        The new cookie string, or ``""`` when no token was provided or
        the exchange failed — callers fall back to the initial session.
    """
    if not guwp_token:
        logger.info("guwpToken not provided, using initial session")
        return ""

    import httpx

    login_url = f"{_SKILLHUB_BASE_URL}/api/v1/auth/third-party/login"
    body = json.dumps(
        {
            "loginMethod": "TOKEN",
            "platform": "GUWP",
            "token": guwp_token,
        },
    )
    try:
        async with httpx.AsyncClient(timeout=_SKILLHUB_TIMEOUT) as client:
            resp = await client.post(
                login_url,
                content=body,
                headers=_skillhub_headers(""),
            )
        resp.raise_for_status()
        new_session_id = resp.headers.get("x-session-id", "")
        if new_session_id:
            logger.info(
                "Refreshed skillhub cookie with session %s...",
                new_session_id[:20],
            )
            return f"SESSION={new_session_id}"
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Failed to refresh skillhub cookie: %s",
            e,
            exc_info=True,
        )

    return ""


@workspace_router.get(
    "/agents/{agent_id}/skills",
    response_model=AgentSkillsListResponse,
    summary="Get Agent Skills",
    description=(
        "Query the external skillhub catalog and return it to the "
        "frontend, marking as ``used`` the skills the agent's workspace "
        "already holds (``workspaces/<agent_id>/skills``)."
    ),
)
async def get_agent_skills(
    agent_id: str,
    page: int = Query(default=0, ge=0),
    q: str = Query(default=""),
    size: int = Query(default=10, ge=1, le=200),
    sort: str = Query(default=""),
    label: str = Query(default=""),
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> AgentSkillsListResponse:
    """Query the external skillhub and return the skill list.

    ``used`` reflects the skills already present in the agent's
    workspace directory. With :class:`LocalWorkspaceManager` the workdir
    is derived from ``agent_id`` alone (``basedir/agent_id``), so no
    session is required to locate it.

    The remote endpoint mirrors these parameters
    (``page/q/size/sort/label``, ``namespace=global``) and answers
    with ``{data: {items: [{slug, summary, ...}], total}}``.
    """
    # Validate ownership: the agent must belong to (or be shared with)
    # the caller, otherwise 404 — mirrors every other agent-scoped route.
    await access.resolve_agent(user_id, agent_id)

    import httpx

    cookie = await _refresh_skillhub_cookie(guwp_token)

    # "Used" = skills already equipped in the agent's workspace
    # (workspaces/<agent_id>/skills). Resolution failure (no workspace
    # yet, or a backend that needs a real session) must not fail the
    # whole request — fall back to marking nothing as used.
    try:
        workspace = await workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id="",
            workspace_id=None,
        )
        agent_skills = await workspace.list_skills()
        used_names = {s.name for s in agent_skills}
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to resolve workspace for agent %s, marking no "
            "skills as used: %s",
            agent_id,
            e,
        )
        used_names = set()

    url = (
        f"{_SKILLHUB_BASE_URL}/api/web/skills"
        f"?page={page}&q={quote(q, safe='')}&size={size}&sort={sort}"
        f"&label={quote(label, safe='')}&namespace=global"
    )
    try:
        async with httpx.AsyncClient(timeout=_SKILLHUB_TIMEOUT) as client:
            resp = await client.get(url, headers=_skillhub_headers(cookie))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to fetch remote skills: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch remote skills: {e}",
        ) from e

    items = (data.get("data") or {}).get("items") or []
    total = (data.get("data") or {}).get("total") or 0

    skills_list = [
        SkillInfo(
            name=item.get("slug", ""),
            category="public",
            description=item.get("summary", "") or "",
            used=item.get("slug", "") in used_names,
        )
        for item in items
        if item.get("slug")
    ]
    return AgentSkillsListResponse(skills=skills_list, total=total)


# ---------------------------------------------------------------------------
# Remote skillhub download helper
# ---------------------------------------------------------------------------

#: Alias kept for readability — the external skillhub base URL.
REMOTE_SKILLHUB_URL = "http://53.12.9.18/skillhub-server/api/web/skills/global"


def _remove_if_empty(path: Path) -> None:
    """Remove ``path`` when it exists and contains nothing."""
    try:
        if path.exists() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


async def _download_skill_from_remote(
    skill_name: str,
    guwp_token: str | None = None,
    target_dir: str | Path | None = None,
) -> bool:
    """Download a skill from the remote skillhub server.

    Args:
        skill_name: The skill name to download.
        guwp_token: Token from the request header for cookie refresh.
        target_dir: Directory the skill is extracted into (a
            ``<target_dir>/<skill_name>`` subdirectory is created).
            Defaults to ``$SKILLHUB_SKILLS_DIR`` when the env var is
            set, otherwise ``./downloaded_skills``.

    Returns:
        True if downloaded and saved successfully, False otherwise.
    """
    import zipfile

    import httpx

    cookie = await _refresh_skillhub_cookie(guwp_token)

    encoded_skill_name = quote(skill_name, safe="")
    download_url = f"{REMOTE_SKILLHUB_URL}/{encoded_skill_name}/download"
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
    }

    logger.info("Downloading skill from: %s", download_url)

    if target_dir is None:
        target_dir = os.environ.get("SKILLHUB_SKILLS_DIR", "downloaded_skills")
    skill_dir = Path(target_dir) / skill_name

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        zip_path = skill_dir / f"{skill_name}.zip"

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.get(download_url, headers=headers)

        if resp.status_code != 200:
            logger.error(
                "Failed to download skill %s: HTTP %d",
                skill_name,
                resp.status_code,
            )
            _remove_if_empty(skill_dir)
            return False

        zip_path.write_bytes(resp.content)
        logger.info("Downloaded skill %s to %s", skill_name, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(skill_dir)
        logger.info("Extracted skill %s to %s", skill_name, skill_dir)

        zip_path.unlink()
        logger.info("Removed zip file %s", zip_path)
        return True

    except Exception as e:  # noqa: BLE001
        logger.error(
            "Failed to download skill %s: %s",
            skill_name,
            e,
            exc_info=True,
        )
        _remove_if_empty(skill_dir)
        return False


@workspace_router.post(
    "/agents/{agent_id}/skills/{skill_full_name}",
    response_model=SkillActionResponse,
    summary="Enable Skill for Agent",
    description=(
        "Add a skill to the agent's workspace. When the skill is not "
        "already equipped, it is downloaded from the remote skillhub "
        "and extracted into ``workspaces/<agent_id>/skills``."
    ),
)
async def enable_agent_skill(
    agent_id: str,
    skill_full_name: str,
    guwp_token: str | None = Header(default=None, alias="guwpToken"),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> SkillActionResponse:
    """Enable a skill for a specific agent.

    ``skill_full_name`` follows the ``category:name`` convention (e.g.
    ``public:writing``); only ``public`` skills are downloadable. When
    the skill is already equipped in the agent's workspace the call is
    a no-op success. Equipping means the skill directory lands under
    ``workspaces/<agent_id>/skills/<name>`` — the workspace's
    ``list_skills`` picks it up on the next chat turn.
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

    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id="",
        workspace_id=None,
    )
    backend = workspace.get_backend()
    skill_dir = backend.join_path(workspace.workdir, "skills", skill_name)

    # Already equipped — no-op success.
    existing = await workspace.list_skills()
    if any(s.dir == skill_dir for s in existing):
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

    # Download from the remote skillhub straight into the workspace's
    # skills/ directory (target_dir → <target>/<skill_name>).
    ok = await _download_skill_from_remote(
        skill_name,
        guwp_token=guwp_token,
        target_dir=backend.join_path(workspace.workdir, "skills"),
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to download skill '{skill_name}' from remote.",
        )

    # Verify the download produced a usable skill (SKILL.md with the
    # required front matter); list_skills also refreshes the index.
    refreshed = await workspace.list_skills()
    if not any(s.dir == skill_dir for s in refreshed):
        await backend.delete_path(skill_dir)
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
