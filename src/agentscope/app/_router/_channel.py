# -*- coding: utf-8 -*-
"""Channel HTTP API routes.

Endpoints:
    GET    /channels/types           - List channel types + schemas
    GET    /channels/                   - List all channels
    POST   /channels/                   - Create channel
    GET    /channels/{channel_id}       - Get channel details
    PATCH  /channels/{channel_id}       - Update channel
    DELETE /channels/{channel_id}       - Delete channel
    POST   /channels/{channel_id}/enable   - Enable channel
    POST   /channels/{channel_id}/disable  - Disable channel
    POST   /channels/{channel_id}/test     - Test connection
    GET    /channels/{channel_id}/status   - Runtime status
    GET    /channels/{channel_id}/bindings - List routing rules
    POST   /channels/{channel_id}/bindings - Add routing rule
    DELETE /channels/{channel_id}/bindings/{idx} - Remove routing rule
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..deps import get_current_user_id
from ..channel import (
    ChannelNotFoundError,
    DuplicateBotError,
    ChannelManager,
    ChannelTypeRegistry,
)
from ..storage import ChannelRecord, ChannelRoutingRule

channel_router = APIRouter(
    prefix="/channels",
    tags=["channels"],
)


# ── Request/Response Schemas ──


class CreateChannelRequest(BaseModel):
    """Request body for creating a new channel."""

    channel_id: str
    channel_type: str
    credentials: dict
    default_agent_id: str
    chat_model_config: dict | None = None
    fallback_chat_model_config: dict | None = None
    routing_rules: list[ChannelRoutingRule] = Field(default_factory=list)
    dm_scope: str = "PER_CHAT"
    permission_mode: str = "dont_ask"
    config: dict[str, Any] = Field(default_factory=dict)
    filter_tool_messages: bool = False
    filter_thinking_messages: bool = True
    enabled: bool = True


class UpdateChannelRequest(BaseModel):
    """Request body for updating a channel."""

    default_agent_id: str | None = None
    chat_model_config: dict | None = None
    fallback_chat_model_config: dict | None = None
    routing_rules: list[ChannelRoutingRule] | None = None
    dm_scope: str | None = None
    permission_mode: str | None = None
    config: dict[str, Any] | None = None
    filter_tool_messages: bool | None = None
    filter_thinking_messages: bool | None = None
    enabled: bool | None = None


class AddBindingRequest(BaseModel):
    """Request body for adding a routing rule."""

    metadata_key: str
    metadata_value: str
    agent_id: str
    priority: int = 0


class ChannelResponse(BaseModel):
    """Channel details response (credentials redacted)."""

    channel_id: str
    channel_type: str
    platform_bot_id: str
    default_agent_id: str
    chat_model_config: dict | None
    fallback_chat_model_config: dict | None = None
    routing_rules: list[ChannelRoutingRule]
    dm_scope: str
    permission_mode: str
    enabled: bool
    config: dict[str, Any]
    filter_tool_messages: bool
    filter_thinking_messages: bool


class BindingResponse(BaseModel):
    """A single routing rule with index as id."""

    id: int
    metadata_key: str
    metadata_value: str
    agent_id: str
    priority: int


def _to_response(record: ChannelRecord) -> ChannelResponse:
    return ChannelResponse(
        channel_id=record.channel_id,
        channel_type=record.channel_type,
        platform_bot_id=record.platform_bot_id,
        default_agent_id=record.default_agent_id,
        chat_model_config=record.chat_model_config,
        fallback_chat_model_config=record.fallback_chat_model_config,
        routing_rules=record.routing_rules,
        dm_scope=record.dm_scope,
        permission_mode=record.permission_mode,
        enabled=record.enabled,
        config=record.config,
        filter_tool_messages=record.filter_tool_messages,
        filter_thinking_messages=record.filter_thinking_messages,
    )


# ── Dependencies ──


async def _get_channel_manager(request: Request) -> ChannelManager:
    """Extract ChannelManager from app.state."""
    manager = getattr(request.app.state, "channel_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Channel feature is not enabled.",
        )
    return manager


async def _get_type_registry(request: Request) -> ChannelTypeRegistry:
    """Extract ChannelTypeRegistry from app.state."""
    registry = getattr(request.app.state, "channel_type_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Channel feature is not enabled.",
        )
    return registry


async def _get_owned_channel(
    channel_id: str,
    user_id: str,
    manager: ChannelManager,
) -> ChannelRecord:
    """Fetch a channel and verify the requesting user owns it."""
    record = await manager.get_channel_record(channel_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_id}' not found.",
        )
    if record.tenant_user_id and record.tenant_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this channel.",
        )
    return record


# ── Endpoints ──


@channel_router.get("/types")
async def list_channel_types(
    registry: ChannelTypeRegistry = Depends(_get_type_registry),
) -> list[dict]:
    """List all supported channel types with their JSON schemas."""
    return [t.model_dump() for t in registry.list_types()]


@channel_router.get("/")
async def list_channels(
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> list[ChannelResponse]:
    """List channels owned by the current user."""
    records = await manager.list_channel_records()
    owned = [r for r in records if r.tenant_user_id == user_id]
    return [_to_response(r) for r in owned]


@channel_router.post("/", status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: CreateChannelRequest,
    manager: ChannelManager = Depends(_get_channel_manager),
    registry: ChannelTypeRegistry = Depends(_get_type_registry),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Create a new channel instance."""
    if not body.chat_model_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_model_config is required for channel creation.",
        )

    try:
        platform_bot_id = registry.extract_platform_bot_id(
            body.channel_type,
            body.credentials,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    record = ChannelRecord(
        channel_id=body.channel_id,
        channel_type=body.channel_type,
        platform_bot_id=platform_bot_id,
        credentials=body.credentials,
        default_agent_id=body.default_agent_id,
        chat_model_config=body.chat_model_config,
        fallback_chat_model_config=body.fallback_chat_model_config,
        routing_rules=body.routing_rules,
        dm_scope=body.dm_scope,
        permission_mode=body.permission_mode,
        enabled=body.enabled,
        config=body.config,
        filter_tool_messages=body.filter_tool_messages,
        filter_thinking_messages=body.filter_thinking_messages,
        tenant_user_id=user_id,
    )

    try:
        await manager.add_channel(record)
    except DuplicateBotError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    return _to_response(record)


@channel_router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Get channel details by id."""
    record = await _get_owned_channel(channel_id, user_id, manager)
    return _to_response(record)


@channel_router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    body: UpdateChannelRequest,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Update channel configuration."""
    await _get_owned_channel(channel_id, user_id, manager)

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    try:
        record = await manager.update_channel(channel_id, updates)
    except ChannelNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return _to_response(record)


@channel_router.delete(
    "/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_channel(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Delete a channel and clean up all associated resources."""
    await _get_owned_channel(channel_id, user_id, manager)
    await manager.remove_channel(channel_id)


@channel_router.post("/{channel_id}/enable")
async def enable_channel(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Enable a disabled channel."""
    await _get_owned_channel(channel_id, user_id, manager)
    try:
        await manager.enable_channel(channel_id)
    except ChannelNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return {"status": "enabled"}


@channel_router.post("/{channel_id}/disable")
async def disable_channel(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Disable and stop a running channel."""
    await _get_owned_channel(channel_id, user_id, manager)
    try:
        await manager.disable_channel(channel_id)
    except ChannelNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return {"status": "disabled"}


@channel_router.get("/{channel_id}/status")
async def channel_status(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Get runtime status of a channel."""
    await _get_owned_channel(channel_id, user_id, manager)
    return await manager.get_channel_status(channel_id)


@channel_router.post("/{channel_id}/test")
async def test_channel(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """Test channel connection (placeholder)."""
    await _get_owned_channel(channel_id, user_id, manager)
    return {"status": "ok", "message": "Connection test not yet implemented."}


# ── Binding (routing rule) management ──


@channel_router.get("/{channel_id}/bindings")
async def list_bindings(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> list[BindingResponse]:
    """List routing rules for a channel."""
    record = await _get_owned_channel(channel_id, user_id, manager)
    return [
        BindingResponse(
            id=i,
            metadata_key=rule.metadata_key,
            metadata_value=rule.metadata_value,
            agent_id=rule.agent_id,
            priority=rule.priority,
        )
        for i, rule in enumerate(record.routing_rules)
    ]


@channel_router.post(
    "/{channel_id}/bindings",
    status_code=status.HTTP_201_CREATED,
)
async def add_binding(
    channel_id: str,
    body: AddBindingRequest,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> BindingResponse:
    """Add a routing rule to a channel."""
    record = await _get_owned_channel(channel_id, user_id, manager)

    new_rule = ChannelRoutingRule(
        metadata_key=body.metadata_key,
        metadata_value=body.metadata_value,
        agent_id=body.agent_id,
        priority=body.priority,
    )
    updated_rules = list(record.routing_rules) + [new_rule]
    try:
        await manager.update_channel(
            channel_id,
            {"routing_rules": updated_rules},
        )
    except ChannelNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    idx = len(updated_rules) - 1
    return BindingResponse(
        id=idx,
        metadata_key=new_rule.metadata_key,
        metadata_value=new_rule.metadata_value,
        agent_id=new_rule.agent_id,
        priority=new_rule.priority,
    )


@channel_router.delete(
    "/{channel_id}/bindings/{binding_idx}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_binding(
    channel_id: str,
    binding_idx: int,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Remove a routing rule by index."""
    record = await _get_owned_channel(channel_id, user_id, manager)

    rules = list(record.routing_rules)
    if binding_idx < 0 or binding_idx >= len(rules):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Binding index {binding_idx} out of range.",
        )

    rules.pop(binding_idx)
    try:
        await manager.update_channel(
            channel_id,
            {"routing_rules": rules},
        )
    except ChannelNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@channel_router.get("/{channel_id}/chat_ids")
async def list_chat_ids(
    channel_id: str,
    manager: ChannelManager = Depends(_get_channel_manager),
    user_id: str = Depends(get_current_user_id),
) -> list[dict]:
    """List known chat_ids for a channel.

    Combines two sources:
    1. Chat_ids recorded from incoming messages (passive).
    2. Bot's chat list fetched from the platform API (active).

    Returns a list of {chat_id, name?, source} dicts.
    """
    await _get_owned_channel(channel_id, user_id, manager)

    seen_ids = await manager.session_mapper.list_seen_chat_ids(
        channel_id,
    )

    results: list[dict] = []

    # Fetch from platform API (may include name)
    bot_chats = await manager.gateway.list_bot_chats(channel_id)
    platform_ids = set()
    for chat in bot_chats:
        cid = chat.get("chat_id", "")
        if cid:
            platform_ids.add(cid)
            results.append(
                {
                    "chat_id": cid,
                    "name": chat.get("name", ""),
                    "source": "platform",
                },
            )

    # Add any seen chat_ids not already from platform
    for cid in seen_ids:
        if cid not in platform_ids:
            results.append(
                {
                    "chat_id": cid,
                    "name": "",
                    "source": "recorded",
                },
            )

    return results
