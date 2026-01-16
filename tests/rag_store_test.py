# -*- coding: utf-8 -*-
"""Test the RAG store implementations."""
import importlib
import importlib.abc
import sys
from unittest import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.rag import (
    QdrantStore,
    MilvusLiteStore,
    VDBStoreBase,
    Document,
    DocMetadata,
)


class RAGStoreTest(IsolatedAsyncioTestCase):
    """Test cases for RAG store implementations."""

    def test_rag_import_does_not_eagerly_import_pymilvus(self) -> None:
        """Importing agentscope.rag should not import pymilvus eagerly."""
        forbidden_prefixes = ("pymilvus",)

        saved = {}
        for name in list(sys.modules):
            if name == "agentscope.rag" or name.startswith("agentscope.rag."):
                saved[name] = sys.modules.pop(name)
            elif any(
                name == p or name.startswith(p + ".")
                for p in forbidden_prefixes
            ):
                saved[name] = sys.modules.pop(name)

        class _Blocker(importlib.abc.MetaPathFinder):
            # type: ignore[override]
            def find_spec(self, fullname, path, target=None):
                if any(
                    fullname == p or fullname.startswith(p + ".")
                    for p in forbidden_prefixes
                ):
                    raise AssertionError(
                        "agentscope.rag import attempted forbidden module: "
                        f"{fullname}",
                    )
                return None

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            importlib.import_module("agentscope.rag")
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)

    def test_milvuslite_store_exported(self) -> None:
        """MilvusLiteStore should be importable without optional deps."""
        self.assertTrue(issubclass(MilvusLiteStore, VDBStoreBase))

    async def test_qdrant_store(self) -> None:
        """Test the QdrantStore implementation."""
        store = QdrantStore(
            location=":memory:",
            collection_name="test",
            dimensions=3,
        )

        await store.add(
            [
                Document(
                    embedding=[0.1, 0.2, 0.3],
                    metadata=DocMetadata(
                        content=TextBlock(
                            type="text",
                            text="This is a test document.",
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
                            text="This is another test document.",
                        ),
                        doc_id="doc1",
                        chunk_id=1,
                        total_chunks=2,
                    ),
                ),
            ],
        )

        res = await store.search(
            query_embedding=[0.15, 0.25, 0.35],
            limit=3,
            score_threshold=0.8,
        )
        self.assertEqual(len(res), 1)
        # Allow small numeric drift up to ±0.1
        self.assertAlmostEqual(res[0].score, 0.9974149072579597, delta=0.1)
        self.assertEqual(
            res[0].metadata.content["text"],
            "This is a test document.",
        )
