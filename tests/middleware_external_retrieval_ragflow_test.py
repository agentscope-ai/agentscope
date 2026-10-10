# -*- coding: utf-8 -*-
"""Unit tests for the :class:`RAGFlowRetrievalBackend` class."""
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import httpx

from agentscope.middleware._external_retrieval import (
    RAGFlowRetrievalBackend,
    RetrievalResult,
)


_BASE_URL = "http://test-ragflow:9380"


def _make_backend(
    handler: httpx.MockTransport,
) -> RAGFlowRetrievalBackend:
    """Build a backend whose httpx client uses a mock transport."""
    backend = RAGFlowRetrievalBackend.__new__(RAGFlowRetrievalBackend)
    backend._dataset_ids = ["dataset-1"]
    backend._client = httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    return backend


class RAGFlowRetrievalBackendTest(IsolatedAsyncioTestCase):
    """The test cases for the :class:`RAGFlowRetrievalBackend` class."""

    async def test_search_success(self) -> None:
        """Successful retrieval should normalize chunks into RetrievalResult."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "chunks": [
                            {
                                "content": "Docker is a container platform.",
                                "document_keyword": "docker-guide.md",
                                "similarity": 0.95,
                                "document_id": "doc-1",
                                "id": "chunk-1",
                            },
                            {
                                "content": "Use docker-compose for multi-container apps.",
                                "document_name": "compose-guide.md",
                                "similarity": 0.87,
                                "document_id": "doc-2",
                                "id": "chunk-2",
                            },
                        ],
                    },
                },
            )

        backend = _make_backend(handler)
        try:
            results = await backend.search("docker deployment", top_k=5)

            self.assertEqual(len(results), 2)
            self.assertIsInstance(results[0], RetrievalResult)
            self.assertEqual(results[0].content, "Docker is a container platform.")
            self.assertEqual(results[0].source, "docker-guide.md")
            self.assertEqual(results[0].score, 0.95)
            self.assertEqual(results[0].metadata["document_id"], "doc-1")

            # Should be sorted by descending score
            self.assertGreater(results[0].score, results[1].score)
        finally:
            await backend.close()

    async def test_search_empty_results(self) -> None:
        """Empty chunk list should return empty list."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 0, "data": {"chunks": []}},
            )

        backend = _make_backend(handler)
        try:
            results = await backend.search("nonexistent topic")
            self.assertEqual(results, [])
        finally:
            await backend.close()

    async def test_search_api_error(self) -> None:
        """API error code should return empty list."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"code": 401, "message": "unauthorized"},
            )

        backend = _make_backend(handler)
        try:
            results = await backend.search("test")
            self.assertEqual(results, [])
        finally:
            await backend.close()

    async def test_search_http_error(self) -> None:
        """HTTP error should return empty list."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        backend = _make_backend(handler)
        try:
            results = await backend.search("test")
            self.assertEqual(results, [])
        finally:
            await backend.close()

    async def test_search_sends_correct_payload(self) -> None:
        """Request payload should include query, dataset_ids, top_k, threshold."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"code": 0, "data": {"chunks": []}},
            )

        backend = _make_backend(handler)
        try:
            await backend.search(
                "test query",
                top_k=3,
                similarity_threshold=0.5,
            )

            self.assertEqual(len(captured), 1)
            payload = captured[0]
            self.assertEqual(payload["question"], "test query")
            self.assertEqual(payload["dataset_ids"], ["dataset-1"])
            self.assertEqual(payload["top_k"], 3)
            self.assertEqual(payload["similarity_threshold"], 0.5)
        finally:
            await backend.close()

    async def test_search_omits_threshold_when_none(self) -> None:
        """similarity_threshold should be omitted when None."""
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"code": 0, "data": {"chunks": []}},
            )

        backend = _make_backend(handler)
        try:
            await backend.search("test query", top_k=3)

            self.assertEqual(len(captured), 1)
            self.assertNotIn("similarity_threshold", captured[0])
        finally:
            await backend.close()

    async def test_auth_header(self) -> None:
        """Authorization header should be set correctly."""
        backend = _make_backend(lambda req: httpx.Response(200, json={"code": 0, "data": {"chunks": []}}))
        try:
            self.assertEqual(
                backend._client.headers["Authorization"],
                "Bearer test-key",
            )
        finally:
            await backend.close()
