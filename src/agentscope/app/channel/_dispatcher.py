# -*- coding: utf-8 -*-
"""ChannelLifecycleDispatcher — reconcile running instances with storage.

One per node. Storage is the source of truth; this dispatcher makes the
node's live channel set match the enabled records, driven by lifecycle
notifications and a periodic sweep (which also self-heals lost
notifications and refreshes the status heartbeat).

It also forwards each channel-bound run's output back to the platform:
on an outbound signal it subscribes to the run's event stream (gap-free
— subscribe **first**, then replay the log, deduplicating by Redis-Stream
``entry_id``) and hands that stream to the local channel's
``send_response``, which folds and delivers it. The channel owns
rendering (streaming, confirmation cards); the dispatcher only feeds it
the events.
"""
import asyncio
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, AsyncIterator

from ..._logging import logger
from ..._utils._common import _generate_id
from ...event import EventType
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import ChannelRecord, StorageBase
from ._base import (
    ChannelBase,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelStatus,
)
from ._gateway import ChannelGateway
from ._registry import ChannelTypeRegistry

# Max seconds to wait for a channel-bound run's reply before giving up.
RESPONSE_TIMEOUT_SECS = 60.0
# TTL (seconds) of a node's per-channel status heartbeat.
LIVENESS_TTL_SECS = 30
# Shared lease held while one worker normalizes a webhook message.
WEBHOOK_DEDUPE_LOCK_TTL_SECS = 300
# One worker drains one webhook job at a time to preserve queue order.
WEBHOOK_DRAIN_LOCK_TTL_SECS = 300
# Keep processed WhatsApp message ids bounded in the shared registry.
WEBHOOK_DEDUPE_TTL_SECS = 7 * 24 * 60 * 60

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
        self._node_id = _generate_id()
        self._tasks: list[asyncio.Task] = []
        self._forward_tasks: set[asyncio.Task] = set()
        self._webhook_tasks: set[asyncio.Task] = set()

    def get_local_channel(self, channel_id: str) -> ChannelBase | None:
        """Return this node's live channel for ``channel_id``, if running —
        how a channel-originated run's agent tools reach it.

        Args:
            channel_id (`str`): The channel to look up.
        """
        inst = self._instances.get(channel_id)
        return inst.channel if inst else None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start reconcile/heartbeat loops; stop all instances on exit."""
        await self.reconcile()
        self._tasks = [
            asyncio.create_task(self._listen(), name="channel-lifecycle"),
            asyncio.create_task(self._periodic(), name="channel-heartbeat"),
            asyncio.create_task(self._outbound(), name="channel-outbound"),
        ]
        if self._types.has_type("whatsapp"):
            webhook_ready = asyncio.Event()
            self._tasks.append(
                asyncio.create_task(
                    self._consume_webhook_signals(webhook_ready),
                    name="channel-webhook-signals",
                ),
            )
            await webhook_ready.wait()
        try:
            yield
        finally:
            for task in (
                *self._tasks,
                *self._forward_tasks,
                *self._webhook_tasks,
            ):
                task.cancel()
            await asyncio.gather(
                *self._tasks,
                *self._forward_tasks,
                *self._webhook_tasks,
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
            channel = self._types.create_channel_from_record(record)
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

    async def _consume_webhook_signals(
        self,
        ready: asyncio.Event,
    ) -> None:
        """Subscribe before draining so startup cannot miss a wake signal."""
        backoff = 1.0
        while True:
            def on_ready() -> None:
                """Expose subscription readiness and drain persisted work."""
                ready.set()
                self._spawn_existing_webhook_drains()

            try:
                async for signal in self._bus.subscribe(
                    MessageBusKeys.channel_webhook_signal(),
                    on_ready=on_ready,
                ):
                    backoff = 1.0
                    for channel_id in signal.get("channel_ids", []):
                        self._spawn_webhook_drain(str(channel_id))
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-except
                ready.set()
                logger.warning("channel webhook subscription lost")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _track_webhook_task(self, task: asyncio.Task) -> None:
        """Track one background webhook task for clean shutdown."""
        self._webhook_tasks.add(task)
        task.add_done_callback(self._webhook_tasks.discard)

    def _spawn_existing_webhook_drains(self) -> None:
        """Drain persisted per-channel queues after each subscription."""
        task = asyncio.create_task(
            self._drain_existing_webhook_queues(),
            name="channel-webhook-existing",
        )
        self._track_webhook_task(task)

    async def _drain_existing_webhook_queues(self) -> None:
        """Schedule drains for all enabled WhatsApp channel records."""
        try:
            records = await self._storage.list_all_channels()
        except Exception:  # pylint: disable=broad-except
            logger.exception("channel webhook startup drain failed")
            return
        for record in records:
            if record.enabled and record.channel_type == "whatsapp":
                self._spawn_webhook_drain(record.id)

    def _spawn_webhook_drain(self, channel_id: str) -> None:
        """Start an ordered drain for one channel without blocking others."""
        if not channel_id:
            return
        task = asyncio.create_task(
            self._drain_webhook_queue(channel_id),
            name=f"channel-webhook-drain:{channel_id}",
        )
        self._track_webhook_task(task)

    async def _drain_webhook_queue(self, channel_id: str) -> None:
        """Drain one channel's webhook messages in queue order.

        A per-channel distributed lock preserves ordering across workers while
        allowing unrelated channels to download media concurrently.
        """
        drain_lock = MessageBusKeys.channel_webhook_drain_lock(channel_id)
        async with self._bus.acquire_lock(
            drain_lock,
            ttl_secs=WEBHOOK_DRAIN_LOCK_TTL_SECS,
        ):
            while True:
                try:
                    jobs = await self._bus.queue_drain(
                        MessageBusKeys.channel_webhook_queue(channel_id),
                    )
                except Exception:  # pylint: disable=broad-except
                    logger.exception("channel webhook drain failed")
                    return
                if not jobs:
                    return
                for _entry_id, job in jobs:
                    await self._process_webhook_job(job)

    async def _process_webhook_job(self, job: dict) -> None:
        """Normalize one shared webhook payload and route its events."""
        channel_id = str(job.get("channel_id", ""))
        message_id = str(job.get("message_id", ""))
        if not channel_id or not message_id:
            return
        record = await self._storage.get_channel(channel_id)
        if record is None or not record.enabled:
            return
        lock_key = MessageBusKeys.channel_webhook_dedupe_lock(
            channel_id,
            message_id,
        )
        if not await self._bus.try_lock(
            lock_key,
            ttl_secs=WEBHOOK_DEDUPE_LOCK_TTL_SECS,
        ):
            return
        try:
            dedupe_namespace = MessageBusKeys.channel_webhook_dedupe(
                channel_id,
            )
            if await self._bus.registry_exists(
                dedupe_namespace,
                message_id,
            ):
                return
            channel = self._types.create_channel_from_record(record)
            normalize = getattr(channel, "normalize_webhook", None)
            if normalize is None:
                logger.error(
                    "Channel %s does not support webhook normalization",
                    record.channel_type,
                )
                return
            events = await normalize(job.get("payload", {}))
            for event in events:
                if not await self._gateway.process_with_result(event):
                    return
            await self._bus.registry_set(
                dedupe_namespace,
                message_id,
                "1",
                ttl_secs=WEBHOOK_DEDUPE_TTL_SECS,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "channel webhook processing failed for %s/%s",
                channel_id,
                message_id,
            )
        finally:
            await self._bus.unlock(lock_key)

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
        """Periodic reconcile (self-heals lost lifecycle events)."""
        interval = max(5.0, LIVENESS_TTL_SECS / 2)
        while True:
            await asyncio.sleep(interval)
            await self.reconcile()

    # -- Read APIs (for the router) --

    async def get_status(self, channel_id: str) -> ChannelStatus:
        """The channel's live connection status, or ``stopped`` if it is not
        running here (disabled / not yet started).

        Args:
            channel_id (`str`): The channel to report on.
        """
        inst = self._instances.get(channel_id)
        return inst.channel.status if inst else ChannelStatus(state="stopped")

    async def list_bot_chats(self, channel_id: str) -> list[dict]:
        """Chats the bot is in, via the local channel if running.

        Args:
            channel_id (`str`): The channel to query.
        """
        inst = self._instances.get(channel_id)
        return await inst.channel.list_bot_chats() if inst else []

    async def list_seen_chat_ids(self, channel_id: str) -> list[str]:
        """Chat_ids passively recorded from inbound messages.

        Args:
            channel_id (`str`): The channel to list seen chats for.
        """
        fields = await self._bus.registry_getall(
            MessageBusKeys.channel_seen_chats(channel_id),
        )
        return sorted(fields.keys())

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
