# -*- coding: utf-8 -*-
"""In-memory implementation of :class:`BlobStoreBase`.

This is a lightweight blob store that keeps all documents in memory.
It is primarily intended for testing and fast local execution where you
want to avoid writing to the filesystem or setting up external cloud storage.

URIs are formatted as ``memory://{key}``.
"""
import io
from contextlib import asynccontextmanager
from typing import IO, AsyncIterator

from ._base import AsyncReadable, BlobStoreBase


_SCHEME = "memory://"


class _MemoryReadable:
    """Adapter that exposes :class:`AsyncReadable` over an in-memory byte buffer."""

    def __init__(self, data: bytes) -> None:
        """Initialize with bytes data."""
        self._stream = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        """Read up to *n* bytes; ``-1`` reads to EOF."""
        return self._stream.read(n)


class MemoryBlobStore(BlobStoreBase):
    """Store blobs entirely in memory.
    
    This blob store is extremely fast but ephemeral. All stored data will
    be lost when the application process terminates.
    """

    def __init__(self) -> None:
        """Initialize an empty memory blob store."""
        # Key to bytes mapping
        self._blobs: dict[str, bytes] = {}

    @staticmethod
    def _parse_uri(uri: str) -> str:
        """Split a ``memory://{key}`` URI into its key."""
        if not uri.startswith(_SCHEME):
            raise ValueError(f"Not a memory blob URI: {uri!r}")
        key = uri[len(_SCHEME) :]
        if not key:
            raise ValueError(f"Malformed memory blob URI: {uri!r}")
        return key

    async def write_stream(self, key: str, stream: IO[bytes]) -> str:
        """Stream-write a blob into memory and return its URI."""
        chunks = []
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            chunks.append(chunk)
            
        self._blobs[key] = b"".join(chunks)
        return f"{_SCHEME}{key}"

    @asynccontextmanager
    async def open(
        self,
        uri: str,
    ) -> AsyncIterator[AsyncReadable]:
        """Stream-read a blob from memory by URI."""
        key = self._parse_uri(uri)
        if key not in self._blobs:
            raise FileNotFoundError(f"Blob not found in memory: {uri}")
        
        yield _MemoryReadable(self._blobs[key])

    async def delete(self, uri: str) -> None:
        """Delete the object at ``uri``. Idempotent."""
        key = self._parse_uri(uri)
        self._blobs.pop(key, None)

    async def size(self, uri: str) -> int | None:
        """Return the object's byte length, or ``None`` if gone."""
        key = self._parse_uri(uri)
        if key not in self._blobs:
            return None
        return len(self._blobs[key])

    async def exists(self, uri: str) -> bool:
        """Return whether the object at ``uri`` is present."""
        key = self._parse_uri(uri)
        return key in self._blobs
