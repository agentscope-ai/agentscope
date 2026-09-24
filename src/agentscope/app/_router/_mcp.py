# -*- coding: utf-8 -*-
"""MCP router — the user's own library of installed MCPs.

This is the user-level collection an install lands in, distinct from
``/workspace/mcp``, which manages the MCPs of one session's workspace.
Installing from a hub only writes here; putting an MCP into a workspace
stays a separate, explicit act.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_current_user_id, get_mcp_hubs, get_storage
from ..hub import MCPHubBase
from .._service import MCPRenderError, render_mcp
from ..storage import StorageBase
from ._schema import MCPView, UpdateMCPRequest

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])


@mcp_router.get("")
async def list_mcps(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> list[MCPView]:
    """Return every MCP the user has installed, ordered by name."""
    records = await storage.list_mcps(user_id)
    return sorted(
        (MCPView.from_record(r) for r in records),
        key=lambda view: view.name,
    )


@mcp_router.patch("/{mcp_id}")
async def update_mcp(
    mcp_id: str,
    body: UpdateMCPRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    hubs: dict[str, MCPHubBase] = Depends(get_mcp_hubs),
) -> MCPView:
    """Rename, enable/disable, or re-key an installed MCP.

    Changing a secret is a re-render, not a field edit: an API key can
    sit anywhere in the config — inside a URL, a header, an env var — so
    the card's template plus the merged answers is the only thing that
    knows where to put it.
    """
    record = await storage.get_mcp(user_id, mcp_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No installed MCP with id {mcp_id!r}.",
        )

    if body.values is not None:
        hub = hubs.get(record.hub_id or "")
        if hub is None or record.card_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This MCP has no card to re-render from — its hub is "
                    "not registered, or it was added by hand."
                ),
            )
        try:
            card = await hub.get_mcp(user_id, record.card_id)
        except KeyError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Hub {record.hub_id!r} no longer has the card this "
                    f"MCP was installed from."
                ),
            ) from e

        # Merged, not replaced: the client sends only what changed, and
        # a write-only field the form did not echo back must survive.
        merged = {**record.values, **body.values}
        try:
            record.client = render_mcp(
                card,
                merged,
                body.name or record.client.name,
            )
        except MCPRenderError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
        record.values = merged
        record.version = card.version
    elif body.name is not None:
        record.client.name = body.name

    if body.enabled is not None:
        record.enabled = body.enabled

    try:
        await storage.upsert_mcp(user_id, record)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    return MCPView.from_record(record)


@mcp_router.delete("/{mcp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp(
    mcp_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Remove an MCP from the user's library.

    Workspaces that already hold this MCP keep it — the workspace
    relation is derived by name, not a foreign key, and tearing down a
    live stateful MCP session as a side effect of a library edit would
    surprise the user.
    """
    if not await storage.delete_mcp(user_id, mcp_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No installed MCP with id {mcp_id!r}.",
        )
