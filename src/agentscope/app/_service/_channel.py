# -*- coding: utf-8 -*-
"""ChannelService — stateless CRUD for channel records.

Validates, writes the record, and publishes a lifecycle notification so
every node's :class:`ChannelLifecycleDispatcher` reconciles its running
instances against storage. Holds no channel instances.
"""

from datetime import datetime

from ..._logging import logger
from ..._utils._common import _generate_id
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import (
    ChannelRecord,
    RoutingConfig,
    SessionSettings,
    StorageBase,
)
from ..channel._errors import ChannelError
from ..channel._credential_binding import ChannelCredentialBindingStore
from ..channel._registry import ChannelTypeRegistry


class ChannelService:
    """CRUD operations on channel records."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        type_registry: ChannelTypeRegistry,
    ) -> None:
        """Bind storage, the message bus, and the channel type registry.

        Args:
            storage (`StorageBase`): Application storage.
            message_bus (`MessageBus`): For lifecycle notifications.
            type_registry (`ChannelTypeRegistry`): Validates types and
                builds instances for the pre-create connection check.
        """
        self._storage = storage
        self._bus = message_bus
        self._types = type_registry
        self._binding_store = ChannelCredentialBindingStore(message_bus)

    async def create(
        self,
        *,
        user_id: str,
        channel_type: str,
        credentials: dict | None,
        credential_binding_id: str | None = None,
        platform_config: dict,
        routing: RoutingConfig,
        session: SessionSettings,
        enabled: bool = True,
        name: str | None = None,
    ) -> ChannelRecord:
        """Create a channel, rejecting a bot already bound elsewhere.

        Args:
            user_id (`str`): Owner of the channel.
            channel_type (`str`): Registered platform type id.
            name (`str | None`): Optional display name.
            credentials (`dict | None`): Manual platform credentials.
            credential_binding_id (`str | None`): Authorized QR session.
            platform_config (`dict`): Platform behaviour options.
            routing (`RoutingConfig`): Inbound routing rules.
            session (`SessionSettings`): Derived-session settings.
            enabled (`bool`): Whether to start the channel immediately.
        """
        if (credentials is None) == (credential_binding_id is None):
            raise ChannelError(
                "Provide exactly one of 'credentials' or "
                "'credential_binding_id'.",
                400,
            )

        binding = None
        if credential_binding_id is not None:
            binding = self._types.get_credential_binding(channel_type)
            if binding is None:
                raise ChannelError(
                    f"Channel type '{channel_type}' does not support QR "
                    "credential binding.",
                    400,
                )
            consume_lock = (
                MessageBusKeys.channel_credential_binding_consume_lock(
                    credential_binding_id,
                )
            )
            async with self._bus.acquire_lock(consume_lock, ttl_secs=600):
                resolved_credentials = await binding.resolve_credentials(
                    user_id,
                    credential_binding_id,
                    self._binding_store,
                )
                record = await self._persist_new(
                    user_id=user_id,
                    channel_type=channel_type,
                    credentials=resolved_credentials,
                    platform_config=platform_config,
                    routing=routing,
                    session=session,
                    enabled=enabled,
                    name=name,
                )
                try:
                    await binding.complete(
                        user_id,
                        credential_binding_id,
                        self._binding_store,
                    )
                except Exception:  # pylint: disable=broad-except
                    # The record is already durable. A cleanup failure must
                    # not turn a successful create into a misleading 500.
                    # A retry is serialized here and rejected by the bot
                    # uniqueness lock before it can create a duplicate.
                    logger.warning(
                        "Failed to complete channel credential binding '%s'.",
                        credential_binding_id,
                        exc_info=True,
                    )
        else:
            assert credentials is not None
            record = await self._persist_new(
                user_id=user_id,
                channel_type=channel_type,
                credentials=credentials,
                platform_config=platform_config,
                routing=routing,
                session=session,
                enabled=enabled,
                name=name,
            )
        await self._notify(record.id)
        return record

    async def update(self, channel_id: str, updates: dict) -> ChannelRecord:
        """Apply routing/session/platform_config/enabled changes;
        credentials and channel_type are immutable (recreate to change).

        Args:
            channel_id (`str`): The channel to update.
            updates (`dict`): Field changes to apply.
        """
        record = await self._require(channel_id)
        updates.pop("credentials", None)
        updates.pop("channel_type", None)
        bot_id = self._types.extract_platform_bot_id(
            record.channel_type,
            record.credentials,
        )
        bot_lock = MessageBusKeys.channel_bot_lock(bot_id)
        async with self._bus.acquire_lock(bot_lock, ttl_secs=600):
            # Re-read after acquiring the lock so a concurrent delete cannot
            # be undone by an update that was waiting on a stale record.
            record = await self._require(channel_id)
            updated = record.model_copy(
                update={
                    **updates,
                    "updated_at": datetime.now().isoformat(),
                },
            )
            await self._storage.upsert_channel(updated, bot_id)
        await self._notify(channel_id)
        return updated

    async def set_enabled(
        self,
        channel_id: str,
        enabled: bool,
    ) -> ChannelRecord:
        """Enable or disable a channel.

        Args:
            channel_id (`str`): The channel to toggle.
            enabled (`bool`): The new enabled state.
        """
        return await self.update(channel_id, {"enabled": enabled})

    async def delete(self, channel_id: str) -> None:
        """Delete a channel and clear its bot-id index.

        Args:
            channel_id (`str`): The channel to delete.
        """
        record = await self._require(channel_id)
        bot_id = self._types.extract_platform_bot_id(
            record.channel_type,
            record.credentials,
        )
        bot_lock = MessageBusKeys.channel_bot_lock(bot_id)
        async with self._bus.acquire_lock(bot_lock, ttl_secs=600):
            # Confirm the record still exists after waiting for the lock.
            await self._require(channel_id)
            await self._storage.delete_channel(channel_id, bot_id)
        await self._notify(channel_id)

    # -- internals --

    async def _persist_new(
        self,
        *,
        user_id: str,
        channel_type: str,
        credentials: dict,
        platform_config: dict,
        routing: RoutingConfig,
        session: SessionSettings,
        enabled: bool,
        name: str | None,
    ) -> ChannelRecord:
        """Validate and persist a new channel under its bot lock."""
        validated = self._types.validate_credentials(
            channel_type,
            credentials,
        )
        bot_id = self._types.extract_platform_bot_id(
            channel_type,
            validated,
        )
        bot_lock = MessageBusKeys.channel_bot_lock(bot_id)
        async with self._bus.acquire_lock(bot_lock, ttl_secs=600):
            existing = await self._storage.get_channel_id_by_platform_bot_id(
                bot_id,
            )
            if existing:
                raise ChannelError(
                    f"Bot '{bot_id}' already registered as channel "
                    f"'{existing}'.",
                    409,
                )

            channel_id = _generate_id()
            now = datetime.now().isoformat()
            record = ChannelRecord(
                id=channel_id,
                channel_type=channel_type,
                name=name,
                user_id=user_id,
                enabled=enabled,
                credentials=validated,
                platform_config=platform_config,
                routing=routing,
                session=session,
                created_at=now,
                updated_at=now,
            )
            await self._storage.upsert_channel(record, bot_id)
        return record

    async def _require(self, channel_id: str) -> ChannelRecord:
        """Load a channel record or raise a 404 ``ChannelError``.

        Args:
            channel_id (`str`): The channel to load.

        Returns:
            `ChannelRecord`: The record.
        """
        record = await self._storage.get_channel(channel_id)
        if record is None:
            raise ChannelError(f"Channel '{channel_id}' not found.", 404)
        return record

    async def _notify(self, channel_id: str) -> None:
        """Publish a lifecycle notification (best-effort).

        Args:
            channel_id (`str`): The changed channel; reconcile re-reads
                storage, so the payload is only a nudge.
        """
        try:
            await self._bus.publish(
                MessageBusKeys.channel_lifecycle(),
                {"channel_id": channel_id},
            )
        except Exception:  # pylint: disable=broad-except
            # Lost notifications are recovered by the periodic reconcile.
            pass
