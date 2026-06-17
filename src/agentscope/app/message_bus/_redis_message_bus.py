# -*- coding: utf-8 -*-
"""The Redis-backed message bus implementation."""
import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Callable, Self, TYPE_CHECKING

from ._base import MessageBus

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisMessageBus(MessageBus):
    """Redis-backed implementation of :class:`MessageBus`.

    Mapping of bus modes to Redis primitives:

    - **Mode A (drain queue)** uses a Redis Stream per key. ``XADD``
      appends a payload whose single field ``payload`` carries the
      JSON-serialised dict. ``queue_drain`` performs ``XRANGE`` followed
      by per-id ``XDEL`` so the read is destructive and idempotent
      under the single-consumer-per-key invariant. ``ttl_secs`` is
      enforced via ``EXPIRE`` after each push (sliding TTL).
    - **Mode C (replay log)** also uses a Redis Stream, but never
      ``XDEL``s on read. Trimming happens via ``XADD … MAXLEN ~N``
      (approximate, for performance) on append, via the ``ttl_secs``
      ``EXPIRE`` on the whole key, or explicitly via
      :meth:`log_trim`. ``log_read`` uses ``XRANGE`` with an exclusive
      start id derived from ``since``.
    - **Mode D (transient broadcast)** rides Redis Pub/Sub. Wake-ups
      are best-effort: payloads published before a subscription exists
      are not delivered.

    The bus owns its own connection pool by default; an external pool
    may be supplied for tests or for sharing a pool across services.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        **kwargs: Any,
    ) -> None:
        """Store connection parameters; the actual pool is created in
        :meth:`__aenter__`.

        Args:
            host (`str`, defaults to ``"localhost"``):
                Redis server host.
            port (`int`, defaults to ``6379``):
                Redis server port.
            db (`int`, defaults to ``0``):
                Redis logical database index.
            password (`str | None`, optional):
                Redis password if required by the server.
            connection_pool (`ConnectionPool | None`, optional):
                An externally managed connection pool. When provided
                the pool is used as-is and **not** closed by
                :meth:`aclose` — the caller retains ownership of its
                lifecycle. When omitted a pool is created from
                *host*/*port*/*db*/*password* on :meth:`__aenter__`
                and closed on :meth:`aclose`.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to
                ``redis.asyncio.ConnectionPool`` when the pool is
                created internally (e.g. ``max_connections=20``,
                ``socket_timeout=5``).
        """
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._external_pool: ConnectionPool | None = connection_pool
        self._kwargs = kwargs

        # Populated in __aenter__; None until the context is entered.
        self._client: Redis | None = None
        self._owned_pool: ConnectionPool | None = None

    async def __aenter__(self) -> Self:
        """Create the connection pool and Redis client.

        If an external pool was supplied at construction time it is
        used directly and its lifecycle remains the caller's
        responsibility. Otherwise, an internal pool is created from the
        stored host/port/db parameters and will be closed by
        :meth:`aclose`.

        Returns:
            `Self`:
                The bus, ready for use as an async context manager.
        """
        try:
            import redis.asyncio as aioredis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisMessageBus. "
                "Install it with: pip install redis[async]",
            ) from e

        if self._external_pool is not None:
            pool = self._external_pool
        else:
            self._owned_pool = aioredis.ConnectionPool(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                **self._kwargs,
            )
            pool = self._owned_pool

        self._client = aioredis.Redis(connection_pool=pool)
        return self

    async def aclose(self) -> None:
        """Close the connection pool if it was created internally.

        Externally supplied pools are left open — the caller owns them.
        """
        if self._owned_pool is not None:
            await self._owned_pool.aclose()
            self._owned_pool = None
        self._client = None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and release resources.

        Args:
            exc_type (`type[BaseException] | None`):
                Exception class raised inside the context, if any.
            exc_value (`BaseException | None`):
                Exception instance raised inside the context, if any.
            traceback (`Any`):
                Traceback associated with the exception, if any.
        """
        await self.aclose()

    def get_client(self) -> Redis:
        """Return the underlying Redis client.

        Only valid inside the async context (between :meth:`__aenter__`
        and :meth:`aclose`).

        Returns:
            `Redis`:
                The asyncio Redis client instance.
        """
        return self._client

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _exclusive_start(since: str | None) -> str:
        """Compute the exclusive start id for ``XRANGE`` given a cursor.

        Redis Streams 6.2+ support the ``(`` prefix for exclusive
        ranges, so the simplest portable encoding of "after ``since``"
        is ``f"({since}"``. ``None`` resolves to ``"-"`` (the absolute
        minimum id).

        Args:
            since (`str | None`):
                The previous cursor returned by :meth:`log_read`, or
                ``None`` to read from the beginning.

        Returns:
            `str`:
                The start argument for ``XRANGE``.
        """
        if since is None:
            return "-"
        return f"({since}"

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
        """Append ``payload`` to the drain queue at ``key``.

        Args:
            key (`str`):
                Stream key for this drain queue.
            payload (`dict`):
                JSON-serializable dict; encoded into the ``payload``
                field of a Redis Stream entry.
            ttl_secs (`int | None`, optional):
                If set, refresh the key's expiry on every push
                (sliding TTL). ``None`` means no TTL — the queue
                persists until drained or deleted explicitly.

        Returns:
            `str`:
                The Redis Stream entry id assigned by ``XADD``.
        """
        entry_id = await self._client.xadd(
            key,
            {"payload": json.dumps(payload)},
        )
        if ttl_secs is not None:
            await self._client.expire(key, ttl_secs)
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Drain up to ``max_count`` entries from the queue at ``key``.

        Implementation: ``XRANGE`` followed by ``XDEL`` of the
        returned ids, so the operation is destructive in a single
        round-trip pair. Entries that arrive between ``XRANGE`` and
        ``XDEL`` are not affected.

        Args:
            key (`str`):
                Stream key for the drain queue.
            max_count (`int`, defaults to ``100``):
                Maximum entries to return in one call.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in arrival order. Empty
                list when the queue is empty or absent.
        """
        entries = await self._client.xrange(key, count=max_count)
        if not entries:
            return []

        results: list[tuple[str, dict]] = []
        ids_to_delete: list[str] = []
        for entry_id, fields in entries:
            ids_to_delete.append(entry_id)
            raw = fields.get("payload")
            if raw is None:
                continue
            results.append((entry_id, json.loads(raw)))

        if ids_to_delete:
            await self._client.xdel(key, *ids_to_delete)

        return results

    async def queue_delete(self, key: str) -> None:
        """Delete the drain queue at ``key``.

        Args:
            key (`str`):
                Stream key for the drain queue. ``DEL`` is a no-op
                when the key does not exist.
        """
        await self._client.delete(key)

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
                Stream key for this replay log.
            payload (`dict`):
                JSON-serializable dict; encoded into the ``payload``
                field of a Redis Stream entry.
            ttl_secs (`int | None`, optional):
                If set, refresh the key's expiry on every append
                (sliding TTL). ``None`` means no TTL.
            max_len (`int | None`, optional):
                If set, cap the log at approximately this many entries
                using ``XADD MAXLEN ~N``. The cap is approximate so
                Redis can use its O(1) trim-by-radix-tree-node mode.
                ``None`` means no cap.

        Returns:
            `str`:
                The Redis Stream entry id, suitable as a cursor for
                later :meth:`log_read` calls.
        """
        kwargs: dict[str, Any] = {}
        if max_len is not None:
            kwargs["maxlen"] = max_len
            kwargs["approximate"] = True
        entry_id = await self._client.xadd(
            key,
            {"payload": json.dumps(payload)},
            **kwargs,
        )
        if ttl_secs is not None:
            await self._client.expire(key, ttl_secs)
        return entry_id

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Read up to ``max_count`` entries newer than ``since``.

        Args:
            key (`str`):
                Stream key for the replay log.
            since (`str | None`, optional):
                Exclusive cursor — return entries strictly newer than
                this id (typically the last id from a previous read).
                ``None`` reads from the beginning.
            max_count (`int`, defaults to ``100``):
                Maximum entries to return.

        Returns:
            `list[tuple[str, dict]]`:
                ``(entry_id, payload)`` pairs in append order.
        """
        start = self._exclusive_start(since)
        entries = await self._client.xrange(
            key,
            min=start,
            count=max_count,
        )
        results: list[tuple[str, dict]] = []
        for entry_id, fields in entries:
            raw = fields.get("payload")
            if raw is None:
                continue
            results.append((entry_id, json.loads(raw)))
        return results

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        """Trim the replay log at ``key``.

        Args:
            key (`str`):
                Stream key for the replay log.
            before_id (`str | None`, optional):
                Drop all entries with id strictly older than this.
                ``None`` deletes the entire key.
        """
        if before_id is None:
            await self._client.delete(key)
            return
        # XTRIM MINID drops entries with id < before_id.
        await self._client.xtrim(key, minid=before_id)

    # ------------------------------------------------------------------
    # Mode F — registry map (hash-keyed namespace)
    # ------------------------------------------------------------------

    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        """Set ``field`` in the Redis Hash at ``namespace``.

        Args:
            namespace (`str`):
                Hash key.
            field (`str`):
                Hash field.
            value (`str`):
                Value to store.
            ttl_secs (`int | None`, optional):
                Refresh the key's expiry (sliding TTL).
        """
        await self._client.hset(namespace, field, value)
        if ttl_secs is not None:
            await self._client.expire(namespace, ttl_secs)

    async def registry_del(self, namespace: str, field: str) -> None:
        """Remove ``field`` from the Redis Hash at ``namespace``.

        Args:
            namespace (`str`):
                Hash key.
            field (`str`):
                Hash field to remove.
        """
        await self._client.hdel(namespace, field)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        """Return whether ``field`` exists in the Hash at ``namespace``.

        Args:
            namespace (`str`):
                Hash key.
            field (`str`):
                Hash field to check.

        Returns:
            `bool`:
                ``True`` if the field exists.
        """
        return bool(await self._client.hexists(namespace, field))

    async def registry_getall(
        self,
        namespace: str,
    ) -> dict[str, str]:
        """Return all field-value pairs from the Hash at ``namespace``.

        Args:
            namespace (`str`):
                Hash key.

        Returns:
            `dict[str, str]`:
                All entries. Empty dict when the key is absent.
        """
        return await self._client.hgetall(namespace) or {}

    async def registry_drop(self, namespace: str) -> None:
        """Delete the entire Hash at ``namespace``.

        Args:
            namespace (`str`):
                Hash key to delete.
        """
        await self._client.delete(namespace)

    # ------------------------------------------------------------------
    # Mode D — transient broadcast
    # ------------------------------------------------------------------

    # Poll interval for the pub/sub read loop. Bounding each read keeps
    # long-lived idle subscriptions resilient: an idle ``socket_timeout``
    # read (raised when the connection defines one, or when the server
    # drops idle connections) surfaces as a benign per-poll timeout that
    # we ignore, instead of a fatal error that tears down the generator.
    _SUBSCRIBE_POLL_TIMEOUT_SECS = 1.0

    async def publish(
        self,
        key: str,
        payload: dict,
    ) -> None:
        """Publish ``payload`` on the broadcast channel ``key``.

        Args:
            key (`str`):
                Pub/Sub channel name.
            payload (`dict`):
                JSON-serializable dict; encoded as the channel
                message body.
        """
        await self._client.publish(key, json.dumps(payload))

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield broadcast payloads on ``key`` until the consumer
        closes the generator.

        Args:
            key (`str`):
                Pub/Sub channel name.
            on_ready (`Callable[[], None] | None`, optional):
                Called once after ``SUBSCRIBE`` has been issued (and
                before any payload is yielded). Lets callers block on
                a "subscription is live" event so they can publish
                immediately after starting a subscriber without losing
                the first message to a SUBSCRIBE/PUBLISH race.

        Yields:
            `dict`:
                Each payload originally passed to :meth:`publish`.
        """
        from redis import exceptions as redis_exceptions

        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(key)
            if on_ready is not None:
                on_ready()
            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self._SUBSCRIBE_POLL_TIMEOUT_SECS,
                    )
                except redis_exceptions.TimeoutError:
                    # Idle read timeout (e.g. the connection defines a
                    # ``socket_timeout`` or the server drops idle
                    # connections). No message arrived in this window;
                    # keep listening rather than crashing the loop.
                    continue
                if message is None:
                    # No payload within the poll window — keep waiting.
                    continue
                if message.get("type") != "message":
                    # Skip ``subscribe`` ack and similar control frames.
                    continue
                data = message.get("data")
                if data is None:
                    continue
                yield json.loads(data)
        finally:
            await pubsub.unsubscribe(key)
            await pubsub.aclose()

    # ------------------------------------------------------------------
    # Mode E — distributed lock
    # ------------------------------------------------------------------

    # Poll interval while waiting for a contested lock.
    _LOCK_RETRY_DELAY_SECS = 0.1

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        """Acquire ``key`` as a distributed mutex.

        Implementation:

        - ``SET key <random-token> NX EX ttl_secs`` to claim the
          lock atomically. Retries every
          ``_LOCK_RETRY_DELAY_SECS`` until acquired.
        - A heartbeat task renews the TTL every ``ttl_secs / 2``
          seconds while the body runs, so a long-running holder
          does not lose the lease.
        - On exit, the heartbeat is cancelled and the lock is
          released by GET-then-DEL guarded on the random token —
          we never delete a key whose value isn't ours, so a
          process whose lease has already expired (and been re-
          acquired by someone else) cannot accidentally release
          the new holder's lock. The non-atomic GET+DEL race
          window opens only after the heartbeat is cancelled, in
          a sub-millisecond span; the ``ttl_secs`` lease still has
          plenty of time left so the race effectively never
          materialises.

        Args:
            key (`str`):
                Lock identifier.
            ttl_secs (`int`, defaults to ``600``):
                Lease duration; auto-renewed via heartbeat.

        Yields:
            `None`: while the lock is held.
        """
        token = uuid.uuid4().hex
        # Acquire (poll until success).
        while True:
            ok = await self._client.set(key, token, nx=True, ex=ttl_secs)
            if ok:
                break
            await asyncio.sleep(self._LOCK_RETRY_DELAY_SECS)

        # Heartbeat: renew TTL every ttl/2 seconds.
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1.0, ttl_secs / 2))
                await self._client.expire(key, ttl_secs)

        hb_task = asyncio.create_task(
            _heartbeat(),
            name=f"lock-heartbeat:{key}",
        )

        try:
            yield
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
            # Release: only delete if the value still matches our
            # token. Failures are swallowed (the lease may have
            # expired naturally, in which case there is nothing to
            # do).
            try:
                current = await self._client.get(key)
                if current == token:
                    await self._client.delete(key)
            except Exception:  # pylint: disable=broad-except
                pass

    async def is_locked(self, key: str) -> bool:
        """Return whether ``key`` currently holds a lock.

        Args:
            key (`str`):
                Lock identifier.

        Returns:
            `bool`:
                ``True`` if Redis has a value at this key.
        """
        result = await self._client.exists(key)
        return bool(result)
