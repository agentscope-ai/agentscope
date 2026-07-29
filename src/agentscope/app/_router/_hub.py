# -*- coding: utf-8 -*-
"""Hub router — browse resource hubs and install from them.

The frontend flow is three levels deep: list the hubs, browse one hub's
cards, then install a chosen card into the caller's session workspace.
Cards are never merged across hubs, which keeps ranking a per-hub
concern.
"""
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..deps import (
    get_current_user_id,
    get_mcp_hubs,
    get_skill_hubs,
    get_storage,
    get_workspace_manager,
    resolve_workspace,
)
from ..hub import (
    HubBase,
    MCPCard,
    MCPHubBase,
    MCPHubPage,
    MCPRenderError,
    SkillCard,
    SkillFetchError,
    SkillHubBase,
    SkillHubPage,
    fetch_skill_dir,
    render_mcp,
)
from ..storage import StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ...workspace import WorkspaceBase

hub_router = APIRouter(prefix="/hub", tags=["hub"])

HubT = TypeVar("HubT", bound=HubBase)


class HubInfo(BaseModel):
    """One registered hub, as shown in the hub picker."""

    hub_id: str = Field(description="The id addressing this hub.")
    display_name: str = Field(description="The user-facing hub name.")
    description: str = Field(description="The user-facing description.")


class InstallMCPRequest(BaseModel):
    """The body of an MCP install call."""

    name: str | None = Field(
        default=None,
        description=(
            "The name to install under, defaulting to the card's name. "
            "Must match ``[a-zA-Z0-9_-]+``; use it to resolve a clash "
            "with an MCP already in the workspace."
        ),
    )
    values: dict = Field(
        default_factory=dict,
        description=(
            "The answers to the card's ``inputs_schema``, e.g. API keys."
        ),
    )


class InstallResponse(BaseModel):
    """What was installed, and which card it came from.

    The rendered config is deliberately not echoed back — it holds the
    secrets the caller just submitted.
    """

    name: str = Field(
        description=(
            "The name the resource ended up under in the workspace. For "
            "skills this is read back from the workspace rather than "
            "requested, because backends derive it from ``SKILL.md``; it "
            "falls back to the card id when ``already_present`` is set, "
            "since no new skill appeared to read the name from."
        ),
    )
    hub_id: str = Field(description="The hub the card came from.")
    card_id: str = Field(description="The card's id on that hub.")
    already_present: bool = Field(
        default=False,
        description=(
            "Whether the workspace already held this resource, in which "
            "case nothing was added."
        ),
    )


def _pick_hub(hubs: dict[str, HubT], hub_id: str) -> HubT:
    """Return the hub under ``hub_id`` or raise 404.

    Args:
        hubs (`dict[str, HubT]`):
            The registered hubs keyed by id.
        hub_id (`str`):
            The requested hub id.

    Returns:
        `HubT`:
            The matching hub, keeping its concrete type.

    Raises:
        `HTTPException`:
            ``404`` when no hub is registered under ``hub_id``.
    """
    hub = hubs.get(hub_id)
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} is not registered.",
        )
    return hub


def _describe(hubs: dict) -> list[HubInfo]:
    """Render the hub picker entries in a stable order.

    Args:
        hubs (`dict`):
            The registered hubs keyed by id.

    Returns:
        `list[HubInfo]`:
            One entry per hub, ordered by id.
    """
    return [
        HubInfo(
            hub_id=hub.hub_id,
            display_name=hub.display_name,
            description=hub.description,
        )
        for _, hub in sorted(hubs.items())
    ]


async def _session_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
) -> WorkspaceBase:
    """Resolve the workspace an install lands in.

    Args:
        user_id (`str`):
            The authenticated user ID.
        agent_id (`str`):
            The agent owning the session.
        session_id (`str`):
            The session to install into.
        storage (`StorageBase`):
            The storage used to look the session record up.
        workspace_manager (`WorkspaceManagerBase`):
            The manager that opens or reattaches the workspace.

    Returns:
        `WorkspaceBase`:
            The session's workspace.
    """
    return await resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )


# ---------------------------------------------------------------------------
# MCP hubs
# ---------------------------------------------------------------------------


@hub_router.get("/mcp")
async def list_mcp_hubs(
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
) -> list[HubInfo]:
    """Return every registered MCP hub."""
    return _describe(hubs)


