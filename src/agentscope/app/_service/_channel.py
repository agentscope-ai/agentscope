# -*- coding: utf-8 -*-
"""ChannelService — stateless CRUD for channel records.

Validates, writes the record, and publishes a lifecycle notification so
every node's :class:`ChannelLifecycleDispatcher` reconciles its running
instances against storage. Holds no channel instances.
"""
import time
from datetime import datetime

from ..._utils._common import _generate_id
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import (
    ChannelRecord,
    RoutingConfig,
    SessionSettings,
    StorageBase,
)
from ..channel._base import ChannelHeartbeat, ChannelStatus
from ..channel._errors import ChannelError
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

    async def create(
        self,
        *,
        user_id: str,
        channel_type: str,
        credentials: dict,
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
            credentials (`dict`): Platform credentials.
            platform_config (`dict`): Platform behaviour options.
            routing (`RoutingConfig`): Inbound routing rules.
            session (`SessionSettings`): Derived-session settings.
            enabled (`bool`): Whether to start the channel immediately.
        """
        bot_id = self._types.extract_platform_bot_id(
            channel_type,
            credentials,
        )
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
        now = datetime.now()
        record = ChannelRecord(
            id=channel_id,
            channel_type=channel_type,
            name=name,
            user_id=user_id,
            enabled=enabled,
            credentials=credentials,
            platform_config=platform_config,
            routing=routing,
            session=session,
            created_at=now,
            updated_at=now,
        )
        await self._storage.upsert_channel(record, bot_id)
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
        updated = record.model_copy(
            update={**updates, "updated_at": datetime.now()},
        )
        bot_id = self._types.extract_platform_bot_id(
            updated.channel_type,
            updated.credentials,
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
        await self._storage.delete_channel(channel_id, bot_id)
        await self._notify(channel_id)

    # -- Read-side (cluster-wide, no local instances needed) --

    async def get_status(self, channel_id: str) -> ChannelStatus:
        """The channel's connection status, from whichever node holds it.

        Nodes that run a channel heartbeat their view into a registry,
        so this answers correctly from an API replica that holds no
        connection. Reports older than the heartbeat TTL are ignored —
        the namespace expires as a whole, so a node that stopped
        reporting can leave its last entry behind indefinitely while a
        successor keeps the namespace alive.

        Args:
            channel_id (`str`): The channel to report on.
        """
        try:
            entries = await self._bus.registry_getall(
                MessageBusKeys.channel_liveness(channel_id),
            )
        except Exception:  # pylint: disable=broad-except
            return ChannelStatus(state="stopped")

        now = time.time()
        best: ChannelStatus | None = None
        for raw in entries.values():
            beat = ChannelHeartbeat.model_validate_json(raw)
            if not beat.is_fresh(now):
                continue
            if beat.status.state == "connected":
                return beat.status
            if best is None or best.state == "stopped":
                best = beat.status
        return best or ChannelStatus(state="stopped")

    async def list_seen_chat_ids(self, channel_id: str) -> list[str]:
        """Chat_ids passively recorded from inbound messages.

        Args:
            channel_id (`str`): The channel to list seen chats for.
        """
        fields = await self._bus.registry_getall(
            MessageBusKeys.channel_seen_chats(channel_id),
        )
        return sorted(fields.keys())

    # -- internals --

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
