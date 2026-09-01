# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
import mimetypes
from urllib.parse import quote

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
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from ..deps import (
    get_current_user_id,
    get_skill_hubs,
    get_storage,
    get_workspace_service,
)
from ..hub import SkillHubBase
from .._service import WorkspaceService, WorkspaceStatus
from .._service._workspace import SkillUploadError, UploadManifest
from ..storage import MCPRecord, StorageBase
from ...mcp import MCPClient
from ...skill import Skill
from ._schema import (
    AddFromLibraryRequest,
    AddFromLibraryResponse,
    AddSkillRequest,
    AddSkillsFromLibraryRequest,
    DirectoryEntry,
    DirectoryListing,
    DownloadTokenResponse,
    MCPClientStatus,
    ToolInfo,
)
from ..._utils._common import _describe_exception

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/mcp")
async def list_mcps(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    clients = await workspace.list_mcps(
        agent_id=agent_id,
        session_id=session_id,
    )

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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Add an MCP client to the session's workspace.

    The MCP is also recorded in the user's library, so one typed in by
    hand is reusable in the next session instead of being retyped. An
    existing record of the same name is left alone: the library is where
    that MCP is defined, and adding it to a second workspace must not
    silently redefine it.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    try:
        await workspace.add_mcp(mcp, agent_id=agent_id, session_id=session_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> AddFromLibraryResponse:
    """Put MCPs the user has already installed into this workspace.

    The rendered config never leaves the server, so the client sends ids
    rather than configs — it has no way to reconstruct one.

    Adding is per-MCP: one that fails to connect does not cancel the
    rest, and the response says which ones landed.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    present = {
        client.name
        for client in await workspace.list_mcps(
            agent_id=agent_id,
            session_id=session_id,
        )
    }

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
            await workspace.add_mcp(
                record.client,
                agent_id=agent_id,
                session_id=session_id,
            )
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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Remove an MCP client from the session's workspace by name."""
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    await workspace.remove_mcp(
        mcp_name,
        agent_id=agent_id,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Skill endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/skill")
async def list_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    return await workspace.list_skills(agent_id=agent_id)


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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Add a skill to the session's workspace from the given path.

    Deprecated: the path is resolved on the server, which only means
    anything for a single-host deployment. Use ``POST /skill/upload``
    to send a folder, or ``POST /skill/from-library`` to install one
    the user already has.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    await workspace.add_skill(body.skill_path, agent_id=agent_id)


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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Install a skill from an uploaded folder.

    The parts are re-tarred on the fly and piped into the workspace, so
    the archive is never held whole. The manifest is what the client
    claims; every limit in it is re-checked here, and the byte counts
    are verified as the tar is built.
    """
    try:
        parsed = UploadManifest.model_validate_json(manifest)
        workspace_service.validate_manifest(parsed, len(files))
    except (ValidationError, SkillUploadError) as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            str(e),
        ) from e

    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    try:
        # The name is unused: the tar members already carry the picked
        # folder as their first path segment.
        await workspace_service.install_skill(
            workspace,
            workspace_service.tar_stream(parsed, files),
            "tar",
            "skill",
            agent_id=agent_id,
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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    skill_hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> AddFromLibraryResponse:
    """Put skills the user has already installed into this workspace.

    Each one is re-downloaded from its hub and piped into the
    workspace; the server holds no copy in between. Adding is
    per-skill, and the response says which ones landed.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
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
            archive = await hub.download(
                user_id,
                record.card_id or record.name,
                record.version,
            )
            await workspace_service.install_skill(
                workspace,
                archive.stream,
                archive.format,
                record.name,
                agent_id=agent_id,
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
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    """Remove a skill from the session's workspace by name."""
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    await workspace.remove_skill(skill_name, agent_id=agent_id)


# ---------------------------------------------------------------------------
# File browsing endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/directories")
async def list_workspace_directory(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(
        default="",
        description=(
            "Absolute path, or one relative to the workspace root. "
            "Empty lists the workspace root itself."
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> DirectoryListing:
    """List one directory level, reachable from a session's workspace.

    Paths are not confined to the workspace root: for a sandboxed
    backend the reachable filesystem is the sandbox, and for a local
    one the caller is already trusted with the host.

    The resolved absolute path comes back alongside the entries, so a
    caller browsing with relative paths can still show where it is.
    """
    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    backend = workspace.get_backend()
    target = backend.abspath(path, cwd=workspace.workdir)

    entry = await backend.stat(target)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found.",
        )
    if not entry.is_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested path is a file, not a directory.",
        )

    # One call for the whole directory: asking per entry would be one
    # round trip each on a sandboxed backend, times three attributes.
    return DirectoryListing(
        path=target,
        entries=[
            DirectoryEntry(
                name=entry.name,
                is_dir=entry.is_dir,
                size_bytes=entry.size_bytes,
                updated_at=entry.mtime,
            )
            for entry in await backend.scandir(target)
        ],
    )


@workspace_router.get("/status")
async def get_workspace_status(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceStatus:
    """Report where a session is pointed, and the git state of that place.

    The directory comes from the session's own ``cwd`` rather than a
    query parameter: it is the session's anchor, and resolving a
    relative one needs the workspace root the client cannot see.

    Git is best-effort. A directory that is not a repository is the
    normal case, not an error, and the sandboxed backends differ in how
    they report a missing binary or an unreachable container — so every
    failure collapses to ``git: null`` and the rest of the response is
    still served.
    """
    return await workspace_service.read_status(
        user_id,
        agent_id,
        session_id,
    )


@workspace_router.post("/files/download-token")
async def create_download_token(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(..., description="The path the token will authorize."),
    user_id: str = Depends(get_current_user_id),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> DownloadTokenResponse:
    """Mint a short-lived token for a browser-native download.

    The browser writes the response straight to disk only when it
    issues the request itself, and such a request carries no custom
    header — hence a credential in the URL. Fetching with ``X-User-ID``
    instead works but holds the whole file in the tab.

    Minting depends on the normal identity, so whatever replaces
    ``X-User-ID`` guards this too.

    The session is resolved here only to fail early: the download is a
    browser navigation, so an error there surfaces as a raw error page
    rather than something the UI can show.
    """
    await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    # Signed verbatim, not resolved: the download verifies against the
    # query string it receives, and resolving needs a user the token
    # has not been read yet to supply.
    token, expires_at = workspace_service.sign_download_token(
        user_id,
        path,
    )
    return DownloadTokenResponse(token=token, expires_at=expires_at)


@workspace_router.get("/files")
async def read_workspace_file(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(
        ...,
        description="Absolute path, or one relative to the workspace root.",
    ),
    download: bool = Query(
        default=False,
        description="Force a Content-Disposition attachment.",
    ),
    token: str
    | None = Query(
        default=None,
        description=(
            "A token from ``POST /workspace/files/download-token``, "
            "accepted in place of the ``X-User-ID`` header so a browser "
            "navigation can download the file directly."
        ),
    ),
    x_user_id: str | None = Header(default=None),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> StreamingResponse:
    """Stream one file out of a session's workspace.

    The body is piped chunk by chunk rather than read whole: the API
    process is shared, so one large download must not be able to
    exhaust it for everyone else.
    """
    if token is not None:
        try:
            user_id = workspace_service.verify_download_token(token, path)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            ) from e
    elif x_user_id:
        user_id = x_user_id
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header or download token is required.",
        )

    workspace = await workspace_service.resolve(
        user_id,
        agent_id,
        session_id,
    )
    backend = workspace.get_backend()
    target = backend.abspath(path, cwd=workspace.workdir)
    basename = backend.basename(target) or "download"

    entry = await backend.stat(target)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )
    if entry.is_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested path is a directory, not a file.",
        )

    headers: dict[str, str] = {}
    # Lets the browser show real download progress instead of a
    # spinner of unknown length; omitted when the backend cannot stat.
    if entry.size_bytes is not None:
        headers["Content-Length"] = str(entry.size_bytes)
    if download:
        headers[
            "Content-Disposition"
        ] = f"attachment; filename*=UTF-8''{quote(basename)}"

    return StreamingResponse(
        backend.read_stream(target),
        media_type=(
            mimetypes.guess_type(basename)[0] or "application/octet-stream"
        ),
        headers=headers,
    )
