# -*- coding: utf-8 -*-
"""Channel HTTP API.

    GET    /channels/types              List channel types + schemas
    POST   /channels/bindings           Open a credential binding
    GET    /channels/bindings/{id}      Poll it, advancing one step
    POST   /channels/bindings/{id}/cancel   Abandon it
    GET    /channels/                   List the user's channels
    POST   /channels/                   Create a channel
    GET    /channels/{id}               Channel details
    PATCH  /channels/{id}               Update routing/session/config
    DELETE /channels/{id}               Delete a channel
    POST   /channels/{id}/enable        Enable
    POST   /channels/{id}/disable       Disable
    GET    /channels/{id}/status        Aggregated runtime status
    GET    /channels/{id}/chat_ids      Known chats (for routing config)
"""
from fastapi import APIRouter, Depends, HTTPException, status

from ..channel import (
    ChannelError,
    ChannelClients,
    ChannelStatus,
    ChannelTypeRegistry,
    ChannelTypeSchema,
)
from .._service import (
    ChannelService,
    CredentialBindingError,
    CredentialBindingService,
)
from .._service._credential_binding import BindingView
from ..deps import (
    get_channel_clients,
    get_credential_binding_service,
    get_channel_service,
    get_channel_type_registry,
    get_current_user_id,
    get_storage,
)
from ..storage import ChannelRecord, StorageBase
from ._schema import (
    ChannelActionResponse,
    ChannelChatId,
    ChannelChatIdsResponse,
    ChannelResponse,
    ChannelSessionsResponse,
    CreateChannelRequest,
    StartCredentialBindingRequest,
    UpdateChannelRequest,
)

channel_router = APIRouter(prefix="/channels", tags=["channels"])


