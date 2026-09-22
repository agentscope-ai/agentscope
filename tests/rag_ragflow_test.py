# -*- coding: utf-8 -*-
"""Offline request/response tests for the RAGFlow knowledge backend."""

import json
import unittest
from email import policy
from email.parser import BytesParser
from typing import Any
from unittest.mock import patch

import httpx
from utils import AnyString

from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.rag import (
    RAGFlowConfig,
    RAGFlowKnowledgeBase,
)


class RAGFlowKnowledgeBaseTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the real async HTTP boundary through ``MockTransport``."""

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

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("httpx.AsyncClient", return_value=client):
            knowledge = RAGFlowKnowledgeBase(
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
            )
        try:
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
        finally:
            await knowledge.aclose()

        expected_request = {
            "dataset_ids": ["dataset-1"],
            "page": 1,
            "page_size": 3,
            "similarity_threshold": 0.25,
            "vector_similarity_weight": 0.6,
            "knn_top_k": 42,
            "keyword": True,
            "rerank_id": "reranker-1",
            "metadata_condition": {
                "logic": "and",
                "conditions": [
                    {
                        "name": "tenant",
                        "comparison_operator": "=",
                        "value": "acme",
                    },
                ],
            },
            "highlight": True,
        }
        self.assertListEqual(
            requests,
            [
                {"question": "policy", **expected_request},
                {"question": "leave", **expected_request},
            ],
        )
        self.assertListEqual(
            [result.model_dump() for result in results],
            [
                {
                    "score": 0.9,
                    "document_id": "doc-1",
                    "chunk": {
                        "content": {
                            "type": "text",
                            "text": "Four weeks of leave.",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        "source": "handbook.md",
                        "chunk_index": 0,
                        "total_chunks": 0,
                        "metadata": {
                            "ragflow_chunk_id": "chunk-1",
                            "vector_similarity": 0.8,
                            "term_similarity": 1.0,
                            "highlight": "Four <em>weeks</em> of leave.",
                            "positions": [1],
                        },
                    },
                },
            ],
        )

    async def test_document_lifecycle_maps_status_and_paginates_chunks(
        self,
    ) -> None:
        """Use documented routes for upload, parse, status, and deletion."""
        calls: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            content_type = request.headers.get("content-type", "")
            if "multipart/form-data" in content_type:
                message = BytesParser(policy=policy.default).parsebytes(
                    (
                        f"Content-Type: {content_type}\r\n"
                        "MIME-Version: 1.0\r\n\r\n"
                    ).encode()
                    + request.content,
                )
                part = next(message.iter_attachments())
                body: Any = {
                    "name": part.get_param(
                        "name",
                        header="content-disposition",
                    ),
                    "filename": part.get_filename(),
                    "content_type": part.get_content_type(),
                    "content": part.get_payload(decode=True),
                }
            elif request.content and "application/json" in content_type:
                body = json.loads(request.content)
            else:
                body = None
            calls.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.url.params),
                    "body": body,
                },
            )
            path = request.url.path
            if request.method == "POST" and path.endswith("/documents"):
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

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("httpx.AsyncClient", return_value=client):
            knowledge = RAGFlowKnowledgeBase(
                name="handbook",
                description="Company policies.",
                config=RAGFlowConfig(
                    api_key="secret",
                    base_url="https://ragflow.example",
                    dataset_id="dataset-1",
                ),
            )
        try:
            document_id = await knowledge.upload_document(
                b"%PDF",
                "manual.pdf",
                metadata={"tenant": "acme"},
            )
            summaries = await knowledge.list_documents()
            chunks = await knowledge.list_chunks(
                "doc-1",
                offset=1,
                limit=1,
            )
            await knowledge.delete_document("doc-1")
        finally:
            await knowledge.aclose()

        self.assertEqual(document_id, "doc-1")
        self.assertListEqual(
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
        self.assertListEqual(
            [chunk.model_dump() for chunk in chunks],
            [
                {
                    "content": {
                        "type": "text",
                        "text": "one",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                    "source": "manual.pdf",
                    "chunk_index": 1,
                    "total_chunks": 2,
                    "metadata": {
                        "ragflow_chunk_id": "chunk-1",
                        "available": True,
                    },
                },
            ],
        )
        self.assertListEqual(
            calls,
            [
                {
                    "method": "POST",
                    "path": "/api/v1/datasets/dataset-1/documents",
                    "query": {},
                    "body": {
                        "name": "file",
                        "filename": "manual.pdf",
                        "content_type": "application/octet-stream",
                        "content": b"%PDF",
                    },
                },
                {
                    "method": "PATCH",
                    "path": "/api/v1/datasets/dataset-1/documents/doc-1",
                    "query": {},
                    "body": {"meta_fields": {"tenant": "acme"}},
                },
                {
                    "method": "POST",
                    "path": "/api/v1/datasets/dataset-1/chunks",
                    "query": {},
                    "body": {"document_ids": ["doc-1"]},
                },
                {
                    "method": "GET",
                    "path": "/api/v1/datasets/dataset-1/documents",
                    "query": {"page": "1", "page_size": "100"},
                    "body": None,
                },
                {
                    "method": "GET",
                    "path": (
                        "/api/v1/datasets/dataset-1/documents/doc-1/chunks"
                    ),
                    "query": {"page": "1", "page_size": "2"},
                    "body": None,
                },
                {
                    "method": "DELETE",
                    "path": "/api/v1/datasets/dataset-1/documents",
                    "query": {},
                    "body": {"ids": ["doc-1"]},
                },
            ],
        )

    async def test_api_errors_and_client_ownership(self) -> None:
        """API errors are surfaced and the managed client closes."""

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 102, "message": "dataset is unavailable"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("httpx.AsyncClient", return_value=client):
            knowledge = RAGFlowKnowledgeBase(
                name="handbook",
                description="Company policies.",
                config=RAGFlowConfig(
                    api_key="secret",
                    base_url="https://ragflow.example",
                    dataset_id="dataset-1",
                ),
            )
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "dataset is unavailable",
            ):
                await knowledge.search(["query"])
        finally:
            await knowledge.aclose()
        self.assertTrue(client.is_closed)


if __name__ == "__main__":
    unittest.main()
