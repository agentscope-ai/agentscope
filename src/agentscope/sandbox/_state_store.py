# -*- coding: utf-8 -*-
"""Sandbox state store implementations."""

import asyncio
from abc import ABC, abstractmethod

from ._types import SandboxIsolationKey


class SandboxStateStore(ABC):
    """Abstract store for persisting sandbox resume state."""

    @abstractmethod
    async def load(self, key: SandboxIsolationKey) -> str | None:
        """Load serialized state for the given isolation key.

        Returns ``None`` when no state is present.
        """

    @abstractmethod
    async def save(self, key: SandboxIsolationKey, state_json: str) -> None:
        """Persist serialized state for the given isolation key."""

    @abstractmethod
    async def delete(self, key: SandboxIsolationKey) -> None:
        """Remove persisted state for the given isolation key."""


class InMemorySandboxStateStore(SandboxStateStore):
    """In-memory state store, suitable for tests and single-process use."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _key(self, key: SandboxIsolationKey) -> str:
        return f"{key.scope.value}:{key.value}"

    async def load(self, key: SandboxIsolationKey) -> str | None:
        async with self._lock:
            return self._data.get(self._key(key))

    async def save(self, key: SandboxIsolationKey, state_json: str) -> None:
        async with self._lock:
            self._data[self._key(key)] = state_json

    async def delete(self, key: SandboxIsolationKey) -> None:
        async with self._lock:
            self._data.pop(self._key(key), None)