@hub_router.get("/mcp/{hub_id}/cards")
async def list_mcp_cards(
    hub_id: str,
    *,
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
) -> MCPHubPage:
    """Browse or search one MCP hub's catalog."""
    hub = _pick_hub(hubs, hub_id)
    return await hub.list_mcps(user_id, q=q, cursor=cursor, limit=limit)


@hub_router.get("/mcp/{hub_id}/cards/{card_id}")
async def get_mcp_card(
    hub_id: str,
    card_id: str,
    *,
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
) -> MCPCard:
    """Return one MCP card, including the inputs the user must fill."""
    hub = _pick_hub(hubs, hub_id)
    try:
        return await hub.get_mcp(user_id, card_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} has no MCP {card_id!r}.",
        ) from e


@hub_router.post(
    "/mcp/{hub_id}/cards/{card_id}/install",
    status_code=status.HTTP_201_CREATED,
)
async def install_mcp(
    hub_id: str,
    card_id: str,
    body: InstallMCPRequest,
    *,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> InstallResponse:
    """Fill a card's template and add the result to the workspace.

    ``workspace.add_mcp`` connects the client, so a wrong API key fails
    here rather than silently installing a broken MCP.
    """
    hub = _pick_hub(hubs, hub_id)
    try:
        card = await hub.get_mcp(user_id, card_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} has no MCP {card_id!r}.",
        ) from e

    try:
        client = render_mcp(card, body.values, body.name)
    except MCPRenderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    workspace = await _session_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    # Checked here rather than left to the backend: only the sandboxed
    # workspaces reject duplicates, and a clash deserves a 409 anyway.
    if any(m.name == client.name for m in await workspace.list_mcps()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An MCP named {client.name!r} is already in this "
                f"workspace. Pass a different 'name' to install anyway."
            ),
        )

    try:
        await workspace.add_mcp(client)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to connect MCP {client.name!r}: {e}",
        ) from e

    # TODO: persist an installed-MCP record here so the card's origin
    # survives. ``MCPClient`` carries no provenance, so once the client
    # reaches the workspace, ``(hub_id, card_id)`` is only known to this
    # response — the UI cannot answer "where did this come from" or
    # "is there a newer version" for an already-installed MCP.
    return InstallResponse(
        name=client.name,
        hub_id=card.hub_id,
        card_id=card.id,
    )


# ---------------------------------------------------------------------------
# Skill hubs
# ---------------------------------------------------------------------------


@hub_router.get("/skill")
async def list_skill_hubs(
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> list[HubInfo]:
    """Return every registered skill hub."""
    return _describe(hubs)


@hub_router.get("/skill/{hub_id}/cards")
async def list_skill_cards(
    hub_id: str,
    *,
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillHubPage:
    """Browse or search one skill hub's catalog."""
    hub = _pick_hub(hubs, hub_id)
    return await hub.list_skills(user_id, q=q, cursor=cursor, limit=limit)


@hub_router.get("/skill/{hub_id}/cards/{card_id}")
async def get_skill_card(
    hub_id: str,
    card_id: str,
    *,
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
) -> SkillCard:
    """Return one skill card, including its ``SKILL.md`` body."""
    hub = _pick_hub(hubs, hub_id)
    try:
        return await hub.get_skill(user_id, card_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} has no skill {card_id!r}.",
        ) from e


@hub_router.post(
    "/skill/{hub_id}/cards/{card_id}/install",
    status_code=status.HTTP_201_CREATED,
)
async def install_skill(
    hub_id: str,
    card_id: str,
    *,
    version: str | None = Query(default=None),
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> InstallResponse:
    """Download a skill archive and unpack it into the workspace.

    The installed name is not the caller's to choose: backends derive it
    from the ``SKILL.md`` frontmatter and resolve clashes themselves, and
    a content-identical skill is skipped outright. So the workspace is
    read back afterwards and the response reports what actually landed.
    """
    hub = _pick_hub(hubs, hub_id)
    workspace = await _session_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )

    before = {skill.name for skill in await workspace.list_skills()}

    try:
        async with fetch_skill_dir(hub, card_id, version=version) as path:
            await workspace.add_skill(path)
    except SkillFetchError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} has no skill {card_id!r}.",
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    added = [
        skill.name
        for skill in await workspace.list_skills()
        if skill.name not in before
    ]
    return InstallResponse(
        name=added[0] if added else card_id,
        hub_id=hub.hub_id,
        card_id=card_id,
        already_present=not added,
    )
