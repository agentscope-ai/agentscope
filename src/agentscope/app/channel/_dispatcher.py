# -*- coding: utf-8 -*-
"""ChannelLifecycleDispatcher — reconcile running instances with storage.

One per node. Storage is the source of truth; this dispatcher makes the
node's live channel set match the enabled records, driven by lifecycle
notifications and a periodic sweep (which also self-heals lost
notifications and refreshes the status heartbeat).

It answers no queries. With the connections in a dedicated worker the
API replicas have no dispatcher to ask, so status is published to the
bus and read from there instead.

It also forwards each channel-bound run's output back to the platform:
on an outbound signal it subscribes to the run's event stream (gap-free
— subscribe **first**, then replay the log, deduplicating by Redis-Stream
``entry_id``) and hands that stream to the local channel's
``send_response``, which folds and delivers it. The channel owns
rendering (streaming, confirmation cards); the dispatcher only feeds it
the events.
"""
import asyncio
import socket
import time
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator

from ..._logging import logger
from ..._utils._common import _generate_id
from ...event import EventType
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

# Max seconds to wait for a channel-bound run's reply before giving up.
RESPONSE_TIMEOUT_SECS = 60.0
# How often the heartbeat is refreshed; well inside
# ``LIVENESS_TTL_SECS`` so a live node never looks expired to a reader.
LIVENESS_REFRESH_SECS = 10

# Events that end a reply's event stream.
_TERMINAL_EVENTS = frozenset(
    {EventType.REPLY_END, EventType.REQUIRE_USER_CONFIRM},
)


@dataclass
class ChannelInstance:
    """A running channel, its listener task, and the config version it
    was started from (for reconcile)."""

    channel: ChannelBase
    task: asyncio.Task
    version: str


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
        self._forward_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start reconcile/heartbeat loops; stop all instances on exit."""
        await self.reconcile()
        await self._publish_status()
        self._tasks = [
            asyncio.create_task(self._listen(), name="channel-lifecycle"),
            asyncio.create_task(self._periodic(), name="channel-heartbeat"),
            asyncio.create_task(self._outbound(), name="channel-outbound"),
        ]
        try:
            yield
        finally:
            for task in (*self._tasks, *self._forward_tasks):
                task.cancel()
            await asyncio.gather(
                *self._tasks,
                *self._forward_tasks,
                return_exceptions=True,
            )
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

    async def _outbound(self) -> None:
        """Drain channel-output signals and forward each run's reply; the
        per-run lease makes the at-least-once drain effectively once."""
        await self._drain_outbound()
        backoff = 1.0
        while True:
            try:
                async for _ in self._bus.subscribe(
                    MessageBusKeys.channel_outbound_signal(),
                ):
                    backoff = 1.0
                    await self._drain_outbound()
            except asyncio.CancelledError:  # pylint: disable=try-except-raise
                raise
            except Exception:  # pylint: disable=broad-except
                logger.warning("channel outbound subscription lost")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _drain_outbound(self) -> None:
        """Forward every queued output signal this node can serve."""
        try:
            jobs = await self._bus.queue_drain(
                MessageBusKeys.channel_outbound_queue(),
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("channel outbound drain failed")
            return
        for _entry_id, job in jobs:
            inst = self._instances.get(job.get("channel_id", ""))
            if inst is None:
                # Not hosted here (reconcile lag). Under no-sharding every
                # node hosts every enabled channel, so drop this stale one.
                continue
            task = asyncio.create_task(
                self._forward(job, inst.channel),
                name=f"channel-forward:{job.get('session_id', '')}",
            )
            self._forward_tasks.add(task)
            task.add_done_callback(self._forward_tasks.discard)

    # -- Output forwarding (a run's reply → the platform) --

    async def _forward(self, job: dict, channel: ChannelBase) -> None:
        """Feed one channel-bound run's event stream to ``send_response``.

        The channel folds and delivers it (streaming, confirmation cards);
        this only subscribes and hands over the stream.

        Args:
            job (`dict`):
                ``{session_id, channel_id, chat_id, user_id, agent_id}``
                from the outbound signal.
            channel (`ChannelBase`): The local channel for ``channel_id``.
        """
        record = await self._storage.get_channel(job["channel_id"])
        if record is None:
            return
        # Dedup across nodes: the drain is at-least-once, so claim a
        # per-run lease first — only the winner forwards, the rest skip.
        lock_key = MessageBusKeys.channel_forward_lease(job["session_id"])
        if not await self._bus.try_lock(
            lock_key,
            ttl_secs=int(RESPONSE_TIMEOUT_SECS) + 10,
        ):
            return
        # Synthetic send target — background runs have no inbound message.
        # Carry the run's identity so a confirmation card can pin its exact
        # target and skip routing re-resolution on click.
        target = ChannelEvent(
            channel_id=job["channel_id"],
            channel_user_id="",
            chat_id=job["chat_id"],
            metadata={
                "session_id": job["session_id"],
                "agent_id": job["agent_id"],
            },
        )
        try:
            async with aclosing(
                self._event_stream(job["session_id"]),
            ) as events:
                await asyncio.wait_for(
                    channel.send_response(target, events),
                    timeout=RESPONSE_TIMEOUT_SECS,
                )
        except (asyncio.TimeoutError, TimeoutError):
            pass  # leave whatever was delivered
        except Exception:  # pylint: disable=broad-except
            logger.exception("channel send_response failed")
        finally:
            await self._bus.unlock(lock_key)

    async def _event_stream(
        self,
        session_id: str,
    ) -> AsyncGenerator[dict, None]:
        """Yield a run's events gap-free, stopping after the terminal one.

        Subscribe **first** (buffering live events), replay the log, then
        go live — deduplicating by ``entry_id`` so the seam is neither
        missed nor double-counted.

        Args:
            session_id (`str`): The run's session, whose events are read.

        Yields:
            `dict`: Each session event, up to and including the terminal
            ``REPLY_END`` / ``REQUIRE_USER_CONFIRM``.
        """
        event_key = MessageBusKeys.session_events(session_id)
        ready = asyncio.Event()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        seen: set[str] = set()

        async def feeder() -> None:
            """Buffer live subscription events into the local queue."""
            try:
                async for evt in self._bus.subscribe(
                    event_key,
                    on_ready=ready.set,
                ):
                    await queue.put(evt)
            except asyncio.CancelledError:
                pass

        feeder_task = asyncio.create_task(feeder())
        try:
            await asyncio.wait_for(ready.wait(), timeout=5.0)
            for entry_id, evt in await self._bus.log_read(
                event_key,
                max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
            ):
                seen.add(str(entry_id))
                yield evt
                if evt.get("type", "") in _TERMINAL_EVENTS:
                    return
            while True:
                evt = await queue.get()
                eid = evt.get("_entry_id")
                if eid is not None:
                    if str(eid) in seen:
                        continue
                    seen.add(str(eid))
                yield evt
                if evt.get("type", "") in _TERMINAL_EVENTS:
                    return
        finally:
            feeder_task.cancel()
            try:
                await feeder_task
            except (
                asyncio.CancelledError,
                Exception,
            ):  # pylint: disable=broad-except
                pass

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
