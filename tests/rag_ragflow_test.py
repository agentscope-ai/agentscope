# -*- coding: utf-8 -*-
"""Offline request/response tests for the RAGFlow knowledge backend."""

import json
import unittest
from typing import Any

import httpx

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.rag import (
    KnowledgeBaseBase,
    RAGFlowConfig,
    RAGFlowError,
    RAGFlowKnowledgeBase,
)


class RAGFlowKnowledgeBaseTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the real async HTTP boundary through ``MockTransport``."""

    def setUp(self) -> None:
        """Initialize the optional client used by each test."""
        self.client: httpx.AsyncClient | None = None

    async def asyncTearDown(self) -> None:
        """Close clients created by individual tests."""
        if self.client is not None:
            await self.client.aclose()

    def _knowledge(self, handler: Any) -> RAGFlowKnowledgeBase:
        """Build a knowledge base backed by an offline transport."""
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        return RAGFlowKnowledgeBase(
            name="handbook",
            description="Company policies.",
            config=RAGFlowConfig(
                api_key="secret",
                base_url="https://ragflow.example",
                dataset_id="dataset-1",
                similarity_threshold=0.25,
                vector_similarity_weight=0.6,
                knn_top_k=42,
                rerank_id="reranker-1",
                keyword=True,
                metadata_condition={
                    "logic": "and",
                    "conditions": [
                        {
                            "name": "tenant",
                            "comparison_operator": "=",
                            "value": "acme",
                        },
                    ],
                },
            ),
            client=self.client,
        )

    async def test_search_uses_shared_contract_and_maps_http_results(
        self,
    ) -> None:
        """Fan out text queries, drop binary blocks, and merge duplicates."""
        requests: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/retrieval")
            self.assertEqual(request.headers["Authorization"], "Bearer secret")
            body = json.loads(request.content)
            requests.append(body)
            score = 0.7 if body["question"] == "policy" else 0.9
            chunks = [
                {
                    "id": "chunk-1",
                    "document_id": "doc-1",
                    "document_keyword": "handbook.md",
                    "content": "Four weeks of leave.",
                    "similarity": score,
                    "vector_similarity": 0.8,
                    "term_similarity": 1.0,
                    "highlight": "Four <em>weeks</em> of leave.",
                    "positions": [1],
                },
                {
                    "id": "weak",
                    "document_id": "doc-2",
                    "document_keyword": "notes.md",
                    "content": "Weak hit.",
                    "similarity": 0.2,
                },
            ]
            return httpx.Response(
                200,
                json={"code": 0, "data": {"chunks": chunks}},
            )

        knowledge = self._knowledge(handler)
        self.assertIsInstance(knowledge, KnowledgeBaseBase)
        results = await knowledge.search(
            [
                "policy",
                TextBlock(text="leave"),
                DataBlock(
                    source=Base64Source(
                        data="AA==",
                        media_type="image/png",
                    ),
                ),
            ],
            top_k=3,
            score_threshold=0.5,
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(
            {
                key: requests[0][key]
                for key in (
                    "dataset_ids",
                    "similarity_threshold",
                    "vector_similarity_weight",
                    "knn_top_k",
                    "rerank_id",
                    "keyword",
                    "metadata_condition",
                )
            },
            {
                "dataset_ids": ["dataset-1"],
                "similarity_threshold": 0.25,
                "vector_similarity_weight": 0.6,
                "knn_top_k": 42,
                "rerank_id": "reranker-1",
                "keyword": True,
                "metadata_condition": knowledge.config.metadata_condition,
            },
        )
        self.assertNotIn("top_k", requests[0])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].score, 0.9)
        self.assertEqual(results[0].document_id, "doc-1")
        self.assertEqual(results[0].chunk.source, "handbook.md")
        self.assertEqual(results[0].chunk.content.text, "Four weeks of leave.")
        self.assertEqual(
            results[0].chunk.metadata["ragflow_chunk_id"],
            "chunk-1",
        )
        self.assertEqual(results[0].chunk.metadata["vector_similarity"], 0.8)

    async def test_document_lifecycle_maps_status_and_paginates_chunks(
        self,
    ) -> None:
        """Use documented routes for upload, parse, status, and deletion."""
        calls: list[tuple[str, str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = (
                json.loads(request.content)
                if request.content
                and "application/json"
                in request.headers.get("content-type", "")
                else None
            )
            calls.append((request.method, request.url.path, body))
            path = request.url.path
            if request.method == "POST" and path.endswith("/documents"):
                self.assertIn(b'filename="manual.pdf"', request.content)
                return httpx.Response(
                    200,
                    json={"code": 0, "data": [{"id": "doc-1"}]},
                )
            if request.method == "GET" and path.endswith("/documents"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "docs": [
                                {
                                    "id": "doc-1",
                                    "name": "manual.pdf",
                                    "chunk_count": 2,
                                    "run": "DONE",
                                    "progress": 1.0,
                                    "progress_msg": "Task done",
                                    "size": 7,
                                    "token_count": 12,
                                    "meta_fields": {"tenant": "acme"},
                                },
                            ],
                        },
                    },
                )
            if request.method == "GET" and path.endswith("/chunks"):
                self.assertEqual(request.url.params["page"], "1")
                self.assertEqual(request.url.params["page_size"], "2")
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "chunks": [
                                {
                                    "id": "chunk-0",
                                    "content": "zero",
                                    "docnm_kwd": "manual.pdf",
                                    "available": True,
                                },
                                {
                                    "id": "chunk-1",
                                    "content": "one",
                                    "docnm_kwd": "manual.pdf",
                                    "available": True,
                                },
                            ],
                            "doc": {"name": "manual.pdf"},
                            "total": 2,
                        },
                    },
                )
            return httpx.Response(200, json={"code": 0, "data": None})

        knowledge = self._knowledge(handler)
        document_id = await knowledge.insert_document(
            b"%PDF",
            "manual.pdf",
            metadata={"tenant": "acme"},
        )
        summaries = await knowledge.list_documents()
        chunks = await knowledge.list_chunks("doc-1", offset=1, limit=1)
        await knowledge.delete_document("doc-1")

        self.assertEqual(document_id, "doc-1")
        self.assertEqual(
            [summary.model_dump() for summary in summaries],
            [
                {
                    "document_id": "doc-1",
                    "source": "manual.pdf",
                    "chunk_count": 2,
                    "metadata": {
                        "run": "DONE",
                        "progress": 1.0,
                        "progress_msg": "Task done",
                        "size": 7,
                        "token_count": 12,
                        "meta_fields": {"tenant": "acme"},
                    },
                },
            ],
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content.text, "one")
        self.assertEqual(chunks[0].chunk_index, 1)
        self.assertEqual(chunks[0].total_chunks, 2)
        self.assertEqual(
            calls[1:],
            [
                (
                    "PATCH",
                    "/api/v1/datasets/dataset-1/documents/doc-1",
                    {"meta_fields": {"tenant": "acme"}},
                ),
                (
                    "POST",
                    "/api/v1/datasets/dataset-1/chunks",
                    {"document_ids": ["doc-1"]},
                ),
                (
                    "GET",
                    "/api/v1/datasets/dataset-1/documents",
                    None,
                ),
                (
                    "GET",
                    "/api/v1/datasets/dataset-1/documents/doc-1/chunks",
                    None,
                ),
                (
                    "DELETE",
                    "/api/v1/datasets/dataset-1/documents",
                    {"ids": ["doc-1"]},
                ),
            ],
        )

    async def test_api_errors_and_client_ownership(self) -> None:
        """API errors are surfaced and caller-owned clients stay open."""

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 102, "message": "dataset is unavailable"},
            )

        knowledge = self._knowledge(handler)
        with self.assertRaisesRegex(
            RAGFlowError,
            "dataset is unavailable",
        ) as ctx:
            await knowledge.search(["query"])
        self.assertEqual(ctx.exception.code, 102)

        await knowledge.aclose()
        self.assertIsNotNone(self.client)
        self.assertFalse(self.client.is_closed)


if __name__ == "__main__":
    unittest.main()
