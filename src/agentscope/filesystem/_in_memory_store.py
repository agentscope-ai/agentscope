# -*- coding: utf-8 -*-
"""In-memory implementation of BaseStore."""
from __future__ import annotations

import threading
from typing import Any

from ._base_store import BaseStore, StoreKey, StoreValue


class InMemoryStore(BaseStore):
    """Thread-safe in-memory store for testing and ephemeral use."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, StoreValue]] = {}
        self._lock = threading.Lock()

    async def get(self, key: StoreKey) -> StoreValue | None:
        with self._lock:
            ns = self._data.get(key.namespace, {})
            return ns.get(key.key)

    async def put(
        self,
        key: StoreKey,
        data: bytes,
        version: int | None = None,
    ) -> StoreValue:
        with self._lock:
            ns = self._data.setdefault(key.namespace, {})
            old = ns.get(key.key)
            new_version = version if version is not None else (old.version + 1 if old else 1)
            val = StoreValue(data=data, version=new_version)
            ns[key.key] = val
            return val

    async def put_if_version(
        self,
        key: StoreKey,
        data: bytes,
        expected_version: int,
    ) -> StoreValue | None:
        with self._lock:
            ns = self._data.setdefault(key.namespace, {})
            old = ns.get(key.key)
            if old is None:
                if expected_version != 0:
                    return None
                val = StoreValue(data=data, version=1)
            else:
                if old.version != expected_version:
                    return None
                val = StoreValue(data=data, version=old.version + 1)
            ns[key.key] = val
            return val

    async def search(self, namespace: str, prefix: str) -> list[StoreKey]:
        with self._lock:
            ns = self._data.get(namespace, {})
            return [
                StoreKey(namespace=namespace, key=k)
                for k in ns
                if k.startswith(prefix)
            ]

    async def delete(self, key: StoreKey) -> bool:
        with self._lock:
            ns = self._data.get(key.namespace)
            if ns is None:
                return False
            if key.key in ns:
                del ns[key.key]
                return True
            return False