def _to_response(
    record: ChannelRecord,
    registry: ChannelTypeRegistry,
) -> ChannelResponse:
    """Project a stored record into a client response (credentials hidden).

    Args:
        record (`ChannelRecord`): The stored channel record.
        registry (`ChannelTypeRegistry`): Used to derive the display-only
            ``platform_bot_id`` from the credentials.

    Returns:
        `ChannelResponse`: The safe, client-facing view.
    """
    try:
        bot_id = registry.extract_platform_bot_id(
            record.channel_type,
            record.credentials,
        )
    except ValueError:
        bot_id = ""
    return ChannelResponse(
        id=record.id,
        channel_type=record.channel_type,
        name=record.name,
        user_id=record.user_id,
        platform_bot_id=bot_id,
        enabled=record.enabled,
        platform_config=record.platform_config,
        routing=record.routing,
        session=record.session,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _owned(
    channel_id: str,
    user_id: str,
    storage: StorageBase,
) -> ChannelRecord:
    """Load a channel and assert the caller owns it.

    Args:
        channel_id (`str`): The channel to load.
        user_id (`str`): The caller.
        storage (`StorageBase`): Application storage.

    Returns:
        `ChannelRecord`: The owned record.

    Raises:
        `HTTPException`: 404 if it does not exist, 403 if owned by
        someone else.
    """
    record = await storage.get_channel(channel_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found.")
    if record.user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied.")
    return record


@channel_router.get("/types")
async def list_channel_types(
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
) -> list[ChannelTypeSchema]:
    """List supported channel types with their JSON schemas."""
    return registry.list_types()


@channel_router.get("/")
async def list_channels(
    storage: StorageBase = Depends(get_storage),
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    user_id: str = Depends(get_current_user_id),
) -> list[ChannelResponse]:
    """List channels owned by the current user."""
    records = await storage.list_channels(user_id)
    return [_to_response(r, registry) for r in records]


@channel_router.post("/", status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: CreateChannelRequest,
    service: ChannelService = Depends(get_channel_service),
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    bindings: CredentialBindingService = Depends(
        get_credential_binding_service,
    ),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Create a channel, from a completed binding or from the body."""
    credentials = body.credentials
    if body.credential_binding_id:
        try:
            credentials = await bindings.claim(
                user_id,
                body.credential_binding_id,
                body.channel_type,
            )
        except CredentialBindingError as e:
            raise HTTPException(e.status_code, str(e)) from e

    try:
        record = await service.create(
            user_id=user_id,
            channel_type=body.channel_type,
            name=body.name,
            credentials=credentials,
            platform_config=body.platform_config,
            routing=body.routing,
            session=body.session,
            enabled=body.enabled,
        )
    except ChannelError as e:
        raise HTTPException(e.status_code, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _to_response(record, registry)


@channel_router.post("/bindings", response_model=BindingView)
async def start_credential_binding(
    body: StartCredentialBindingRequest,
    bindings: CredentialBindingService = Depends(
        get_credential_binding_service,
    ),
    user_id: str = Depends(get_current_user_id),
) -> BindingView:
    """Open a session and return the URL the operator must approve.

    Returns immediately; the client polls for the outcome, and each poll
    is what advances the session.
    """
    try:
        return await bindings.start(user_id, body.channel_type)
    except CredentialBindingError as e:
        raise HTTPException(e.status_code, str(e)) from e


@channel_router.get("/bindings/{binding_id}", response_model=BindingView)
async def poll_credential_binding(
    binding_id: str,
    bindings: CredentialBindingService = Depends(
        get_credential_binding_service,
    ),
    user_id: str = Depends(get_current_user_id),
) -> BindingView:
    """Report the session, asking the platform once when due.

    Never returns the credentials themselves — those are handed over
    only by creating a channel with this ``binding_id``.
    """
    try:
        return await bindings.poll(user_id, binding_id)
    except CredentialBindingError as e:
        raise HTTPException(e.status_code, str(e)) from e


@channel_router.post("/bindings/{binding_id}/cancel")
async def cancel_credential_binding(
    binding_id: str,
    bindings: CredentialBindingService = Depends(
        get_credential_binding_service,
    ),
    user_id: str = Depends(get_current_user_id),
) -> ChannelActionResponse:
    """Abandon the session, from whichever replica takes this call."""
    try:
        await bindings.cancel(user_id, binding_id)
    except CredentialBindingError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return ChannelActionResponse(channel_id=binding_id)


@channel_router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Get channel details."""
    record = await _owned(channel_id, user_id, storage)
    return _to_response(record, registry)


@channel_router.patch("/{channel_id}")
async def update_channel(
    channel_id: str,
    body: UpdateChannelRequest,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Update routing / session / config / enabled."""
    await _owned(channel_id, user_id, storage)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No fields to update.",
        )
    try:
        record = await service.update(channel_id, updates)
    except ChannelError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return _to_response(record, registry)


@channel_router.delete(
    "/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_channel(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Delete a channel."""
    await _owned(channel_id, user_id, storage)
    await service.delete(channel_id)


@channel_router.post("/{channel_id}/enable")
async def enable_channel(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    user_id: str = Depends(get_current_user_id),
) -> ChannelActionResponse:
    """Enable a channel."""
    await _owned(channel_id, user_id, storage)
    await service.set_enabled(channel_id, True)
    return ChannelActionResponse(status="enabled")


@channel_router.post("/{channel_id}/disable")
async def disable_channel(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    user_id: str = Depends(get_current_user_id),
) -> ChannelActionResponse:
    """Disable a channel."""
    await _owned(channel_id, user_id, storage)
    await service.set_enabled(channel_id, False)
    return ChannelActionResponse(status="disabled")


@channel_router.get("/{channel_id}/status")
async def channel_status(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    user_id: str = Depends(get_current_user_id),
) -> ChannelStatus:
    """The channel's live connection status, from whichever node holds
    it — this replica need not be the one connected."""
    await _owned(channel_id, user_id, storage)
    return await service.get_status(channel_id)


@channel_router.get("/{channel_id}/sessions")
async def list_channel_sessions(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    user_id: str = Depends(get_current_user_id),
) -> ChannelSessionsResponse:
    """Sessions this channel spawned, newest first."""
    await _owned(channel_id, user_id, storage)
    sessions = await storage.list_sessions_by_channel(user_id, channel_id)
    return ChannelSessionsResponse(sessions=sessions, total=len(sessions))


@channel_router.get("/{channel_id}/chat_ids")
async def list_chat_ids(
    channel_id: str,
    storage: StorageBase = Depends(get_storage),
    service: ChannelService = Depends(get_channel_service),
    clients: ChannelClients = Depends(get_channel_clients),
    user_id: str = Depends(get_current_user_id),
) -> ChannelChatIdsResponse:
    """Known chats for routing config: platform list ∪ passively seen.

    The platform list is fetched over REST through a client, so it
    answers from a replica that holds no connection.
    """
    await _owned(channel_id, user_id, storage)
    chats: list[ChannelChatId] = []
    platform_ids: set[str] = set()
    channel = await clients.get(channel_id)
    for chat in await channel.list_bot_chats() if channel else []:
        cid = chat.get("chat_id", "")
        if cid:
            platform_ids.add(cid)
            chats.append(
                ChannelChatId(
                    chat_id=cid,
                    name=chat.get("name", ""),
                    source="platform",
                ),
            )
    for cid in await service.list_seen_chat_ids(channel_id):
        if cid not in platform_ids:
            chats.append(ChannelChatId(chat_id=cid, source="recorded"))
    return ChannelChatIdsResponse(chats=chats)
