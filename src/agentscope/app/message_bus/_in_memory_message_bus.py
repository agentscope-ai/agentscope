# -*- coding: utf-8 -*-
"""In-memory message bus implementation.

A pure-Python :class:`MessageBus` backed by :mod:`asyncio` primitives,
Python dicts and lists.  Designed for **single-process** use — local
development, unit tests, and examples that want to avoid a Redis
dependency.

.. note::

   **Not suitable for production multiprocess deployments.** All state
   lives inside the process; there is no persistence, no cross-process
   pub/sub, and the "distributed" lock is just an :class:`asyncio.Lock`.
   For real deployments use :class:`RedisMessageBus` (or another
   networked backend).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Callable, Self

from ._base import MessageBus


class InMemoryMessageBus(MessageBus):  # pylint: disable=R0904
    """In-memory implementation of :class:`MessageBus`.

    Mapping of bus modes to in-memory structures:

    - **Mode A (drain queue)** — each key maps to a
      :class:`list[tuple[str, dict]]` of ``(entry_id, payload)`` pairs.
      ``queue_push`` appends; ``queue_drain`` pops from the front (FIFO)
      and deletes the returned entries.
    - **Mode C (replay log)** — same underlying list structure, but
      ``log_read`` is non-destructive.  ``log_trim`` removes entries
      in-place.
    - **Mode D (transient broadcast)** — each channel keeps a set of
      :class:`asyncio.Queue` subscribers.  ``publish`` pushes to all
      currently-subscribed queues; ``subscribe`` yields from one.
    - **Mode E (distributed lock)** — :class:`asyncio.Lock` per key,
      suitable only for single-process concurrency.
    - **Mode F (registry map)** — ``dict[str, dict[str, str]]``, one
      nested dict per namespace.

    Entry ids are monotonic ``"<seq>-0"`` strings (e.g. ``"1-0"``,
    ``"2-0"``, …).  They are **not** lexicographically sortable once
    the sequence exceeds single digits; comparison must parse the
    numeric prefix (as :meth:`log_read` does internally).
    """

    def __init__(self) -> None:
        """Initialise empty in-memory stores."""
        # Global auto-increment counter for entry ids.
        self._seq: int = 0

        # Mode A — drain queues: key -> [(entry_id, payload), ...]
        self._queues: dict[str, list[tuple[str, dict]]] = defaultdict(list)

        # Mode B — reliable queues. ``delivered`` prevents a new read from
        # returning an entry already assigned to the group; ``pending`` maps
        # it to (consumer, last-delivery monotonic timestamp).
        self._reliable_queues: dict[
            str,
            list[tuple[str, dict]],
        ] = defaultdict(list)
        self._reliable_groups: dict[
            tuple[str, str],
            dict[str, object],
        ] = {}

        # Mode C — replay logs: key -> [(entry_id, payload), ...]
        self._logs: dict[str, list[tuple[str, dict]]] = defaultdict(list)

        # Mode D — pub/sub: channel -> set of asyncio.Queue subscribers
        self._subscribers: dict[
            str,
            set[asyncio.Queue[dict | None]],
        ] = defaultdict(set)

        # Mode E — locks: key -> asyncio.Lock
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Track which key is currently held so is_locked() works.
        self._lock_holders: dict[str, str] = {}

        # Mode F — registry maps: namespace -> {field: value}
        self._registries: dict[str, dict[str, str]] = defaultdict(dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """No-op — nothing to open for in-memory transport.

        Returns:
            `Self`:
                The bus, ready for use.
        """
        return self

    async def aclose(self) -> None:
        """Signal all open subscribers so their generators terminate."""
        for subs in self._subscribers.values():
            for q in subs:
                q.put_nowait(None)
        self._subscribers.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        """Return a monotonic ``"<seq>-0"`` id.

        The format matches Redis Stream entry ids so callers that parse
        ids (e.g. ``_exclusive_start``) keep working.

        Returns:
            `str`:
                A unique, monotonically increasing entry id.
        """
        self._seq += 1
        return f"{self._seq}-0"

    # ------------------------------------------------------------------
    # Mode A — drain queue
    # ------------------------------------------------------------------

    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Append ``payload`` to the in-memory drain queue at ``key``.

        ``ttl_secs`` is accepted for API compatibility but ignored — the
        in-memory implementation does not expire keys.

        Args:
            key (`str`):
                Queue identifier.
            payload (`dict`):
                JSON-serializable dict to enqueue.
            ttl_secs (`int | None`, optional):
                Ignored (no-op).

        Returns:
            `str`:
                The synthetic entry id assigned to this entry.
        """
        entry_id = self._next_id()
        self._queues[key].append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain up to ``max_count`` entries from the queue at ``key``.

        Returned entries are removed from the internal list.

        Args:
            key (`str`):
                Queue identifier.
            max_count (`int`, defaults to ``100``):
                Maximum entries to return.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in arrival order.
        """
        q = self._queues.get(key)
        if not q:
            return []
        drained = q[:max_count]
        del q[:max_count]
        return drained

    async def queue_delete(self, key: str) -> None:
        """Delete the drain queue at ``key``.

        Args:
            key (`str`):
                Queue identifier.
        """
        self._queues.pop(key, None)

    # ------------------------------------------------------------------
    # Mode B — reliable work queue
    # ------------------------------------------------------------------

    async def reliable_queue_push(self, key: str, payload: dict) -> str:
        """Append an entry without acknowledging it on read."""
        entry_id = self._next_id()
        self._reliable_queues[key].append((entry_id, payload))
        return entry_id

    async def reliable_queue_ensure_group(self, key: str, group: str) -> None:
        """Create in-memory group bookkeeping when absent."""
        self._reliable_groups.setdefault(
            (key, group),
            {"delivered": set(), "pending": {}},
        )

    async def reliable_queue_read(
        self,
        key: str,
        group: str,
        consumer: str,
        *,
        max_count: int = 1,
        block_ms: int = 1000,
    ) -> list[tuple[str, dict]]:
        """Assign previously unseen entries to ``consumer``."""
        await self.reliable_queue_ensure_group(key, group)
        deadline = time.monotonic() + max(0, block_ms) / 1000
        while True:
            state = self._reliable_groups[(key, group)]
            delivered = state["delivered"]
            pending = state["pending"]
            assert isinstance(delivered, set)
            assert isinstance(pending, dict)
            result: list[tuple[str, dict]] = []
            now = time.monotonic()
            for entry_id, payload in self._reliable_queues.get(key, []):
                if entry_id in delivered:
                    continue
                delivered.add(entry_id)
                pending[entry_id] = (consumer, now)
                result.append((entry_id, payload))
                if len(result) >= max_count:
                    return result
            if result or block_ms <= 0 or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))

    async def reliable_queue_reclaim(
        self,
        key: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        max_count: int = 1,
    ) -> list[tuple[str, dict]]:
        """Transfer idle pending entries to ``consumer``."""
        await self.reliable_queue_ensure_group(key, group)
        state = self._reliable_groups[(key, group)]
        pending = state["pending"]
        assert isinstance(pending, dict)
        payloads = dict(self._reliable_queues.get(key, []))
        now = time.monotonic()
        result: list[tuple[str, dict]] = []
        for entry_id, (_owner, touched_at) in list(pending.items()):
            if (now - touched_at) * 1000 < min_idle_ms:
                continue
            pending[entry_id] = (consumer, now)
            if entry_id in payloads:
                result.append((entry_id, payloads[entry_id]))
            if len(result) >= max_count:
                break
        return result

    async def reliable_queue_touch(
        self,
        key: str,
        group: str,
        consumer: str,
        entry_ids: list[str],
    ) -> None:
        """Refresh entries that are still owned by ``consumer``."""
        await self.reliable_queue_ensure_group(key, group)
        pending = self._reliable_groups[(key, group)]["pending"]
        assert isinstance(pending, dict)
        now = time.monotonic()
        for entry_id in entry_ids:
            current = pending.get(entry_id)
            if current is not None and current[0] == consumer:
                pending[entry_id] = (consumer, now)

    async def reliable_queue_ack(
        self,
        key: str,
        group: str,
        entry_ids: list[str],
    ) -> int:
        """Remove acknowledged entries and their pending records."""
        await self.reliable_queue_ensure_group(key, group)
        state = self._reliable_groups[(key, group)]
        pending = state["pending"]
        delivered = state["delivered"]
        assert isinstance(pending, dict)
        assert isinstance(delivered, set)
        acked = 0
        for entry_id in entry_ids:
            if pending.pop(entry_id, None) is not None:
                acked += 1
            delivered.discard(entry_id)
        ids = set(entry_ids)
        self._reliable_queues[key] = [
            item
            for item in self._reliable_queues.get(key, [])
            if item[0] not in ids
        ]
        return acked

    # ------------------------------------------------------------------
    # Mode C — replay log
    # ------------------------------------------------------------------

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        """Append ``payload`` to the replay log at ``key``.

        Args:
            key (`str`):
                Log identifier.
            payload (`dict`):
                JSON-serializable dict to append.
            ttl_secs (`int | None`, optional):
                Ignored (no-op — in-memory does not expire keys).
            max_len (`int | None`, optional):
                If set, trim the log to approximately this many entries
                after appending.

        Returns:
            `str`:
                The entry id assigned to the new entry.
        """
        entry_id = self._next_id()
        log = self._logs[key]
        log.append((entry_id, payload))
        if max_len is not None and len(log) > max_len:
            del log[: len(log) - max_len]
        return entry_id

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Read up to ``max_count`` entries newer than ``since``.

        Reads are non-destructive.

        Args:
            key (`str`):
                Log identifier.
            since (`str | None`, optional):
                Exclusive cursor — return entries whose id is strictly
                greater than ``since``.  ``None`` reads from the start.
            max_count (`int`, defaults to ``100``):
                Maximum entries to return.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in append order.
        """
        log = self._logs.get(key)
        if not log:
            return []
        if since is None:
            return log[:max_count]
        # Find the first entry with id > since.  Entry ids are
        # "<seq>-0" strings; integer comparison on the seq prefix is
        # sufficient because our ids are monotonic.
        since_seq = int(since.split("-")[0])
        start = 0
        for i, (eid, _) in enumerate(log):
            if int(eid.split("-")[0]) > since_seq:
                start = i
                break
        else:
            # All entries are <= since.
            return []
        return log[start : start + max_count]

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
                ``None`` drops the entire log.
        """
        if before_id is None:
            self._logs.pop(key, None)
            return
        log = self._logs.get(key)
        if not log:
            return
        before_seq = int(before_id.split("-")[0])
        self._logs[key] = [
            (eid, p) for eid, p in log if int(eid.split("-")[0]) >= before_seq
        ]

    # ------------------------------------------------------------------
    # Mode D — transient broadcast
    # ------------------------------------------------------------------

    async def publish(
        self,
        key: str,
        payload: dict,
    ) -> None:
        """Publish ``payload`` to all current subscribers of ``key``.

        Args:
            key (`str`):
                Channel identifier.
            payload (`dict`):
                JSON-serializable dict delivered to each subscriber.
        """
        for q in self._subscribers.get(key, set()):
            q.put_nowait(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield broadcast payloads for ``key`` until the consumer
        closes the generator.

        Args:
            key (`str`):
                Channel identifier.
            on_ready (`Callable[[], None] | None`, optional):
                Called once after the subscription is established and
                before any payload is yielded.

        Yields:
            `dict`:
                Each payload from :meth:`publish`.
        """
        q: asyncio.Queue[dict | None] = asyncio.Queue()
        self._subscribers[key].add(q)
        try:
            if on_ready is not None:
                on_ready()
            while True:
                item = await q.get()
                if item is None:
                    # Sentinel from aclose() — shut down gracefully.
                    break
                yield item
        finally:
            self._subscribers[key].discard(q)

    # ------------------------------------------------------------------
    # Mode E — distributed lock (process-local asyncio.Lock)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """Acquire a process-local mutex on ``key``.

        In-memory equivalent of a distributed lock.  ``ttl_secs`` is
        accepted for API compatibility but does **not** expire the lock
        automatically — the lock is held until the context exits.

        Args:
            key (`str`):
                Lock identifier.
            ttl_secs (`int`, defaults to ``600``):
                Ignored (no automatic expiry).

        Yields:
            `None`: while the lock is held.
        """
        lock = self._locks[key]
        token = uuid.uuid4().hex
        async with lock:
            self._lock_holders[key] = token
            try:
                yield
            finally:
                self._lock_holders.pop(key, None)

    async def is_locked(self, key: str) -> bool:
        """Return whether ``key`` currently holds a lock.

        Args:
            key (`str`):
                Lock identifier.

        Returns:
            `bool`:
                ``True`` if some coroutine holds the lock.
        """
        return key in self._lock_holders

    # ------------------------------------------------------------------
    # Mode F — registry map
    # ------------------------------------------------------------------

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Set ``field`` to ``value`` in the registry at ``namespace``.

        ``ttl_secs`` is accepted for API compatibility but ignored.

        Args:
            namespace (`str`):
                Registry key.
            field (`str`):
                Field name.
            value (`str`):
                Value to store.
            ttl_secs (`int | None`, optional):
                Ignored (no TTL support).
        """
        self._registries[namespace][field] = value

    async def registry_del(self, namespace: str, field: str) -> None:
        """Remove ``field`` from the registry at ``namespace``.

        Args:
            namespace (`str`):
                Registry key.
            field (`str`):
                Field to remove.
        """
        reg = self._registries.get(namespace)
        if reg is not None:
            reg.pop(field, None)

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
        return field in self._registries.get(namespace, {})

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
                All entries (shallow copy). Empty dict when absent.
        """
        return dict(self._registries.get(namespace, {}))

    async def registry_drop(self, namespace: str) -> None:
        """Delete the entire registry at ``namespace``.

        Args:
            namespace (`str`):
                Registry key to delete.
        """
        self._registries.pop(namespace, None)
