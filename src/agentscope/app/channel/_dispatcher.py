# -*- coding: utf-8 -*-
"""ChannelLifecycleDispatcher — reconcile running instances with storage.

One per node. Storage is the source of truth; this dispatcher makes the
node's live channel set match the enabled records, driven by lifecycle
notifications and a periodic sweep (which also self-heals lost
notifications and refreshes the status heartbeat).

It answers no queries. With the connections in a dedicated worker the
API replicas have no dispatcher to ask, so status is published to the
bus and read from there instead.

Holding the connections is all it does. A reply never comes back here:
delivery is plain REST, so the node running the agent builds its own
client and sends it directly.
"""
import asyncio
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator

from ..._logging import logger
from ..._utils._common import _generate_id
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import ChannelRecord, StorageBase
from ._base import (
    LIVENESS_TTL_SECS,
    ChannelBase,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelHeartbeat,
)
from ._gateway import ChannelGateway
from ._registry import ChannelTypeRegistry

# How often the heartbeat is refreshed; well inside
# ``LIVENESS_TTL_SECS`` so a live node never looks expired to a reader.
LIVENESS_REFRESH_SECS = 10


@dataclass
class ChannelInstance:
    """A running channel, its listener task, and the config version it
    was started from (for reconcile)."""

    channel: ChannelBase
    task: asyncio.Task
    version: datetime


class ChannelLifecycleDispatcher:
    """Reconciles this node's channel instances against storage."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        type_registry: ChannelTypeRegistry,
        gateway: ChannelGateway,
    ) -> None:
        """Bind dependencies and start with an empty instance table.

        Args:
            storage (`StorageBase`): Source of truth for channel records.
            message_bus (`MessageBus`): Lifecycle / outbound signalling.
            type_registry (`ChannelTypeRegistry`): Builds instances.
            gateway (`ChannelGateway`): Inbound event orchestrator bound
                into each started channel.
        """
        self._storage = storage
        self._bus = message_bus
        self._types = type_registry
        self._gateway = gateway
        self._instances: dict[str, ChannelInstance] = {}
        self._node_id = f"{socket.gethostname()}:{_generate_id()[:8]}"
        self._tasks: list[asyncio.Task] = []

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start reconcile/heartbeat loops; stop all instances on exit."""
        await self.reconcile()
        await self._publish_status()
        self._tasks = [
            asyncio.create_task(self._listen(), name="channel-lifecycle"),
            asyncio.create_task(self._periodic(), name="channel-heartbeat"),
        ]
        try:
            yield
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            for cid in set(self._instances):
                await self._stop(cid)

    # -- Reconcile --

    async def reconcile(self) -> None:
        """Drive the local instance set to match enabled records."""
        try:
            records = await self._storage.list_all_channels()
        except Exception:  # pylint: disable=broad-except
            logger.exception("channel reconcile: failed to list channels")
            return
        desired = {r.id: r for r in records if r.enabled}

        for cid in set(self._instances) - set(desired):
            await self._stop(cid)

        for cid, record in desired.items():
            inst = self._instances.get(cid)
            # A channel that gave up (state 'failed') parks its task alive, so
            # ``task.done()`` stays False and it is not restarted here; editing
            # the channel bumps ``updated_at`` and re-triggers a fresh start.
            if (
                inst is None
                or inst.version != record.updated_at
                or inst.task.done()
            ):
                if inst is not None:
                    await self._stop(cid)
                await self._start(record)

    async def _start(self, record: ChannelRecord) -> None:
        """Build, start, and register one channel from its record.

        Args:
            record (`ChannelRecord`): The enabled channel to start.
        """
        try:
            channel = self._types.create_channel(
                channel_type=record.channel_type,
                channel_id=record.id,
                credentials=record.credentials,
                config=record.platform_config,
            )
            task = asyncio.create_task(
                channel.start_listening(self._gateway.process),
                name=f"channel-listener:{record.id}",
            )
            self._instances[record.id] = ChannelInstance(
                channel,
                task,
                record.updated_at,
            )
            logger.info(
                "channel '%s' (%s) started",
                record.id,
                record.channel_type,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("channel '%s' failed to start", record.id)

    async def _stop(self, channel_id: str) -> None:
        """Cancel a channel's listener; its ``start_listening`` ``finally``
        releases resources.

        Args:
            channel_id (`str`): The channel to stop; a no-op if not here.
        """
        inst = self._instances.pop(channel_id, None)
        if inst is None:
            return
        inst.task.cancel()
        try:
            await inst.task
        except (
            asyncio.CancelledError,
            Exception,
        ):  # pylint: disable=broad-except
            pass
        # Withdraw this node's report rather than leaving it to age out.
        try:
            await self._bus.registry_del(
                MessageBusKeys.channel_liveness(channel_id),
                self._node_id,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("channel '%s' status withdrawal failed", channel_id)
        logger.info("channel '%s' stopped", channel_id)

    # -- Loops --

    async def _listen(self) -> None:
        """Reconcile on each lifecycle notification (reconnect on drop)."""
        backoff = 1.0
        while True:
            try:
                async for _ in self._bus.subscribe(
                    MessageBusKeys.channel_lifecycle(),
                ):
                    backoff = 1.0
                    await self.reconcile()
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception:  # pylint: disable=broad-except
                logger.warning("channel lifecycle subscription lost")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _periodic(self) -> None:
        """Reconcile and publish status on a fixed interval.

        The reconcile self-heals lost lifecycle events; the heartbeat is
        what makes status readable from processes that hold no
        connection — with a dedicated channel worker that is every API
        replica.
        """
        while True:
            await asyncio.sleep(LIVENESS_REFRESH_SECS)
            await self.reconcile()
            await self._publish_status()

    async def _publish_status(self) -> None:
        """Write this node's view of each local channel's status.

        Each report carries the time it was written: the namespace TTL
        expires the whole hash, not this node's field, so a reader
        cannot tell a live entry from one a restarted predecessor left
        behind without the stamp.
        """
        now = time.time()
        for channel_id, inst in list(self._instances.items()):
            try:
                await self._bus.registry_set(
                    MessageBusKeys.channel_liveness(channel_id),
                    self._node_id,
                    ChannelHeartbeat(
                        status=inst.channel.status,
                        reported_at=now,
                    ).model_dump_json(),
                    ttl_secs=LIVENESS_TTL_SECS,
                )
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "channel '%s' status heartbeat failed",
                    channel_id,
                )

    async def dispatch(
        self,
        event: ChannelEvent | ChannelConfirmationResultEvent,
        channel_id: str,
    ) -> None:
        """Route an event through the gateway (used by tests).

        Args:
            event (`ChannelEvent | ChannelConfirmationResultEvent`): The
                event to route.
            channel_id (`str`): The channel whose gateway handles it.
        """
        inst = self._instances.get(channel_id)
        if inst:
            await self._gateway.process(event)
