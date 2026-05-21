# -*- coding: utf-8 -*-
"""Integration test for ValkeyStore against a live Valkey instance.

Requires a Valkey server with the Search module on localhost:6379.
Run with::

    VALKEY_INTEGRATION_TEST=true pytest tests/valkey_store_integration_test.py
"""
import os
import unittest
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import ValkeyStore, Document, DocMetadata

_VALKEY_AVAILABLE = (
    os.environ.get("VALKEY_INTEGRATION_TEST", "").lower() == "true"
)


@unittest.skipUnless(_VALKEY_AVAILABLE, "Requires live Valkey server")
class ValkeyStoreIntegrationTest(IsolatedAsyncioTestCase):
    """Integration tests against a real Valkey instance."""

    async def asyncSetUp(self) -> None:
        """Create a store with a unique index for test isolation."""
        import uuid

        self.index_name = f"test_idx_{uuid.uuid4().hex[:8]}"
        self.prefix = f"test:doc:{self.index_name}:"
        self.store = ValkeyStore(
            host="localhost",
            port=6379,
            index_name=self.index_name,
            prefix=self.prefix,
            dimensions=3,
            distance="COSINE",
        )

    async def asyncTearDown(self) -> None:
        """Clean up: drop index and delete test keys."""
        try:
            await self.store.drop_index()
        except Exception:
            pass

        # Delete any remaining test keys
        client = self.store.get_client()
        # Scan for keys with our prefix and delete them
        cursor = "0"
        while True:
            result = await client.custom_command(
                ["SCAN", cursor, "MATCH", f"{self.prefix}*", "COUNT", "100"],
            )
            cursor = (
                result[0].decode()
                if isinstance(
                    result[0],
                    bytes,
                )
                else str(result[0])
            )
            keys = result[1]
            if keys:
                key_list = [
                    k.decode() if isinstance(k, bytes) else k for k in keys
                ]
                for key in key_list:
                    await client.delete([key])
            if cursor == "0":
                break

        await self.store.close()

    async def test_add_and_search(self) -> None:
        """Test adding documents and searching by vector similarity."""
        docs = [
            Document(
                embedding=[0.1, 0.2, 0.3],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="First test document.",
                    ),
                    doc_id="doc1",
                    chunk_id=0,
                    total_chunks=2,
                ),
            ),
            Document(
                embedding=[0.9, 0.1, 0.4],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="Second test document.",
                    ),
                    doc_id="doc1",
                    chunk_id=1,
                    total_chunks=2,
                ),
            ),
            Document(
                embedding=[0.5, 0.5, 0.5],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="Third test document.",
                    ),
                    doc_id="doc2",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        await self.store.add(docs)

        # Give Valkey a moment to index
        import asyncio

        await asyncio.sleep(0.5)

        # Search for something close to [0.1, 0.2, 0.3]
        results = await self.store.search(
            query_embedding=[0.15, 0.25, 0.35],
            limit=3,
        )

        self.assertGreater(len(results), 0)
        # The closest match should be the first document
        self.assertEqual(
            results[0].metadata.content["text"],
            "First test document.",
        )
        self.assertIsNotNone(results[0].score)
        # COSINE similarity should be high for a near-identical vector
        self.assertGreater(results[0].score, 0.9)

    async def test_search_with_threshold(self) -> None:
        """Test that score_threshold filters results."""
        docs = [
            Document(
                embedding=[1.0, 0.0, 0.0],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="Aligned document.",
                    ),
                    doc_id="aligned",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
            Document(
                embedding=[0.0, 1.0, 0.0],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="Orthogonal document.",
                    ),
                    doc_id="orthogonal",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        await self.store.add(docs)

        import asyncio

        await asyncio.sleep(0.5)

        # Search with a high threshold — only the aligned doc
        # should pass
        results = await self.store.search(
            query_embedding=[1.0, 0.0, 0.0],
            limit=10,
            score_threshold=0.9,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].metadata.content["text"],
            "Aligned document.",
        )

    async def test_delete_by_doc_id(self) -> None:
        """Test deleting documents by doc_id."""
        docs = [
            Document(
                embedding=[0.1, 0.2, 0.3],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="To be deleted.",
                    ),
                    doc_id="delete_me",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
            Document(
                embedding=[0.4, 0.5, 0.6],
                metadata=DocMetadata(
                    content=TextBlock(
                        type="text",
                        text="Should remain.",
                    ),
                    doc_id="keep_me",
                    chunk_id=0,
                    total_chunks=1,
                ),
            ),
        ]

        await self.store.add(docs)

        import asyncio

        await asyncio.sleep(0.5)

        # Delete one doc
        await self.store.delete(ids="delete_me")
        await asyncio.sleep(0.3)

        # Search should only find the remaining doc
        results = await self.store.search(
            query_embedding=[0.4, 0.5, 0.6],
            limit=10,
        )

        doc_ids = [r.metadata.doc_id for r in results]
        self.assertNotIn("delete_me", doc_ids)
        self.assertIn("keep_me", doc_ids)
