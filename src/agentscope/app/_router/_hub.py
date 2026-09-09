# -*- coding: utf-8 -*-
"""Hub router — browse resource hubs and install from them.

The frontend flow is three levels deep: list the hubs, browse one hub's
cards, then install a chosen card into the caller's library. Cards are
never merged across hubs, which keeps ranking a per-hub concern.

Installing is user-level for both kinds — nothing here touches a
workspace. See ``_mcp.py`` / ``_skill.py`` for the libraries it writes.
"""
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import (
    get_current_user_id,
    get_mcp_hubs,
    get_skill_hubs,
    get_storage,
)
from ..hub import (
    HubBase,
    MCPCard,
    MCPHubBase,
    MCPHubPage,
    SkillCard,
    SkillHubBase,
    SkillHubPage,
)
from .._service import MCPRenderError, render_mcp
from ..storage import MCPRecord, SkillRecord, StorageBase
from ._schema import HubInfo, InstallMCPRequest, MCPView, SkillView

hub_router = APIRouter(prefix="/hub", tags=["hub"])

HubT = TypeVar("HubT", bound=HubBase)


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
            icon_url=hub.icon_url,
        )
        for _, hub in sorted(hubs.items())
    ]


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
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
    storage: StorageBase = Depends(get_storage),
) -> MCPView:
    """Fill a card's template and add the result to the user's library.

    Installing is a user-level act with no workspace involved — putting
    the MCP into a session's workspace is separate and explicit. The
    record keeps ``(hub_id, card_id)`` so the library can later answer
    "where did this come from" and "is there a newer version".

    Note that the config is not connection-tested here, so a mistyped
    API key surfaces on first use rather than now. Testing it would mean
    connecting from the app process, and a stdio MCP would then spawn
    its command here instead of in the workspace sandbox.
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

    record = MCPRecord(
        user_id=user_id,
        client=client,
        values=body.values,
        display_name=card.display_name,
        description=card.description,
        tags=card.tags,
        author=card.author,
        icon_url=card.icon_url,
        url=card.url,
        hub_id=card.hub_id,
        card_id=card.id,
        version=card.version,
    )
    try:
        await storage.upsert_mcp(user_id, record)
    except ValueError as e:
        # The name is unique per user, so a clash is the caller's to
        # resolve by passing a different one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{e} Pass a different 'name' to install it alongside "
                f"the existing one."
            ),
        ) from e

    return MCPView.from_record(record)


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


@hub_router.post(
    "/skill/{hub_id}/cards/{card_id:path}/install",
    status_code=status.HTTP_201_CREATED,
)
async def install_skill(
    hub_id: str,
    card_id: str,
    *,
    name: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    hubs: dict[str, SkillHubBase] = Depends(get_skill_hubs),
    storage: StorageBase = Depends(get_storage),
) -> SkillView:
    """Add a skill card to the user's library.

    Mirrors the MCP install: user-level, no workspace involved. The
    archive is not downloaded here — only the card's metadata is
    recorded, and the files are fetched when the skill is actually put
    into a workspace. So a hub that has since dropped the card fails
    then rather than now.
    """
    hub = _pick_hub(hubs, hub_id)
    try:
        card = await hub.get_skill(user_id, card_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub {hub_id!r} has no skill {card_id!r}.",
        ) from e

    record = SkillRecord(
        user_id=user_id,
        name=name or card.name,
        display_name=card.display_name,
        description=card.description,
        tags=card.tags,
        author=card.author,
        icon_url=card.icon_url,
        url=card.url,
        markdown=card.markdown or "",
        hub_id=card.hub_id,
        card_id=card.id,
        version=card.version,
    )
    try:
        await storage.upsert_skill(user_id, record)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{e} Pass a different 'name' to install it alongside "
                f"the existing one."
            ),
        ) from e

    return SkillView.from_record(record)


# Keep this catch-all path route after the more specific ``/install`` route.
# This keeps route precedence explicit and protects specific routes from
# accidental shadowing as more card actions are added.
@hub_router.get("/skill/{hub_id}/cards/{card_id:path}")
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
