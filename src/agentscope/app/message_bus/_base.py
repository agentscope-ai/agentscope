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

from typing_extensions import deprecated

from ._keys import MessageBusKeys


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

    # ------------------------------------------------------------------
    # Mode F — registry map (hash-keyed namespace)
    # ------------------------------------------------------------------

    @abstractmethod
    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Set ``field`` to ``value`` in the registry at ``namespace``.

        If the namespace does not exist it is created. When
        ``ttl_secs`` is supplied, the namespace's TTL is refreshed
        (sliding) — individual fields do not carry independent TTLs.

        Args:
            namespace (`str`):
                Registry key (e.g. ``"agentscope:bg_tasks:sess123"``).
            field (`str`):
                Field name within the registry.
            value (`str`):
                Serialized value to store.
            ttl_secs (`int | None`, optional):
                Refresh the namespace expiry to this many seconds.
        """

    @abstractmethod
    async def registry_del(self, namespace: str, field: str) -> None:
        """Remove ``field`` from the registry at ``namespace``.

        A no-op when the field or namespace does not exist.

        Args:
            namespace (`str`):
                Registry key.
            field (`str`):
                Field to remove.
        """

    @abstractmethod
    async def registry_exists(self, namespace: str, field: str) -> bool:
        """Return whether ``field`` exists in the registry at
        ``namespace``.

        Args:
            namespace (`str`):
                Registry key.
            field (`str`):
                Field to check.

        Returns:
            `bool`:
                ``True`` if the field is present.
        """

    @abstractmethod
    async def registry_getall(
        self,
        namespace: str,
    ) -> dict[str, str]:
        """Return all field-value pairs in the registry at
        ``namespace``.

        Args:
            namespace (`str`):
                Registry key.

        Returns:
            `dict[str, str]`:
                All entries. Empty dict when the namespace is absent.
        """

    @abstractmethod
    async def registry_get(
        self,
        namespace: str,
        field: str,
    ) -> str | None:
        """Return the value of a single ``field`` in the registry at
        ``namespace``, or ``None`` if absent.

        Args:
            namespace (`str`):
                Registry key.
            field (`str`):
                Field to retrieve.

        Returns:
            `str | None`:
                The stored value, or ``None`` if missing.
        """

    @abstractmethod
    async def registry_drop(self, namespace: str) -> None:
        """Delete the entire registry at ``namespace``.

        Idempotent: a no-op when the namespace does not exist.

        Args:
            namespace (`str`):
                Registry key to delete.
        """

    # ==================================================================
    # Deprecated domain helpers
    #
    # These thin shells delegate to the generic primitives above. They
    # exist so that code written against the old API keeps working for
    # one release cycle; new code should use the primitives + MessageBusKeys
    # (or the standalone functions in agentscope.app._service) directly.
    #
    # The _XXX_KEY class-level constants are kept as well — some tests
    # reference them — but new code should use MessageBusKeys instead.
    # ==================================================================

    # Key constants (kept for backward compat) -------------------------

    _SESSION_LOCK_KEY = "agentscope:session:lock:{sid}"
    _SESSION_EVENTS_KEY = "agentscope:session:events:{sid}"
    _SESSION_CANCEL_KEY = "agentscope:session:cancel"
    _SESSION_RUN_TTL_SECS = 600
    _SESSION_REPLAY_MAX_LEN = 1000
    _INBOX_KEY = "agentscope:inbox:{sid}"
    _WAKEUP_QUEUE_KEY = "agentscope:wakeups"
    _WAKEUP_SIGNAL_KEY = "agentscope:wakeup_signal"
    _BG_TASKS_KEY = "agentscope:bg_tasks:{sid}"
    _BG_TASKS_TTL_SECS = 86400
    _TASK_CANCEL_KEY = "agentscope:task:cancel"

    # Session run coordination -----------------------------------------

    @deprecated(
        "Use acquire_lock(MessageBusKeys.session_lock(sid), ...) directly.",
    )
    @asynccontextmanager
    async def session_run(self, session_id: str) -> AsyncGenerator[None, None]:
        """Acquire the session lock, yield, then trim the replay log."""
        async with self.acquire_lock(
            self._SESSION_LOCK_KEY.format(sid=session_id),
            ttl_secs=self._SESSION_RUN_TTL_SECS,
        ):
            try:
                yield
            finally:
                await self.log_trim(
                    self._SESSION_EVENTS_KEY.format(sid=session_id),
                )

    @deprecated(
        "Use is_locked(MessageBusKeys.session_lock(sid)) directly.",
    )
    async def session_is_running(self, session_id: str) -> bool:
        """Check whether some process holds the session lock."""
        return await self.is_locked(
            self._SESSION_LOCK_KEY.format(sid=session_id),
        )

    @deprecated(
        "Use publish_session_event(bus, sid, event) from "
        "agentscope.app._bus_ops directly.",
    )
    async def session_publish_event(
        self,
        session_id: str,
        event: dict,
    ) -> str:
        """Append + fan-out a session event."""
        key = self._SESSION_EVENTS_KEY.format(sid=session_id)
        entry_id = await self.log_append(
            key,
            event,
            max_len=self._SESSION_REPLAY_MAX_LEN,
        )
        await self.publish(key, {**event, "_entry_id": entry_id})
        return entry_id

    @deprecated(
        "Use log_read(MessageBusKeys.session_events(sid), ...) directly.",
    )
    async def session_read_events(
        self,
        session_id: str,
        since: str | None = None,
        max_count: int = 1000,
    ) -> list[tuple[str, dict]]:
        """Read events from the session's replay log."""
        return await self.log_read(
            self._SESSION_EVENTS_KEY.format(sid=session_id),
            since=since,
            max_count=max_count,
        )

    @deprecated(
        "Use subscribe(MessageBusKeys.session_events(sid), ...) directly, "
        "stripping _entry_id from each payload.",
    )
    async def session_subscribe_events(
        self,
        session_id: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Live-subscribe to session events, stripping _entry_id."""
        key = self._SESSION_EVENTS_KEY.format(sid=session_id)
        async for payload in self.subscribe(key, on_ready=on_ready):
            yield {k: v for k, v in payload.items() if k != "_entry_id"}

    # Cross-process cancel ---------------------------------------------

    @deprecated(
        "Use publish(MessageBusKeys.session_cancel_channel(), "
        "{'session_id': sid}) directly.",
    )
    async def session_publish_cancel(self, session_id: str) -> None:
        """Broadcast a session cancel request."""
        await self.publish(
            self._SESSION_CANCEL_KEY,
            {"session_id": session_id},
        )

    @deprecated(
        "Use subscribe(MessageBusKeys.session_cancel_channel(), ...) "
        "directly, extracting session_id from the payload.",
    )
    async def session_subscribe_cancel(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Subscribe to session cancel broadcasts, yielding session ids."""
        async for payload in self.subscribe(
            self._SESSION_CANCEL_KEY,
            on_ready=on_ready,
        ):
            sid = payload.get("session_id")
            if isinstance(sid, str):
                yield sid

    # Purge -------------------------------------------------------------

    @deprecated(
        "Call log_trim / queue_delete / registry_drop with "
        "MessageBusKeys directly.",
    )
    async def session_purge(self, session_id: str) -> None:
        """Delete all per-session bus state."""
        await self.log_trim(self._SESSION_EVENTS_KEY.format(sid=session_id))
        await self.queue_delete(self._INBOX_KEY.format(sid=session_id))
        await self.registry_drop(self._BG_TASKS_KEY.format(sid=session_id))

    # Inbox -----------------------------------------------------------

    @deprecated(
        "Use queue_push(MessageBusKeys.inbox(sid), ...) directly.",
    )
    async def inbox_push(
        self,
        session_id: str,
        msg: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Push a message to a session's inbox."""
        return await self.queue_push(
            self._INBOX_KEY.format(sid=session_id),
            msg,
            ttl_secs=ttl_secs,
        )

    @deprecated(
        "Use queue_drain(MessageBusKeys.inbox(sid), ...) directly.",
    )
    async def inbox_drain(
        self,
        session_id: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain pending inbox messages for a session."""
        return await self.queue_drain(
            self._INBOX_KEY.format(sid=session_id),
            max_count=max_count,
        )

    # Wakeup ----------------------------------------------------------

    @deprecated(
        "Use enqueue_run_trigger(bus, ...) from "
        "agentscope.app._bus_ops directly.",
    )
    async def enqueue_wakeup(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Enqueue an idle-session wake-up."""
        await self.queue_push(
            self._WAKEUP_QUEUE_KEY,
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "kind": MessageBusKeys.WAKEUP_KIND_WAKE,
                "input": None,
            },
        )
        await self.publish(self._WAKEUP_SIGNAL_KEY, {})

    @deprecated(
        "Use enqueue_run_trigger(bus, ...) from "
        "agentscope.app._bus_ops directly.",
    )
    async def enqueue_input(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        *,
        kind: str,
        inputs: dict | None = None,
    ) -> None:
        """Enqueue a typed run trigger."""
        await self.queue_push(
            self._WAKEUP_QUEUE_KEY,
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "kind": kind,
                "input": inputs,
            },
        )
        await self.publish(self._WAKEUP_SIGNAL_KEY, {})

    @deprecated(
        "Use queue_drain(MessageBusKeys.wakeup_queue(), ...) directly.",
    )
    async def dequeue_wakeups(
        self,
        max_count: int = 64,
    ) -> list[dict]:
        """Drain pending run-trigger entries."""
        entries = await self.queue_drain(
            self._WAKEUP_QUEUE_KEY,
            max_count=max_count,
        )
        return [payload for _entry_id, payload in entries]

    @deprecated(
        "Use subscribe(MessageBusKeys.wakeup_signal(), ...) directly.",
    )
    async def subscribe_wakeup_signal(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Subscribe to the shared wake-up signal channel."""
        async for payload in self.subscribe(
            self._WAKEUP_SIGNAL_KEY,
            on_ready=on_ready,
        ):
            yield payload

    # Background task registry -------------------------------------------

    @deprecated(
        "Use registry_set(MessageBusKeys.bg_tasks(sid), ...) directly.",
    )
    async def bg_task_register(
        self,
        session_id: str,
        task_id: str,
        metadata: str,
    ) -> None:
        """Register a background task."""
        await self.registry_set(
            self._BG_TASKS_KEY.format(sid=session_id),
            task_id,
            metadata,
            ttl_secs=self._BG_TASKS_TTL_SECS,
        )

    @deprecated(
        "Use registry_del(MessageBusKeys.bg_tasks(sid), tid) directly.",
    )
    async def bg_task_unregister(
        self,
        session_id: str,
        task_id: str,
    ) -> None:
        """Unregister a background task."""
        await self.registry_del(
            self._BG_TASKS_KEY.format(sid=session_id),
            task_id,
        )

    @deprecated(
        "Use registry_exists(MessageBusKeys.bg_tasks(sid), tid) directly.",
    )
    async def bg_task_exists(
        self,
        session_id: str,
        task_id: str,
    ) -> bool:
        """Check whether a background task is registered."""
        return await self.registry_exists(
            self._BG_TASKS_KEY.format(sid=session_id),
            task_id,
        )

    @deprecated(
        "Use registry_getall(MessageBusKeys.bg_tasks(sid)) directly.",
    )
    async def bg_task_list(
        self,
        session_id: str,
    ) -> dict[str, str]:
        """List all background tasks for a session."""
        return await self.registry_getall(
            self._BG_TASKS_KEY.format(sid=session_id),
        )

    @deprecated(
        "Use registry_drop(MessageBusKeys.bg_tasks(sid)) directly.",
    )
    async def bg_task_purge(self, session_id: str) -> None:
        """Delete all background task entries for a session."""
        await self.registry_drop(
            self._BG_TASKS_KEY.format(sid=session_id),
        )

    @deprecated(
        "Use publish(MessageBusKeys.task_cancel_channel(), "
        "{'task_id': tid}) directly.",
    )
    async def task_publish_cancel(self, task_id: str) -> None:
        """Broadcast a cancel request for a single background task."""
        await self.publish(self._TASK_CANCEL_KEY, {"task_id": task_id})

    @deprecated(
        "Use subscribe(MessageBusKeys.task_cancel_channel(), ...) "
        "directly, extracting task_id from the payload.",
    )
    async def task_subscribe_cancel(
        self,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Subscribe to task cancel broadcasts, yielding task ids."""
        async for payload in self.subscribe(
            self._TASK_CANCEL_KEY,
            on_ready=on_ready,
        ):
            tid = payload.get("task_id")
            if isinstance(tid, str):
                yield tid
