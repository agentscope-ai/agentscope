# -*- coding: utf-8 -*-
"""Tests for :class:`MemoryBlobStore`.

Properties under test:
- A blob written through ``write_stream`` reads back identical bytes via ``open``.
- The URI emitted is ``memory://{key}`` and round-trips through ``exists`` / ``delete``.
- ``exists`` distinguishes present from missing without raising.
- ``delete`` is idempotent.
- Bad URIs raise ``ValueError`` before any read/write logic.
"""
import io
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app.rag.blob_store import MemoryBlobStore


class MemoryBlobStoreTest(IsolatedAsyncioTestCase):
    """The memory blob store test case."""

    async def asyncSetUp(self) -> None:
        """Set up an isolated memory blob store for each test."""
        self.store = MemoryBlobStore()

    async def test_write_and_read(self) -> None:
        """A blob written via write_stream reads back identical bytes."""
        payload = b"hello memory store " * 1024  # 19 KB, crosses 8 KB chunk boundary
        stream = io.BytesIO(payload)
        
        uri = await self.store.write_stream("test_docs/hello.txt", stream)
        self.assertEqual(uri, "memory://test_docs/hello.txt")

        # Read back in one go
        async with self.store.open(uri) as readable:
            data = await readable.read()
            self.assertEqual(data, payload)

        # Read back in chunks
        async with self.store.open(uri) as readable:
            data1 = await readable.read(100)
            data2 = await readable.read()
            self.assertEqual(data1 + data2, payload)

    async def test_exists_and_delete(self) -> None:
        """Exists returns bool; delete is idempotent."""
        uri = await self.store.write_stream("temp.bin", io.BytesIO(b"data"))
        
        self.assertTrue(await self.store.exists(uri))
        
        await self.store.delete(uri)
        self.assertFalse(await self.store.exists(uri))
        
        # Deleting a non-existent blob should not raise
        await self.store.delete(uri)

    async def test_size(self) -> None:
        """Size returns exact byte length or None if missing."""
        payload = b"12345"
        uri = await self.store.write_stream("size.bin", io.BytesIO(payload))
        
        self.assertEqual(await self.store.size(uri), 5)
        
        await self.store.delete(uri)
        self.assertIsNone(await self.store.size(uri))

    async def test_read_missing_blob(self) -> None:
        """Opening a missing blob raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            async with self.store.open("memory://missing.bin"):
                pass

    async def test_invalid_uri(self) -> None:
        """Passing malformed URIs raises ValueError."""
        with self.assertRaisesRegex(ValueError, "Not a memory blob URI"):
            async with self.store.open("s3://bucket/key"):
                pass

        with self.assertRaisesRegex(ValueError, "Malformed memory blob URI"):
            async with self.store.open("memory://"):
                pass
