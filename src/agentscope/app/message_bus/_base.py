# -*- coding: utf-8 -*-
"""The message bus abstract base class.

The message bus is the *live* transport layer used to coordinate work
across sessions and processes. It is intentionally separate from
:class:`StorageBase`, which owns *persistent* records: storage may live
on a relational database while the bus stays on a push-capable backend
(Redis, NATS, …) where waking idle consumers and fanning out events is
cheap.

The interface is grouped by **consumption semantics** — i.e. how a
payload's lifetime ends — rather than by business use case. Callers map
their own concepts (agent inbox, session SSE replay, idle wake-up, …)
onto the right mode plus a key naming convention they own.

Three orthogonal modes are exposed:

============================  ===========================================
Mode A — drain queue          Mode C — replay log
``queue_push`` /              ``log_append`` /
``queue_drain``               ``log_read`` / ``log_trim``

Single-consumer, ack-on-read. Multi-consumer, externally bounded.
Each entry returned at most       Each reader tracks its own cursor;
once; storage drops it the        entries persist until trimmed,
moment it is read. TTL bounds     ``max_len`` truncates from the head,
orphaned data when the consumer   or TTL expires the whole key.
disappears.
============================  ===========================================

Mode D — transient broadcast: ``publish`` / ``subscribe``. Fire-and-forget
pub/sub; only currently-subscribed listeners receive a payload, no
history. Use for wake-up signals where missed-while-offline is fine.

Counted broadcast (one entry consumed by N distinct readers) is
intentionally not exposed as a primitive: in practice it requires
consumer-group coordination (Redis Streams' XREADGROUP/XACK semantics)
and adds substantial state. Producers wanting "fan out to N members"
should fan out at write time — push one entry per recipient inbox using
Mode A. The bus stays simple; deduplication is the producer's
responsibility.
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Callable, Self


class MessageBus(ABC):  # pylint: disable=too-many-public-methods
    """Abstract base class for live message transport.

    Implementations expose three consumption modes (drain queue, replay
    log, transient broadcast) over arbitrary string keys and JSON-style
    dict payloads. Callers own key naming and payload schemas.
    """

    async def __aenter__(self) -> Self:
        """Open underlying transport resources (connection pools, …).

        Returns:
            `Self`:
                The bus instance, for use as an async context manager.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Release transport resources on context exit.

        Args:
            exc_type (`type[BaseException] | None`):
                The exception class raised inside the context, if any.
            exc_value (`BaseException | None`):
                The exception instance raised inside the context, if any.
            traceback (`Any`):
                The traceback associated with the exception, if any.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Release underlying transport resources. Default is a no-op."""

    # ------------------------------------------------------------------
    # Mode A — drain queue (single consumer, ack-on-read)
    # ------------------------------------------------------------------

    @abstractmethod
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Append ``payload`` to the drain queue at ``key``.

        Drain queues are single-consumer, ack-on-read: a subsequent
        :meth:`queue_drain` returns each entry exactly once and then
        deletes it. ``ttl_secs`` bounds the queue's lifetime so a key
        whose consumer disappears does not accumulate entries forever.

        Args:
            key (`str`):
                Queue identifier. Caller-defined naming convention; the
                bus treats it as opaque.
            payload (`dict`):
                JSON-serializable dict to enqueue. Schema is the
                caller's responsibility.
            ttl_secs (`int | None`, optional):
                If set, refresh the queue key's expiry to this many
                seconds on every push (sliding TTL). When ``None``, the
                key never expires and must be drained or deleted
                explicitly.

        Returns:
            `str`:
                Transport-level entry id (e.g. the Redis Stream entry
                id). Useful for tracing; not required for normal
                consumption.
        """

    @abstractmethod
    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain up to ``max_count`` entries from the queue at ``key``.

        Returned entries are removed from the queue in the same
        operation, so a subsequent call returns only entries that
        arrived after this one. Safe under the single-consumer-per-key
        invariant.

        Args:
            key (`str`):
                Queue identifier.
            max_count (`int`, defaults to ``100``):
                Maximum number of entries to return in one call.
                Older entries are returned first; remaining entries
                stay in the queue for the next call.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in arrival order. Empty
                list when the queue is empty.
        """

    @abstractmethod
    async def queue_delete(self, key: str) -> None:
        """Delete the drain queue at ``key`` and all of its entries.

        Idempotent: a no-op when the key does not exist. Used by
        :meth:`session_purge` to drop a session's inbox during
        cascade-delete.

        Args:
            key (`str`):
                Queue identifier.
        """

    # ------------------------------------------------------------------
    # Mode C — replay log (multi-consumer, externally bounded)
    # ------------------------------------------------------------------

    @abstractmethod
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        """Append ``payload`` to the replay log at ``key``.

        Replay logs are append-only; readers track their own cursor and
        may join at any time. Lifetime is bounded externally: by
        ``ttl_secs`` (whole key expires), by ``max_len`` (oldest
        entries trimmed once the log exceeds the cap), or by explicit
        :meth:`log_trim`.

        Args:
            key (`str`):
                Log identifier.
            payload (`dict`):
                JSON-serializable dict to append.
            ttl_secs (`int | None`, optional):
                If set, refresh the key's expiry to this many seconds
                on every append (sliding TTL). ``None`` means no TTL.
            max_len (`int | None`, optional):
                If set, cap the log at approximately this many entries
                — older entries are trimmed when the cap is exceeded.
                The cap is approximate so the operation can use the
                backend's efficient near-trim mode (e.g. ``XADD MAXLEN
                ~N``). ``None`` means no cap.

        Returns:
            `str`:
                Transport-level entry id, useful as a cursor for
                subsequent :meth:`log_read` calls.
        """

    @abstractmethod
    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Read up to ``max_count`` entries from the replay log at
        ``key``, starting after ``since``.

        Reads are non-destructive: the same entries can be returned to
        any number of readers, or to the same reader on retry. Each
        reader is responsible for tracking its own cursor.

        Args:
            key (`str`):
                Log identifier.
            since (`str | None`, optional):
                Cursor — return entries strictly newer than this id.
                Pass the last ``entry_id`` from the previous read.
                ``None`` reads from the beginning of the log.
            max_count (`int`, defaults to ``100``):
                Maximum number of entries to return.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in append order. Empty
                list when no entries are newer than ``since``.
        """

    @abstractmethod
    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        """Trim the replay log at ``key``.

        Args:
            key (`str`):
                Log identifier.
            before_id (`str | None`, optional):
                Drop all entries with id strictly older than this.
                ``None`` drops the entire log (i.e. deletes the key).
        """

    # ------------------------------------------------------------------
    # Mode D — transient broadcast (fire-and-forget)
    # ------------------------------------------------------------------

    @abstractmethod
    async def publish(
        self,
        key: str,
        payload: dict,
    ) -> None:
        """Publish ``payload`` on the broadcast channel ``key``.

        Only subscribers connected at the moment of publish receive the
        payload — no history is retained. Use for wake-up signals or
        short-lived notifications where missed-while-offline is
        acceptable.

        Args:
            key (`str`):
                Channel identifier.
            payload (`dict`):
                JSON-serializable dict delivered as-is to subscribers.
        """

    @abstractmethod
    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield broadcast payloads for ``key`` until the consumer
        closes the generator.

        Subscriptions are best-effort: only payloads published *after*
        the subscription is established are delivered. Callers own the
        generator's lifetime — closing it releases the underlying
        subscription.

        Args:
            key (`str`):
                Channel identifier.
            on_ready (`Callable[[], None] | None`, optional):
                If supplied, invoked exactly once after the underlying
                subscription is established and before any payload is
                yielded. Used by callers that need to block a
                bootstrapping step (e.g.
                ``SessionTriggerListenerManager.start``) until the
                subscription is live, so a publish-immediately-after
                race is impossible.

        Yields:
            `dict`:
                Each payload originally passed to :meth:`publish`.
        """
        # The empty `yield` makes Python treat this as an async generator
        # function (return type AsyncGenerator) rather than a coroutine
        # returning an AsyncGenerator. Subclasses override it; this body
        # never runs.
        if False:  # pylint: disable=using-constant-test
            yield  # pylint: disable=unreachable

    # ------------------------------------------------------------------
    # Mode E — distributed lock (cluster-wide mutex)
    # ------------------------------------------------------------------

    @abstractmethod
    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """Acquire a distributed mutex on ``key``.

        Blocks until the lock is acquired, then yields. The
        implementation maintains the lock across long-running
        bodies (typically with a heartbeat task that renews the
        TTL) so the lease only expires if the holding process
        actually crashes — at which point another acquirer may
        take over after at most ``ttl_secs``.

        Args:
            key (`str`):
                Lock identifier.
            ttl_secs (`int`, defaults to ``600``):
                Lease duration in seconds. The implementation
                should renew this periodically while the body
                runs; callers do not need to.

        Yields:
            `None`: while the lock is held.
        """
        # The decorator-based abstract method requires a body for
        # @asynccontextmanager to work; subclasses override it.
        if False:  # pylint: disable=using-constant-test
            yield  # pylint: disable=unreachable

    @abstractmethod
    async def is_locked(self, key: str) -> bool:
        """Return whether ``key`` currently holds a lock.

        Args:
            key (`str`):
                Lock identifier (same key passed to
                :meth:`acquire_lock`).

        Returns:
            `bool`:
                ``True`` if some process holds the lock right now.
        """

    # ==================================================================
    # Domain helpers — concrete on the base class so all backends
    # share the same key conventions and serialisation rules.
    # ==================================================================

    # Session run coordination -----------------------------------------

    _SESSION_LOCK_KEY = "agentscope:session:lock:{sid}"
    """Per-session distributed-lock key template."""

    _SESSION_EVENTS_KEY = "agentscope:session:events:{sid}"
    """Per-session replay log + live pub/sub channel key template."""

    _SESSION_CANCEL_KEY = "agentscope:session:cancel"
    """Global cancel-broadcast channel. Used by
    :meth:`session_publish_cancel` to ask whichever process is currently
    running a given session to abort its run; the payload carries the
    target ``session_id`` and only the worker actually holding that
    session's task reacts."""

    _SESSION_RUN_TTL_SECS = 600
    """Default lock lease for a chat run (10 minutes)."""

    _SESSION_REPLAY_MAX_LEN = 1000
    """Replay log length cap; older events are trimmed on append."""

    @asynccontextmanager
    async def session_run(self, session_id: str) -> AsyncGenerator[None, None]:
        """Block until exclusive control of ``session_id`` is held.

        Two processes calling this for the same session_id queue up;
        the second only enters after the first releases (or its
        lease expires after a crash).

        On exit, the session's replay log is trimmed *before* the
        lock is released. This guarantees that any process which
        acquires the lock next sees a clean log (and any SSE
        subscriber that connects between this run and the next sees
        a clean slate, not stale events from this run).

        Args:
            session_id (`str`):
                The session to lock.

        Yields:
            `None`: while the session lock is held.
        """
        async with self.acquire_lock(
            self._SESSION_LOCK_KEY.format(sid=session_id),
            ttl_secs=self._SESSION_RUN_TTL_SECS,
        ):
            try:
                yield
            finally:
                # Replay log was the in-flight buffer for this run;
                # the chat run is responsible for persisting the
                # complete Msg to storage before releasing the lock,
                # so the log is no longer needed by any subscriber.
                await self.log_trim(
                    self._SESSION_EVENTS_KEY.format(sid=session_id),
                )

    async def session_is_running(self, session_id: str) -> bool:
        """Return whether some process is currently running this session.

        Args:
            session_id (`str`):
                The session to check.

        Returns:
            `bool`:
                ``True`` if a chat run holds the session lock right now.
        """
        return await self.is_locked(
            self._SESSION_LOCK_KEY.format(sid=session_id),
        )

    async def session_publish_event(
        self,
        session_id: str,
        event: dict,
    ) -> str:
        """Append a session event to the replay log + fan it out live.

        The single event is persisted to a Redis Stream (so late-joining
        subscribers can replay it) and simultaneously
        published on a Pub/Sub channel of the same key (so already-connected
        subscribers see it immediately). The persisted
        log entry id is included in the live payload as
        ``_entry_id`` so subscribers can deduplicate replay vs live
        delivery.

        Args:
            session_id (`str`):
                The session this event belongs to.
            event (`dict`):
                JSON-serializable event payload (typically
                ``AgentEvent.model_dump(mode='json')``).

        Returns:
            `str`:
                The replay-log entry id assigned by the backend.
        """
        key = self._SESSION_EVENTS_KEY.format(sid=session_id)
        entry_id = await self.log_append(
            key,
            event,
            max_len=self._SESSION_REPLAY_MAX_LEN,
        )
        await self.publish(key, {**event, "_entry_id": entry_id})
        return entry_id

    async def session_read_events(
        self,
        session_id: str,
        since: str | None = None,
        max_count: int = 1000,
    ) -> list[tuple[str, dict]]:
        """Read events from the session's replay log.

        Args:
            session_id (`str`):
                The session whose events to read.
            since (`str | None`, optional):
                Cursor — return entries strictly newer than this
                replay-log id. ``None`` reads from the beginning.
            max_count (`int`, defaults to ``1000``):
                Maximum events to return.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, event_payload)`` pairs in append order.
        """
        return await self.log_read(
            self._SESSION_EVENTS_KEY.format(sid=session_id),
            since=since,
            max_count=max_count,
        )

    async def session_subscribe_events(
        self,
        session_id: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Live-subscribe to a session's published events.

        Yields only payloads delivered after the subscription is
        established. To replay history first, call
        :meth:`session_read_events` separately.

        Args:
            session_id (`str`):
                The session to subscribe to.
            on_ready (`Callable[[], None] | None`, optional):
                Forwarded to the underlying :meth:`subscribe`.

        Yields:
            `dict`:
                Event payloads, with the internal ``_entry_id``
                field stripped (callers don't need to see it).
        """
        key = self._SESSION_EVENTS_KEY.format(sid=session_id)
        async for payload in self.subscribe(key, on_ready=on_ready):
            yield {k: v for k, v in payload.items() if k != "_entry_id"}

    # Cross-process cancel ---------------------------------------------

    async def session_publish_cancel(self, session_id: str) -> None:
        """Broadcast a cancel request for ``session_id``.

        Sent on a transient pub/sub channel: only processes that have
        an active :meth:`session_subscribe_cancel` subscription at
        publish time will see it. The process actually running the
        session is expected to be such a subscriber (its
        :class:`~agentscope.app._manager.CancelDispatcher` subscribes
        for the lifetime of the app). Other processes ignore the
        message because they hold no asyncio task for that session.

        Args:
            session_id (`str`):
                The session whose run should be cancelled.
        """
        await self.publish(
            self._SESSION_CANCEL_KEY,
            {"session_id": session_id},
        )

    async def session_subscribe_cancel(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Subscribe to the cancel-broadcast channel.

        Yields session ids as cancel requests arrive. A single
        subscriber per process is enough — the
        :class:`~agentscope.app._manager.CancelDispatcher` filters
        locally by checking whether the incoming ``session_id`` is in
        its own :class:`ChatRunRegistry`.

        Args:
            on_ready (`Callable[[], None] | None`, optional):
                Forwarded to the underlying :meth:`subscribe`.

        Yields:
            `str`:
                The session id from each incoming cancel payload.
        """
        async for payload in self.subscribe(
            self._SESSION_CANCEL_KEY,
            on_ready=on_ready,
        ):
            sid = payload.get("session_id")
            if isinstance(sid, str):
                yield sid

    # Purge -------------------------------------------------------------

    async def session_purge(self, session_id: str) -> None:
        """Delete all per-session bus state.

        Drops the events log and the inbox queue. The distributed
        run-lock is intentionally not touched: callers must ensure no
        run is in flight (e.g. by publishing cancel via
        :meth:`session_publish_cancel` and polling
        :meth:`is_locked` until it clears) before calling this.
        Any residual lock key expires on its own after at most
        :attr:`_SESSION_RUN_TTL_SECS`.

        Idempotent: a no-op when the keys are already absent.

        Args:
            session_id (`str`):
                The session whose bus state should be removed.
        """
        await self.log_trim(self._SESSION_EVENTS_KEY.format(sid=session_id))
        await self.queue_delete(self._INBOX_KEY.format(sid=session_id))

    # Inbox -----------------------------------------------------------

    _INBOX_KEY = "agentscope:inbox:{sid}"
    """Per-session inbox queue key template."""

    async def inbox_push(
        self,
        session_id: str,
        msg: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Append an inbound message to a session's inbox.

        Args:
            session_id (`str`):
                The recipient session id.
            msg (`dict`):
                JSON-serializable :class:`Msg` payload (use
                ``Msg.model_dump(mode='json')``).
            ttl_secs (`int | None`, optional):
                Inbox key lifetime; ``None`` means no expiry.

        Returns:
            `str`:
                The transport-level entry id from
                :meth:`queue_push`.
        """
        return await self.queue_push(
            self._INBOX_KEY.format(sid=session_id),
            msg,
            ttl_secs=ttl_secs,
        )

    async def inbox_drain(
        self,
        session_id: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain pending inbox messages for a session.

        Args:
            session_id (`str`):
                The session whose inbox to drain.
            max_count (`int`, defaults to ``100``):
                Maximum entries to drain in one call.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, msg_payload)`` pairs in arrival order.
        """
        return await self.queue_drain(
            self._INBOX_KEY.format(sid=session_id),
            max_count=max_count,
        )

    # Wakeup ----------------------------------------------------------

    _WAKEUP_QUEUE_KEY = "agentscope:wakeups"
    """Shared wake-up queue (durable Redis Stream)."""

    _WAKEUP_SIGNAL_KEY = "agentscope:wakeup_signal"
    """Shared Pub/Sub channel that nudges dispatchers to drain the
    wake-up queue."""

    async def enqueue_wakeup(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Enqueue a wake-up request and signal dispatchers.

        Producers (e.g. ``TeamSay``, ``AgentCreate``, the scheduler
        trigger, or the BG-tool completion watcher) call this after
        depositing a message in the recipient's inbox. The shared
        :class:`WakeupDispatcher` (one per process) drains the queue
        on each signal and starts a chat run for any session that
        is not currently active.

        Args:
            user_id (`str`):
                The owning user id.
            session_id (`str`):
                The session to wake.
            agent_id (`str`):
                The agent id that owns the session.
        """
        await self.queue_push(
            self._WAKEUP_QUEUE_KEY,
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
            },
        )
        await self.publish(self._WAKEUP_SIGNAL_KEY, {})

    async def dequeue_wakeups(
        self,
        max_count: int = 64,
    ) -> list[dict]:
        """Drain pending wake-up entries.

        Args:
            max_count (`int`, defaults to ``64``):
                Maximum entries to drain per call.

        Returns:
            `list[dict]`:
                Entries shaped ``{"user_id", "session_id",
                "agent_id"}`` in enqueue order.
        """
        entries = await self.queue_drain(
            self._WAKEUP_QUEUE_KEY,
            max_count=max_count,
        )
        return [payload for _entry_id, payload in entries]

    async def subscribe_wakeup_signal(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Subscribe to the shared wake-up signal channel.

        Each yielded item indicates "drain the queue now"; the
        payload itself carries no business data.

        Args:
            on_ready (`Callable[[], None] | None`, optional):
                Forwarded to the underlying :meth:`subscribe`.

        Yields:
            `dict`:
                The empty / opaque signal payload.
        """
        async for payload in self.subscribe(
            self._WAKEUP_SIGNAL_KEY,
            on_ready=on_ready,
        ):
            yield payload
