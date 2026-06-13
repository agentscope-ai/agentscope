# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""An in-memory message bus backed by Python data structures.

Suitable for development, testing, and single-process deployments.  All
state resides in process memory — restarting the process loses all data
and the bus cannot coordinate across process boundaries.
"""
import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Callable

from ._base import MessageBus


class MemMessageBus(MessageBus):
    """An in-memory message bus implementation.

    Mapping of bus modes to in-memory primitives:

    - **Mode A (drain queue)** uses :class:`asyncio.Queue` per key.
      ``queue_push`` puts into the queue; ``queue_drain`` pumps up to
      ``max_count`` items with a short non-blocking poll.  ``queue_delete``
      drops the queue.
    - **Mode C (replay log)** uses an ordered :class:`list` per key.
      Each entry gets a monotonic integer id.  ``log_read`` slices from
      ``since``; ``log_append`` pushes to the end; ``log_trim`` prunes
      old entries or drops the whole log.
    - **Mode D (transient broadcast)** uses per-channel subscriber
      :class:`asyncio.Queue` instances.  ``publish`` pushes to all
      subscribed queues; ``subscribe`` yields from a personal queue.
    - **Mode E (distributed lock)** uses per-key :class:`asyncio.Lock`.
      Single-process only — no cross-process coordination.
    - **Mode F (registry map)** uses nested :class:`dict` per namespace.

    Usage::

        async with MemMessageBus() as bus:
            await bus.publish("chan", {"msg": "hello"})
    """

    def __init__(self) -> None:
        """Initialise the in-memory bus with empty data structures."""
        # Mode A — drain queues: key → asyncio.Queue
        self._queues: dict[str, asyncio.Queue[tuple[str, dict]]] = {}

        # Mode C — replay logs: key → list of (int_id, payload)
        self._logs: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        # Per-log monotonic id counter
        self._log_counters: dict[str, int] = defaultdict(int)

        # Mode D — transient broadcast: channel → set of subscriber queues
        self._channels: dict[
            str, set[asyncio.Queue[dict]],
        ] = defaultdict(set)

        # Mode E — distributed locks: key → asyncio.Lock
        self._locks: dict[str, asyncio.Lock] = {}

        # Mode F — registry maps: namespace → dict of field→value
        self._registries: dict[str, dict[str, str]] = defaultdict(dict)

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def aclose(self) -> None:
        """Release resources — clear all stored data."""
        self._queues.clear()
        self._logs.clear()
        self._log_counters.clear()
        self._channels.clear()
        self._locks.clear()
        self._registries.clear()

    # ==================================================================
    # Mode A — drain queue
    # ==================================================================

    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        """Append ``payload`` to the drain queue at ``key``.

        ``ttl_secs`` is accepted for interface parity but ignored —
        in-memory queues have no expiry.
        """
        _ = ttl_secs
        entry_id = uuid.uuid4().hex
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
        await self._queues[key].put((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain up to ``max_count`` entries from the queue at ``key``."""
        queue = self._queues.get(key)
        if queue is None:
            return []
        results: list[tuple[str, dict]] = []
        for _ in range(max_count):
            try:
                entry_id, payload = queue.get_nowait()
                results.append((entry_id, payload))
            except asyncio.QueueEmpty:
                break
        return results

    async def queue_delete(self, key: str) -> None:
        """Delete the drain queue at ``key``."""
        _ = self._queues.pop(key, None)

    # ==================================================================
    # Mode C — replay log
    # ==================================================================

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        """Append ``payload`` to the replay log at ``key``.

        ``ttl_secs`` is accepted for interface parity but ignored.
        """
        _ = ttl_secs
        entry_id = self._log_counters[key]
        self._log_counters[key] = entry_id + 1
        self._logs[key].append((entry_id, payload))
        if max_len is not None:
            if len(self._logs[key]) > max_len:
                self._logs[key] = self._logs[key][-max_len:]
        return str(entry_id)

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Read up to ``max_count`` entries newer than ``since``."""
        entries = self._logs.get(key)
        if entries is None:
            return []
        # Map string ids back to int for comparison
        since_int = int(since) if since is not None else -1
        results: list[tuple[str, dict]] = []
        for entry_id, payload in entries:
            if entry_id <= since_int:
                continue
            results.append((str(entry_id), payload))
            if len(results) >= max_count:
                break
        return results

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        """Trim the replay log at ``key``."""
        if before_id is None:
            _ = self._logs.pop(key, None)
            _ = self._log_counters.pop(key, None)
            return
        before_int = int(before_id)
        entries = self._logs.get(key)
        if entries is None:
            return
        self._logs[key] = [
            (eid, p) for eid, p in entries if eid >= before_int
        ]

    # ==================================================================
    # Mode D — transient broadcast
    # ==================================================================

    async def publish(
        self,
        key: str,
        payload: dict,
    ) -> None:
        """Publish ``payload`` on the broadcast channel ``key``."""
        subscribers = self._channels.get(key)
        if subscribers is None:
            return
        # Snapshot subscribers to avoid mutation-during-iteration issues
        for q in list(subscribers):
            await q.put(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield broadcast payloads on ``key`` until the generator closes."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._channels[key].add(q)
        try:
            if on_ready is not None:
                on_ready()
            while True:
                payload = await q.get()
                yield payload
        finally:
            self._channels[key].discard(q)
            if not self._channels[key]:
                _ = self._channels.pop(key, None)

    # ==================================================================
    # Mode E — distributed lock
    # ==================================================================

    # Heartbeat interval as fraction of TTL (renews every ttl/2).
    _LOCK_HEARTBEAT_RATIO: float = 0.5

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """Acquire an in-process mutex on ``key``.

        Single-process only: uses :class:`asyncio.Lock` internally.
        The ``ttl_secs`` parameter is accepted for interface parity but
        the lock has no expiry — it is released only when the context
        manager exits.
        """
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        lock = self._locks[key]

        # Heartbeat task exists for interface parity — it does nothing
        # because asyncio.Lock never expires.  We still spawn and cancel
        # it so subclass or decorator-based tests that mock
        # _LOCK_HEARTBEAT_RATIO work the same way.
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(
                    max(1.0, ttl_secs * self._LOCK_HEARTBEAT_RATIO),
                )

        async with lock:
            hb_task = asyncio.create_task(
                _heartbeat(),
                name=f"mem-lock-heartbeat:{key}",
            )
            try:
                yield
            finally:
                _ = hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass
                # Clean up lock dict entry if no one is waiting.
                if not lock.locked():
                    _ = self._locks.pop(key, None)

    async def is_locked(self, key: str) -> bool:
        """Return whether ``key`` is currently locked."""
        lock = self._locks.get(key)
        return lock is not None and lock.locked()

    # ==================================================================
    # Mode F — registry map
    # ==================================================================

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Set ``field`` to ``value`` in the registry at ``namespace``.

        ``ttl_secs`` is accepted for interface parity but ignored.
        """
        _ = ttl_secs
        self._registries[namespace][field] = value

    async def registry_del(self, namespace: str, field: str) -> None:
        """Remove ``field`` from the registry at ``namespace``."""
        reg = self._registries.get(namespace)
        if reg is not None:
            _ = reg.pop(field, None)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        """Return whether ``field`` exists in the registry."""
        reg = self._registries.get(namespace)
        return reg is not None and field in reg

    async def registry_getall(
        self,
        namespace: str,
    ) -> dict[str, str]:
        """Return all field-value pairs in the registry."""
        reg = self._registries.get(namespace)
        if reg is None:
            return {}
        return dict(reg)

    async def registry_drop(self, namespace: str) -> None:
        """Delete the entire registry at ``namespace``."""
        _ = self._registries.pop(namespace, None)
