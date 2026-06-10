# -*- coding: utf-8 -*-
"""Namespace-scoped KV store abstraction."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoreKey:
    """Qualified key within a namespace."""

    namespace: str
    key: str


@dataclass(frozen=True)
class StoreValue:
    """Value + opaque version for CAS."""

    data: bytes
    version: int


class BaseStore(ABC):
    """Minimal namespace-scoped KV store with optimistic concurrency."""

    @abstractmethod
    async def get(self, key: StoreKey) -> StoreValue | None:
        """Fetch value by key."""

    @abstractmethod
    async def put(
        self,
        key: StoreKey,
        data: bytes,
        version: int | None = None,
    ) -> StoreValue:
        """Store data, optionally at a specific version (last-write-wins if None)."""

    @abstractmethod
    async def put_if_version(
        self,
        key: StoreKey,
        data: bytes,
        expected_version: int,
    ) -> StoreValue | None:
        """Store only if current version equals *expected_version*.

        Returns the new value on success, ``None`` if CAS failed.
        """

    @abstractmethod
    async def search(self, namespace: str, prefix: str) -> list[StoreKey]:
        """List keys under *namespace* matching *prefix*."""

    @abstractmethod
    async def delete(self, key: StoreKey) -> bool:
        """Delete key. Returns ``True`` if something was removed."""
