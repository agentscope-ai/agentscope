# -*- coding: utf-8 -*-
"""Session mapper — pure KV mapping from (channel_id, mapper_key) to
session_id.

The mapper does not create sessions; session lifecycle is orchestrated
by ``ChannelGateway``. The mapper key is computed by the Gateway based
on the configured ``DmScope``:

- MAIN: ``"main"``
- PER_PEER: ``"{channel_user_id}"``
- PER_CHAT: ``"{chat_id}"``
- PER_CHANNEL_PEER: ``"{chat_id}:{channel_user_id}"``
"""
from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from ..message_bus import MessageBus


class SessionMappingRecord(BaseModel):
    """Persisted channel → session mapping record."""

    channel_id: str
    mapper_key: str
    agent_id: str
    session_id: str
    agentscope_user_id: str
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )
    last_active_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )


class SessionMapperBase(ABC):
    """Abstract KV mapper: (channel_id, mapper_key) → session_id."""

    @abstractmethod
    async def get(self, channel_id: str, mapper_key: str) -> str | None:
        """Return the session_id for a mapping, or None if absent."""

    @abstractmethod
    async def set_if_absent(
        self,
        channel_id: str,
        mapper_key: str,
        session_id: str,
        record: SessionMappingRecord,
    ) -> str:
        """Atomically set the mapping if it does not exist.

        Returns the actual session_id (which may differ if another task
        raced and won).
        """

    @abstractmethod
    async def delete(self, channel_id: str, mapper_key: str) -> None:
        """Remove a single mapping entry."""

    @abstractmethod
    async def delete_all_for_channel(self, channel_id: str) -> None:
        """Remove all mappings belonging to a channel (cascade cleanup)."""

    async def list_keys(  # pylint: disable=unused-argument
        self,
        channel_id: str,
    ) -> list[str]:
        """Return all mapper_keys for a channel. Default: empty list."""
        return []

    async def record_chat_id(  # pylint: disable=unused-argument
        self,
        channel_id: str,
        chat_id: str,
    ) -> None:
        """Record a seen chat_id for a channel (idempotent)."""

    async def list_seen_chat_ids(  # pylint: disable=unused-argument
        self,
        channel_id: str,
    ) -> list[str]:
        """Return all previously seen chat_ids for a channel."""
        return []


class InMemorySessionMapper(SessionMapperBase):
    """In-memory session mapper for development and testing.

    NOTE: Not suitable for multi-process deployments.
    Use ``MessageBusSessionMapper`` for distributed scenarios.
    """

    def __init__(self) -> None:
        self._store: dict[str, SessionMappingRecord] = {}
        self._seen_chat_ids: dict[str, set[str]] = {}

    def _key(self, channel_id: str, mapper_key: str) -> str:
        return f"{channel_id}::{mapper_key}"

    async def get(self, channel_id: str, mapper_key: str) -> str | None:
        record = self._store.get(self._key(channel_id, mapper_key))
        return record.session_id if record else None

    async def set_if_absent(
        self,
        channel_id: str,
        mapper_key: str,
        session_id: str,
        record: SessionMappingRecord,
    ) -> str:
        key = self._key(channel_id, mapper_key)
        existing = self._store.get(key)
        if existing is not None:
            return existing.session_id
        self._store[key] = record
        return session_id

    async def delete(self, channel_id: str, mapper_key: str) -> None:
        self._store.pop(self._key(channel_id, mapper_key), None)

    async def delete_all_for_channel(self, channel_id: str) -> None:
        prefix = f"{channel_id}::"
        keys_to_remove = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._store[k]

    async def list_keys(self, channel_id: str) -> list[str]:
        prefix = f"{channel_id}::"
        return [k[len(prefix) :] for k in self._store if k.startswith(prefix)]

    async def record_chat_id(
        self,
        channel_id: str,
        chat_id: str,
    ) -> None:
        self._seen_chat_ids.setdefault(channel_id, set()).add(chat_id)

    async def list_seen_chat_ids(self, channel_id: str) -> list[str]:
        return sorted(self._seen_chat_ids.get(channel_id, set()))


class MessageBusSessionMapper(SessionMapperBase):
    """Distributed session mapper backed by MessageBus registry.

    Uses ``MessageBus.registry_set/registry_getall`` to store mappings in
    Redis (or whatever transport backs the bus). Each channel gets its own
    registry namespace; fields within the namespace are mapper_keys.

    This implementation is safe for multi-process clusters:
    - ``set_if_absent`` uses a distributed lock to ensure atomicity.
    - All nodes share the same underlying state via the bus.
    """

    _NAMESPACE_PREFIX = "agentscope:channel:session_map:"

    def __init__(self, message_bus: MessageBus) -> None:
        self._bus = message_bus

    def _namespace(self, channel_id: str) -> str:
        return f"{self._NAMESPACE_PREFIX}{channel_id}"

    async def get(self, channel_id: str, mapper_key: str) -> str | None:
        import json

        ns = self._namespace(channel_id)
        raw = await self._bus.registry_get(ns, mapper_key)
        if raw is None:
            return None
        record_data = json.loads(raw)
        return record_data.get("session_id")

    async def set_if_absent(
        self,
        channel_id: str,
        mapper_key: str,
        session_id: str,
        record: SessionMappingRecord,
    ) -> str:
        import json

        ns = self._namespace(channel_id)
        lock_key = f"{ns}:lock:{mapper_key}"

        async with self._bus.acquire_lock(lock_key, ttl_secs=10):
            existing = await self.get(channel_id, mapper_key)
            if existing is not None:
                return existing
            await self._bus.registry_set(
                ns,
                mapper_key,
                json.dumps(record.model_dump(mode="json")),
            )
        return session_id

    async def delete(self, channel_id: str, mapper_key: str) -> None:
        ns = self._namespace(channel_id)
        await self._bus.registry_del(ns, mapper_key)

    async def delete_all_for_channel(self, channel_id: str) -> None:
        ns = self._namespace(channel_id)
        await self._bus.registry_drop(ns)

    async def list_keys(self, channel_id: str) -> list[str]:
        ns = self._namespace(channel_id)
        all_fields = await self._bus.registry_getall(ns)
        return list(all_fields.keys())

    def _chat_ids_namespace(self, channel_id: str) -> str:
        return f"agentscope:channel:seen_chats:{channel_id}"

    async def record_chat_id(
        self,
        channel_id: str,
        chat_id: str,
    ) -> None:
        ns = self._chat_ids_namespace(channel_id)
        await self._bus.registry_set(ns, chat_id, "1")

    async def list_seen_chat_ids(self, channel_id: str) -> list[str]:
        ns = self._chat_ids_namespace(channel_id)
        all_fields = await self._bus.registry_getall(ns)
        return sorted(all_fields.keys())
