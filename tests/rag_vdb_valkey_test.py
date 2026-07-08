# -*- coding: utf-8 -*-
"""Unit tests for the ValkeyStore class.

These tests mock the Valkey Glide client so no running Valkey server is
required. They verify that ValkeyStore correctly translates the
VectorStoreBase interface into the expected Valkey Search commands.
"""

import json
import struct
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import pytest

glide = pytest.importorskip("glide")

from agentscope.message import TextBlock  # noqa: E402
from agentscope.rag import (  # noqa: E402
    Chunk,
    ValkeyStore,
    VectorRecord,
)


def _make_record(
    text: str,
    vector: list[float],
    document_id: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
    metadata: dict | None = None,
) -> VectorRecord:
    """Build a VectorRecord for testing."""
    return VectorRecord(
        vector=vector,
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=text),
            source=f"{document_id}.txt",
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            metadata=metadata or {},
        ),
    )


def _encode_vector(vector: list[float]) -> bytes:
    """Encode a float vector to little-endian bytes."""
    return struct.pack(f"<{len(vector)}f", *vector)


class ValkeyStoreTest(IsolatedAsyncioTestCase):
    """The test cases for the ValkeyStore class."""

    async def asyncSetUp(self) -> None:
        """Create a ValkeyStore with a mocked client before each test."""
        self._exit_stack = AsyncExitStack()
        self.mock_client = AsyncMock()

        # Patch the client creation to return our mock
        patcher = patch(
            "agentscope.rag._vdb._valkey.ValkeyStore._create_client",
            return_value=self.mock_client,
        )
        self._exit_stack.enter_context(patcher)

        self.store = await self._exit_stack.enter_async_context(
            ValkeyStore(host="localhost", port=6379),
        )

    async def asyncTearDown(self) -> None:
        """Close the store after each test."""
        await self._exit_stack.aclose()

    async def test_create_collection_new_index(self) -> None:
        """create_collection calls FT.CREATE when index does not exist."""
        from glide import RequestError

        # FT.INFO raises RequestError (index doesn't exist) twice
        # (fast path + double-check under lock), then FT.CREATE succeeds
        self.mock_client.custom_command = AsyncMock(
            side_effect=[
                RequestError("Unknown index name"),
                RequestError("Unknown index name"),
                b"OK",
            ],
        )

        await self.store.create_collection("kb-1", dimensions=3)

        calls = self.mock_client.custom_command.call_args_list
        self.assertEqual(calls[0][0][0][0], "FT.INFO")
        self.assertEqual(calls[1][0][0][0], "FT.INFO")
        self.assertEqual(calls[2][0][0][0], "FT.CREATE")

        # Verify FT.CREATE has correct index name and dimensions
        create_args = calls[2][0][0]
        self.assertEqual(create_args[1], "kb-1")
        self.assertIn("3", create_args)

    async def test_create_collection_already_exists(self) -> None:
        """create_collection is a no-op when index already exists."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[b"index_name", b"kb-1"],
        )

        await self.store.create_collection("kb-1", dimensions=3)

        calls = self.mock_client.custom_command.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0][0], "FT.INFO")

    async def test_delete_collection(self) -> None:
        """delete_collection drops the index and scans/deletes keys."""
        self.mock_client.custom_command = AsyncMock(
            side_effect=[
                b"OK",  # FT.DROPINDEX
                [b"0", []],  # SCAN returns no keys
            ],
        )

        await self.store.delete_collection("kb-1")

        calls = self.mock_client.custom_command.call_args_list
        self.assertEqual(calls[0][0][0], ["FT.DROPINDEX", "kb-1"])
        self.assertEqual(calls[1][0][0][0], "SCAN")

    async def test_delete_collection_not_exists(self) -> None:
        """delete_collection handles missing index gracefully."""
        from glide import RequestError

        self.mock_client.custom_command = AsyncMock(
            side_effect=[
                RequestError("Index: with name 'kb-1' not found"),
                [b"0", []],
            ],
        )

        await self.store.delete_collection("kb-1")

    async def test_delete_collection_reraises_unexpected_error(
        self,
    ) -> None:
        """delete_collection re-raises non-'unknown index' errors."""
        from glide import RequestError

        self.mock_client.custom_command = AsyncMock(
            side_effect=RequestError("connection refused"),
        )

        with self.assertRaises(RequestError):
            await self.store.delete_collection("kb-1")

    async def test_has_collection_true(self) -> None:
        """has_collection returns True when FT.INFO succeeds."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[b"index_name", b"kb-1"],
        )

        result = await self.store.has_collection("kb-1")
        self.assertTrue(result)

    async def test_has_collection_false(self) -> None:
        """has_collection returns False when FT.INFO raises."""
        from glide import RequestError

        self.mock_client.custom_command = AsyncMock(
            side_effect=RequestError("Unknown index name"),
        )

        result = await self.store.has_collection("kb-1")
        self.assertFalse(result)

    async def test_insert_stores_hash(self) -> None:
        """insert calls hset with correct field structure."""
        self.mock_client.hset = AsyncMock(return_value=5)

        record = _make_record(
            "Hello world!",
            [1.0, 0.0, 0.0],
            document_id="doc-1",
        )
        await self.store.insert("kb-1", [record])

        self.mock_client.hset.assert_called_once()
        call_args = self.mock_client.hset.call_args

        key = call_args[0][0]
        self.assertTrue(key.startswith("kb-1:"))

        fields = call_args[0][1]
        self.assertEqual(fields["document_id"], "doc-1")
        self.assertEqual(fields["source"], "doc-1.txt")
        self.assertEqual(fields["vector"], _encode_vector([1.0, 0.0, 0.0]))
        # No redundant metadata field
        self.assertNotIn("metadata", fields)

        chunk_data = json.loads(fields["chunk"])
        self.assertEqual(chunk_data["content"]["text"], "Hello world!")

    async def test_insert_empty_records(self) -> None:
        """Inserting an empty record list is a no-op."""
        await self.store.insert("kb-1", [])
        self.mock_client.hset.assert_not_called()

    async def test_delete_by_document_id(self) -> None:
        """delete finds and removes all records for a document."""
        self.mock_client.custom_command = AsyncMock(
            side_effect=[
                [2, {b"kb-1:abc123": {}, b"kb-1:def456": {}}],
                [0, {}],
            ],
        )

        await self.store.delete("kb-1", document_id="doc-1")

        calls = self.mock_client.custom_command.call_args_list
        self.assertEqual(calls[0][0][0][0], "FT.SEARCH")
        self.assertIn("@document_id", calls[0][0][0][2])
        # Batch DEL with multiple keys
        self.assertEqual(
            calls[1][0][0],
            ["DEL", "kb-1:abc123", "kb-1:def456"],
        )

    async def test_search_returns_results(self) -> None:
        """search returns correctly parsed VectorSearchResult objects."""
        chunk1 = json.dumps(
            {
                "content": {"type": "text", "text": "Hello world!", "id": "x"},
                "source": "doc-1.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "metadata": {},
            },
        )
        chunk2 = json.dumps(
            {
                "content": {"type": "text", "text": "Goodbye!", "id": "y"},
                "source": "doc-2.txt",
                "chunk_index": 0,
                "total_chunks": 1,
                "metadata": {},
            },
        )

        search_response = [
            2,
            {
                b"kb-1:abc": {
                    b"__vector_score": b"0.0",
                    b"document_id": b"doc-1",
                    b"chunk": chunk1.encode(),
                    b"source": b"doc-1.txt",
                },
                b"kb-1:def": {
                    b"__vector_score": b"0.5",
                    b"document_id": b"doc-2",
                    b"chunk": chunk2.encode(),
                    b"source": b"doc-2.txt",
                },
            },
        ]

        self.mock_client.custom_command = AsyncMock(
            return_value=search_response,
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=2,
        )

        self.assertEqual(len(results), 2)
        self.assertAlmostEqual(results[0].score, 1.0)
        self.assertEqual(results[0].document_id, "doc-1")
        self.assertEqual(results[0].chunk.content.text, "Hello world!")
        self.assertAlmostEqual(results[1].score, 0.5)
        self.assertEqual(results[1].document_id, "doc-2")

    async def test_search_with_metadata_filter(self) -> None:
        """search passes metadata filter as a pre-filter expression."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[0, {}],
        )

        await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"document_id": "doc-1"},
        )

        call_args = self.mock_client.custom_command.call_args[0][0]
        query = call_args[2]
        self.assertIn("@document_id", query)
        self.assertIn("doc\\-1", query)

    async def test_search_rejects_unsupported_filter_key(self) -> None:
        """search raises ValueError for unsupported filter keys."""
        with self.assertRaises(ValueError) as ctx:
            await self.store.search(
                "kb-1",
                query_vector=[1.0, 0.0, 0.0],
                metadata_filter={"evil_key": "value"},
            )
        self.assertIn("Unsupported filter field", str(ctx.exception))

    async def test_search_empty_collection(self) -> None:
        """search on empty collection returns empty list."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[0, {}],
        )

        results = await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
        )

        self.assertEqual(results, [])

    async def test_list_documents(self) -> None:
        """list_documents aggregates records by document_id."""
        chunk1 = json.dumps(
            {
                "content": {"type": "text", "text": "A", "id": "a"},
                "source": "alpha.txt",
                "chunk_index": 0,
                "total_chunks": 2,
                "metadata": {"media_type": "text/plain"},
            },
        )
        chunk2 = json.dumps(
            {
                "content": {"type": "text", "text": "B", "id": "b"},
                "source": "alpha.txt",
                "chunk_index": 1,
                "total_chunks": 2,
                "metadata": {"media_type": "text/plain"},
            },
        )
        chunk3 = json.dumps(
            {
                "content": {"type": "text", "text": "C", "id": "c"},
                "source": "beta.md",
                "chunk_index": 0,
                "total_chunks": 1,
                "metadata": {"media_type": "text/markdown"},
            },
        )

        self.mock_client.custom_command = AsyncMock(
            return_value=[b"0", [b"kb-1:a", b"kb-1:b", b"kb-1:c"]],
        )

        self.mock_client.hgetall = AsyncMock(
            side_effect=[
                {
                    b"document_id": b"doc-1",
                    b"chunk": chunk1.encode(),
                    b"source": b"alpha.txt",
                },
                {
                    b"document_id": b"doc-1",
                    b"chunk": chunk2.encode(),
                    b"source": b"alpha.txt",
                },
                {
                    b"document_id": b"doc-2",
                    b"chunk": chunk3.encode(),
                    b"source": b"beta.md",
                },
            ],
        )

        summaries = await self.store.list_documents("kb-1")
        summaries_by_id = {s.document_id: s for s in summaries}

        self.assertEqual(set(summaries_by_id), {"doc-1", "doc-2"})
        self.assertEqual(summaries_by_id["doc-1"].chunk_count, 2)
        self.assertEqual(summaries_by_id["doc-1"].source, "alpha.txt")
        self.assertEqual(
            summaries_by_id["doc-1"].metadata,
            {"media_type": "text/plain"},
        )
        self.assertEqual(summaries_by_id["doc-2"].chunk_count, 1)
        self.assertEqual(summaries_by_id["doc-2"].source, "beta.md")

    async def test_context_manager_closes_client(self) -> None:
        """Exiting the context manager closes the client."""
        self.mock_client.close = AsyncMock()

        await self.store.__aexit__(None, None, None)

        self.mock_client.close.assert_called_once()

    async def test_get_client_raises_outside_context(self) -> None:
        """get_client raises RuntimeError when not in context."""
        store = ValkeyStore(host="localhost", port=6379)

        with self.assertRaises(RuntimeError):
            store.get_client()

    async def test_score_conversion_l2(self) -> None:
        """L2 metric returns raw distance (lower = more similar)."""
        store = ValkeyStore(host="localhost", port=6379, distance="L2")
        score = store._to_similarity_score(4.0)
        self.assertAlmostEqual(score, 4.0)

    async def test_score_conversion_cosine(self) -> None:
        """COSINE metric converts distance to 1-distance."""
        score = self.store._to_similarity_score(0.25)
        self.assertAlmostEqual(score, 0.75)

    async def test_escape_tag_value(self) -> None:
        """Tag values with special characters are properly escaped."""
        escaped = ValkeyStore._escape_tag_value("doc-1/file.txt")
        self.assertEqual(escaped, r"doc\-1\/file\.txt")

    async def test_escape_tag_value_pipe(self) -> None:
        """Pipe character (TAG SEPARATOR) is escaped."""
        escaped = ValkeyStore._escape_tag_value("a|b")
        self.assertEqual(escaped, r"a\|b")

    async def test_encode_vector(self) -> None:
        """Vectors are encoded as little-endian float32 bytes."""
        vector = [1.0, 0.0, -1.0]
        encoded = ValkeyStore._encode_vector(vector)
        self.assertEqual(len(encoded), 12)
        decoded = struct.unpack("<3f", encoded)
        self.assertAlmostEqual(decoded[0], 1.0)
        self.assertAlmostEqual(decoded[1], 0.0)
        self.assertAlmostEqual(decoded[2], -1.0)

    async def test_list_documents_with_filter(self) -> None:
        """list_documents with metadata_filter uses FT.SEARCH path."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[0, {}],
        )

        summaries = await self.store.list_documents(
            "kb-1",
            metadata_filter={"source": "alpha.txt"},
        )

        self.assertEqual(summaries, [])
        call = self.mock_client.custom_command.call_args[0][0]
        self.assertEqual(call[0], "FT.SEARCH")
        self.assertIn("@source", call[2])
        self.assertIn(r"alpha\.txt", call[2])

    async def test_search_with_source_filter(self) -> None:
        """search accepts 'source' as a filter key."""
        self.mock_client.custom_command = AsyncMock(
            return_value=[0, {}],
        )

        await self.store.search(
            "kb-1",
            query_vector=[1.0, 0.0, 0.0],
            top_k=5,
            metadata_filter={"source": "report.pdf"},
        )

        call_args = self.mock_client.custom_command.call_args[0][0]
        query = call_args[2]
        self.assertIn("@source", query)
        self.assertIn(r"report\.pdf", query)

    async def test_insert_partial_failure(self) -> None:
        """insert raises RuntimeError on partial hset failure."""
        self.mock_client.hset = AsyncMock(
            side_effect=[5, ConnectionError("lost connection"), 5],
        )

        records = [
            _make_record("A", [1.0, 0.0, 0.0], document_id="doc-1"),
            _make_record("B", [0.0, 1.0, 0.0], document_id="doc-2"),
            _make_record("C", [0.0, 0.0, 1.0], document_id="doc-3"),
        ]

        with self.assertRaises(RuntimeError) as ctx:
            await self.store.insert("kb-1", records)
        self.assertIn("1/3", str(ctx.exception))
        self.assertIn("hset calls failed", str(ctx.exception))

    async def test_escape_tag_value_rejects_control_chars(self) -> None:
        """Tag values with control characters raise ValueError."""
        with self.assertRaises(ValueError):
            ValkeyStore._escape_tag_value("doc\n-1")
        with self.assertRaises(ValueError):
            ValkeyStore._escape_tag_value("doc\t-1")
        with self.assertRaises(ValueError):
            ValkeyStore._escape_tag_value("\x00evil")

    async def test_invalid_collection_name_rejected(self) -> None:
        """Collection names with glob chars are rejected."""
        with self.assertRaises(ValueError):
            await self.store.has_collection("kb-*")
        with self.assertRaises(ValueError):
            await self.store.has_collection("kb[1]")
        with self.assertRaises(ValueError):
            await self.store.has_collection("*")

    async def test_repr_masks_credentials(self) -> None:
        """__repr__ masks credentials."""
        store = ValkeyStore(
            host="myhost",
            port=6380,
            credentials=("user", "secret"),
            use_tls=True,
        )
        r = repr(store)
        self.assertIn("***", r)
        self.assertNotIn("secret", r)
        self.assertIn("myhost", r)
