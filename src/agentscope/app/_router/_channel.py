# -*- coding: utf-8 -*-
"""Channel HTTP API.

GET    /channels/types              List channel types + schemas
GET    /channels/                   List the user's channels
POST   /channels/                   Create a channel
POST   /channels/bindings           Start QR credential binding
GET    /channels/bindings/{id}      Poll QR credential binding
DELETE /channels/bindings/{id}      Cancel QR credential binding
GET    /channels/{id}               Channel details
PATCH  /channels/{id}               Update routing/session/config
DELETE /channels/{id}               Delete a channel
POST   /channels/{id}/enable        Enable
POST   /channels/{id}/disable       Disable
GET    /channels/{id}/status        Aggregated runtime status
GET    /channels/{id}/chat_ids      Known chats (for routing config)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from ..channel import (
    ChannelCredentialBindingBase,
    ChannelCredentialBindingSession,
    ChannelCredentialBindingStore,
    ChannelCredentialBindingStatus,
    ChannelError,
    ChannelLifecycleDispatcher,
    ChannelStatus,
    ChannelTypeRegistry,
    ChannelTypeSchema,
)
from .._service import ChannelService
from ..deps import (
    get_channel_dispatcher,
    get_channel_service,
    get_channel_type_registry,
    get_current_user_id,
    get_message_bus,
    get_storage,
)
from ..message_bus import MessageBus
from ..storage import ChannelRecord, StorageBase
from ._schema import (
    ChannelActionResponse,
    ChannelChatId,
    ChannelChatIdsResponse,
    ChannelResponse,
    ChannelSessionsResponse,
    CreateChannelRequest,
    StartChannelCredentialBindingRequest,
    UpdateChannelRequest,
)

channel_router = APIRouter(prefix="/channels", tags=["channels"])

BindingIdPath = Annotated[
    str,
    Path(
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Opaque credential binding identifier.",
    ),
]


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
    user_id: str = Depends(get_current_user_id),
) -> ChannelResponse:
    """Create a channel."""
    try:
        record = await service.create(
            user_id=user_id,
            channel_type=body.channel_type,
            name=body.name,
            credentials=body.credentials,
            credential_binding_id=body.credential_binding_id,
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


def _require_binding_provider(
    channel_type: str,
    registry: ChannelTypeRegistry,
) -> ChannelCredentialBindingBase:
    """Return a type's binding provider or raise a client-facing 404."""
    if not registry.has_type(channel_type):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Channel type '{channel_type}' is not registered.",
        )
    provider = registry.get_credential_binding(channel_type)
    if provider is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Channel type '{channel_type}' does not support QR binding.",
        )
    return provider


async def _find_binding_provider_by_id(
    binding_id: str,
    registry: ChannelTypeRegistry,
    store: ChannelCredentialBindingStore,
) -> ChannelCredentialBindingBase | None:
    """Resolve a binding provider from the shared opaque-id record."""
    record = await store.get(binding_id)
    if record is None:
        return None
    provider = registry.get_credential_binding_by_provider_id(
        record.provider_id,
    )
    if provider is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Credential binding provider is not registered. Please retry.",
        )
    return provider


@channel_router.post("/bindings", status_code=status.HTTP_201_CREATED)
async def start_credential_binding(
    body: StartChannelCredentialBindingRequest,
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    message_bus: MessageBus = Depends(get_message_bus),
    user_id: str = Depends(get_current_user_id),
) -> ChannelCredentialBindingSession:
    """Start a platform-specific QR authorization session."""
    provider = _require_binding_provider(body.channel_type, registry)
    try:
        session = await provider.start(
            user_id,
            ChannelCredentialBindingStore(message_bus),
        )
    except ChannelError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return session


@channel_router.get("/bindings/{binding_id}")
async def get_credential_binding_status(
    binding_id: BindingIdPath,
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    message_bus: MessageBus = Depends(get_message_bus),
    user_id: str = Depends(get_current_user_id),
) -> ChannelCredentialBindingStatus:
    """Poll a QR authorization session without exposing credentials."""
    store = ChannelCredentialBindingStore(message_bus)
    provider = await _find_binding_provider_by_id(
        binding_id,
        registry,
        store,
    )
    if provider is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Credential binding session not found.",
        )
    try:
        binding_status = await provider.get_status(
            user_id,
            binding_id,
            store,
        )
    except ChannelError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return binding_status


@channel_router.delete(
    "/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_credential_binding(
    binding_id: BindingIdPath,
    registry: ChannelTypeRegistry = Depends(get_channel_type_registry),
    message_bus: MessageBus = Depends(get_message_bus),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Cancel an unfinished QR authorization session."""
    store = ChannelCredentialBindingStore(message_bus)
    provider = await _find_binding_provider_by_id(
        binding_id,
        registry,
        store,
    )
    if provider is None:
        return
    try:
        await provider.cancel(
            user_id,
            binding_id,
            store,
        )
    except ChannelError as e:
        raise HTTPException(e.status_code, str(e)) from e


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
    dispatcher: ChannelLifecycleDispatcher = Depends(get_channel_dispatcher),
    user_id: str = Depends(get_current_user_id),
) -> ChannelStatus:
    """The channel's live connection status."""
    await _owned(channel_id, user_id, storage)
    return await dispatcher.get_status(channel_id)


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
    dispatcher: ChannelLifecycleDispatcher = Depends(get_channel_dispatcher),
    user_id: str = Depends(get_current_user_id),
) -> ChannelChatIdsResponse:
    """Known chats for routing config: platform list ∪ passively seen."""
    await _owned(channel_id, user_id, storage)
    chats: list[ChannelChatId] = []
    platform_ids: set[str] = set()
    for chat in await dispatcher.list_bot_chats(channel_id):
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
    for cid in await dispatcher.list_seen_chat_ids(channel_id):
        if cid not in platform_ids:
            chats.append(ChannelChatId(chat_id=cid, source="recorded"))
    return ChannelChatIdsResponse(chats=chats)
