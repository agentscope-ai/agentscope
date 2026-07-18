# -*- coding: utf-8 -*-
"""ChannelManager — lifecycle and dynamic management of channel instances.

Owns the ChannelGateway, handles start/stop/add/remove/update of
channels. Integrates into the FastAPI lifespan via ``lifespan()``
async context manager.

In a multi-node cluster, lifecycle events (channel added/removed/enabled/
disabled) are broadcast via MessageBus so all nodes can sync their local
channel instances.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..._logging import logger
from ._base import ChannelBase
from ._config import ChannelConfig
from ._gateway import ChannelGateway
from ._errors import ChannelNotFoundError, DuplicateBotError
from ._registry import ChannelTypeRegistry
from ._session_mapper import SessionMapperBase
from ..message_bus import MessageBus
from ..storage import StorageBase
from ..storage import ChannelRecord
from .._service import ChatService
from .._manager import ChatRunRegistry

_LIFECYCLE_CHANNEL = "agentscope:channel:lifecycle"


def _gen_node_id() -> str:
    """Generate a unique node identifier for this process."""
    import uuid

    return uuid.uuid4().hex[:12]


class ChannelManager:
    """Lifecycle and dynamic management of channel instances (control plane).

    Handles CRUD operations for channels, start/stop of platform adapter
    listener tasks, and multi-node lifecycle synchronization via MessageBus.

    See ``ChannelGateway`` for the event-processing/data plane counterpart.
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        chat_service: ChatService,
        chat_run_registry: ChatRunRegistry,
        session_mapper: SessionMapperBase,
        config: ChannelConfig,
        type_registry: ChannelTypeRegistry | None = None,
    ) -> None:
        """Initialise the manager with its dependencies.

        Args:
            storage: App-level persistence for sessions, agents, channels.
            message_bus: Distributed pub/sub for lifecycle event broadcast
                across cluster nodes and per-user locking.
            chat_service: Service for triggering agent chat runs (passed
                through to ``ChannelGateway``).
            chat_run_registry: Registry for spawning async chat run tasks
                (passed through to ``ChannelGateway``).
            session_mapper: Maps channel peer/chat to AgentScope sessions
                (passed through to ``ChannelGateway``).
            config: Module-level configuration (timeouts, concurrency
                limits, default session config).
            type_registry: Registry mapping channel type strings to
                platform adapter factories. Defaults to a built-in
                registry with Feishu/Discord/DingTalk/WeCom.
        """
        self._storage = storage
        self._session_mapper = session_mapper
        self._message_bus = message_bus
        self._config = config
        self._type_registry = type_registry or ChannelTypeRegistry()
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._lifecycle_task: asyncio.Task | None = None
        self._node_id = _gen_node_id()

        self._gateway = ChannelGateway(
            storage=storage,
            message_bus=message_bus,
            chat_service=chat_service,
            chat_run_registry=chat_run_registry,
            mapper=session_mapper,
            session_config=config.default_session_config,
            response_timeout=config.response_timeout,
            concurrent_users_limit=config.concurrent_users_limit,
        )

    @property
    def gateway(self) -> ChannelGateway:
        """Expose the gateway for direct event dispatch (testing)."""
        return self._gateway

    async def get_channel_record(
        self,
        channel_id: str,
    ) -> ChannelRecord | None:
        """Fetch a channel record by id."""
        return await self._storage.get_channel_by_id(channel_id)

    async def list_channel_records(
        self,
        enabled_only: bool = False,
    ) -> list[ChannelRecord]:
        """List all channel records, optionally filtering enabled only."""
        records = await self._storage.list_all_channels()
        if enabled_only:
            return [r for r in records if r.enabled]
        return records

    @property
    def session_mapper(self) -> SessionMapperBase:
        """Public access to session mapper for read operations."""
        return self._session_mapper

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Async context manager for application lifespan integration."""
        await self._load_and_start_channels()
        self._lifecycle_task = asyncio.create_task(
            self._lifecycle_listener(),
            name="channel-lifecycle-listener",
        )
        try:
            yield
        finally:
            if self._lifecycle_task:
                self._lifecycle_task.cancel()
                try:
                    await self._lifecycle_task
                except (asyncio.CancelledError, Exception):
                    pass
            for task in self._channel_tasks.values():
                task.cancel()
            await asyncio.gather(
                *self._channel_tasks.values(),
                return_exceptions=True,
            )
            for channel in self._gateway.iter_channels():
                try:
                    await channel.on_stop()
                except Exception:
                    logger.exception(
                        "Error during channel shutdown: %s",
                        channel.channel_id,
                    )
            self._channel_tasks.clear()

    # ── Lifecycle broadcast (multi-node sync) ──

    async def _broadcast_lifecycle(
        self,
        action: str,
        channel_id: str,
    ) -> None:
        """Publish a lifecycle event so other nodes can react."""
        try:
            await self._message_bus.publish(
                _LIFECYCLE_CHANNEL,
                {
                    "action": action,
                    "channel_id": channel_id,
                    "origin_node": self._node_id,
                },
            )
        except Exception:
            logger.debug(
                "Failed to broadcast lifecycle event: %s %s",
                action,
                channel_id,
            )

    async def _lifecycle_listener(self) -> None:
        """Subscribe to lifecycle events from other nodes.

        Automatically reconnects on subscription failure with backoff.
        """
        backoff = 1.0
        while True:
            try:
                async for payload in self._message_bus.subscribe(
                    _LIFECYCLE_CHANNEL,
                ):
                    backoff = 1.0  # Reset on successful message
                    await self._handle_lifecycle_event(payload)
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception:
                logger.warning(
                    "Lifecycle subscription lost, reconnecting in %.1fs",
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_lifecycle_event(self, payload: dict) -> None:
        """Process a single lifecycle event from another node."""
        action = payload.get("action")
        channel_id = payload.get("channel_id")
        origin_node = payload.get("origin_node")
        if not action or not channel_id:
            return
        if origin_node == self._node_id:
            return

        if action in ("added", "enabled"):
            if channel_id not in self._channel_tasks:
                record = await self._storage.get_channel_by_id(
                    channel_id,
                )
                if record and record.enabled:
                    try:
                        await self._start_channel(record)
                    except Exception:
                        logger.exception(
                            "Failed to start channel %s from lifecycle event",
                            channel_id,
                        )
        elif action in ("removed", "disabled"):
            if channel_id in self._channel_tasks:
                await self._stop_channel(channel_id)
        elif action == "updated":
            if channel_id in self._channel_tasks:
                await self._stop_channel(channel_id)
            record = await self._storage.get_channel_by_id(channel_id)
            if record and record.enabled:
                try:
                    await self._start_channel(record)
                except Exception:
                    logger.exception(
                        "Failed to restart channel %s from lifecycle event",
                        channel_id,
                    )

    # ── Public CRUD ──

    async def add_channel(self, record: ChannelRecord) -> None:
        """Add and optionally start a new channel.

        Raises DuplicateBotError if the platform_bot_id is already
        registered to another channel.
        """
        existing = await self._storage.get_channel_by_platform_bot_id(
            record.platform_bot_id,
        )
        if existing:
            raise DuplicateBotError(
                record.platform_bot_id,
                existing.channel_id,
            )
        user_id = record.tenant_user_id or "system"
        await self._storage.upsert_channel(user_id, record)
        if record.enabled:
            await self._start_channel(record)
        await self._broadcast_lifecycle("added", record.channel_id)

    async def remove_channel(self, channel_id: str) -> None:
        """Stop, clean up mappings, and delete a channel."""
        await self._stop_channel(channel_id)
        await self._session_mapper.delete_all_for_channel(channel_id)
        record = await self._storage.get_channel_by_id(channel_id)
        if record:
            user_id = record.tenant_user_id or "system"
            await self._storage.delete_channel(user_id, channel_id)
        await self._broadcast_lifecycle("removed", channel_id)

    async def update_channel(
        self,
        channel_id: str,
        updates: dict,
    ) -> ChannelRecord:
        """Stop the old instance, apply updates, and restart if enabled."""
        await self._stop_channel(channel_id)
        existing = await self._storage.get_channel_by_id(channel_id)
        if existing is None:
            raise ChannelNotFoundError(channel_id)
        record = existing.model_copy(update=updates)
        user_id = record.tenant_user_id or "system"
        await self._storage.upsert_channel(user_id, record)
        if record.enabled:
            await self._start_channel(record)
        await self._broadcast_lifecycle("updated", channel_id)
        return record

    async def enable_channel(self, channel_id: str) -> None:
        """Enable and start a disabled channel."""
        existing = await self._storage.get_channel_by_id(channel_id)
        if existing is None:
            raise ChannelNotFoundError(channel_id)
        record = existing.model_copy(update={"enabled": True})
        user_id = record.tenant_user_id or "system"
        await self._storage.upsert_channel(user_id, record)
        await self._start_channel(record)
        await self._broadcast_lifecycle("enabled", channel_id)

    async def disable_channel(self, channel_id: str) -> None:
        """Disable and stop a running channel."""
        await self._stop_channel(channel_id)
        existing = await self._storage.get_channel_by_id(channel_id)
        if existing is None:
            raise ChannelNotFoundError(channel_id)
        record = existing.model_copy(update={"enabled": False})
        user_id = record.tenant_user_id or "system"
        await self._storage.upsert_channel(user_id, record)
        await self._broadcast_lifecycle("disabled", channel_id)

    async def get_channel_status(self, channel_id: str) -> dict:
        """Return current runtime status of a channel."""
        task = self._channel_tasks.get(channel_id)
        if task is None:
            return {"status": "stopped"}
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            return {
                "status": "error" if exc else "stopped",
                "error": str(exc) if exc else None,
            }
        return {"status": "running"}

    # ── Internal lifecycle ──

    async def _load_and_start_channels(self) -> None:
        """Load all enabled channels from storage on startup."""
        records = await self.list_channel_records(enabled_only=True)
        for record in records:
            try:
                await self._start_channel(record)
            except Exception:
                logger.exception(
                    "Failed to start channel %s",
                    record.channel_id,
                )

    async def _start_channel(self, record: ChannelRecord) -> None:
        """Instantiate, initialise, and launch a channel listener."""
        channel = self._create_channel_instance(record)
        await channel.on_start()
        try:
            channel.set_gateway(self._gateway)
            self._gateway.register_channel(channel)
            task = asyncio.create_task(
                channel.start_listening(),
                name=f"channel-listener:{record.channel_id}",
            )
            self._channel_tasks[record.channel_id] = task
        except Exception:
            await channel.on_stop()
            raise
        logger.info(
            "Channel '%s' (%s) started.",
            record.channel_id,
            record.channel_type,
        )

    async def _stop_channel(self, channel_id: str) -> None:
        """Cancel the listener task and unregister the channel."""
        task = self._channel_tasks.pop(channel_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        channel = self._gateway.get_channel(channel_id)
        if channel:
            try:
                await channel.on_stop()
            except Exception:
                logger.exception(
                    "Error stopping channel %s",
                    channel_id,
                )
        self._gateway.unregister_channel(channel_id)
        logger.info("Channel '%s' stopped.", channel_id)

    def _create_channel_instance(self, record: ChannelRecord) -> ChannelBase:
        """Instantiate the appropriate channel class from record type.

        Delegates to the ChannelTypeRegistry factory if available.
        """
        return self._type_registry.create_channel(
            channel_type=record.channel_type,
            channel_id=record.channel_id,
            credentials=record.credentials,
            config=record.config,
        )
