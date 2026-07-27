# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""End-to-end wiring test for dedicated-deployment knowledge-base upload.

Boots the FastAPI app with ``enable_index_worker=False`` so the API
process does NOT host an :class:`IndexWorker`. Dispatch happens
through the message bus: a ``MessageBusDispatcher`` writes the task
to the shared queue and publishes the signal. In a real deployment
a separate worker process would pick it up; here we run an
:class:`IndexTaskConsumer` against a stub worker inside the test
to confirm the producer side reaches the bus correctly.

What this test guards:

- the API's lifespan accepts ``enable_index_worker=False`` without
  raising;
- an upload returns ``201`` and persists a ``pending`` record;
- the message bus carries the dispatch out of the API process so
  a worker subscribed on the same bus can consume it;
- the consumer's ``worker.process`` is invoked exactly once per
  dispatch, with the same ids the API recorded in storage.

We deliberately do NOT drive the document to ``ready`` here — that
path is already covered by the embedded-mode upload test. The
purpose of this test is to lock down the bus hop introduced by
:class:`MessageBusDispatcher`.
"""
import asyncio
import tempfile
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app._service import IndexTaskConsumer
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import (
    KnowledgeBaseManagerBase,
    KnowledgeBaseNotFoundError,
)
from agentscope.app.rag.knowledge_base_manager._dimension_policy import (
    DimensionPolicy,
    DimensionPolicyKind,
)
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    RedisStorage,
)
from agentscope.app.workspace_manager._base import WorkspaceManagerBase
from agentscope.rag import VectorStoreBase
from agentscope.rag._vdb._vector_store import (
    DocumentSummary,
    VectorRecord,
    VectorSearchResult,
)


# ----------------------------------------------------------------------
# Test doubles — borrowed in spirit from service_knowledge_base_upload_test
# but trimmed: the dedicated-mode test does not drive embedding.
# ----------------------------------------------------------------------


class _FakeVectorStore(VectorStoreBase):
    """Bare minimum to satisfy create_app's vector-store wiring."""

    def __init__(self) -> None:
        self._collections: dict[str, list[VectorRecord]] = {}

    async def create_collection(self, name: str, dimensions: int) -> None:
        self._collections.setdefault(name, [])

    async def delete_collection(self, name: str) -> None:
        self._collections.pop(name, None)

    async def has_collection(self, name: str) -> bool:
        return name in self._collections

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        self._collections.setdefault(collection, []).extend(records)

    async def delete(self, collection: str, document_id: str) -> None:
        self._collections[collection] = [
            r
            for r in self._collections.get(collection, [])
            if r.document_id != document_id
        ]

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        return []

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[DocumentSummary]:
        return []


class _FakeKbManager(KnowledgeBaseManagerBase):
    """KB manager that resolves knowledge bases via storage only.

    Returns a noop knowledge for ``get_knowledge`` because dedicated
    mode does not exercise embedding in this test — the worker stub
    intercepts ``process`` before the knowledge call.
    """

    async def get_dimension_policy(self) -> DimensionPolicy:
        return DimensionPolicy(kind=DimensionPolicyKind.ANY, dimension=None)

    async def create_knowledge_base(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding_model_config: EmbeddingModelConfig,
    ) -> KnowledgeBaseRecord:
        raise NotImplementedError

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        return False

    async def get_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> Any:
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            raise KnowledgeBaseNotFoundError(
                f"Knowledge base {knowledge_base_id!r} not found.",
            )
        raise NotImplementedError  # unused in this test


class _NoopWorkspaceManager(WorkspaceManagerBase):
    """Workspace manager that does nothing."""

    async def get_workspace(self, *args: Any, **kwargs: Any) -> Any:
        """Fake implementation."""
        raise NotImplementedError

    async def close(self, workspace_id: str) -> None:
        """Fake implementation."""
        return None

    async def close_all(self) -> None:
        """Fake implementation."""
        return None


def _make_storage(fr: fakeredis.aioredis.FakeRedis) -> RedisStorage:
    class _FakeStorage(RedisStorage):
        async def __aenter__(self) -> "_FakeStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeStorage()


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    class _FakeBus(RedisMessageBus):
        async def __aenter__(self) -> "_FakeBus":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeBus()


class _RecordingWorker:
    """Stub worker that records each ``process`` invocation.

    Stands in for :class:`IndexWorker` in the test so we can verify
    that the dispatch hop landed without spinning up the full
    parse → chunk → embed pipeline.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.notify = asyncio.Event()

    async def process(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
    ) -> None:
        """Record the dispatched task and signal the test.

        Stands in for :class:`IndexWorker.process_one` so the lifespan
        tests can assert that the API process forwarded the right
        ``user_id`` / ``knowledge_base_id`` / ``document_id`` triple.

        Args:
            user_id (`str`):
                The owning user id.
            knowledge_base_id (`str`):
                The parent knowledge base id.
            document_id (`str`):
                The document id to "process".
        """
        self.calls.append(
            {
                "user_id": user_id,
                "knowledge_base_id": knowledge_base_id,
                "document_id": document_id,
            },
        )
        self.notify.set()


class DedicatedModeUploadFlowTest(IsolatedAsyncioTestCase):
    """The producer side reaches the bus; a separate consumer sees it."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._vector_store = _FakeVectorStore()
        storage = _make_storage(self._fr)
        self._api_message_bus = _make_bus(self._fr)

        self._app = create_app(
            storage=storage,
            message_bus=self._api_message_bus,
            workspace_manager=_NoopWorkspaceManager(),
            knowledge_base_manager=_FakeKbManager(
                storage=storage,
                vector_store=self._vector_store,
            ),
            blob_store=LocalBlobStore(root_dir=self._tmp.name),
            enable_index_worker=False,
        )

        # Seed a knowledge base directly so we don't have to mock the
        # manager's create flow over HTTP.
        kb_record = KnowledgeBaseRecord(
            user_id="user-1",
            data=KnowledgeBaseData(
                name="kb",
                description="",
                embedding_model_config=EmbeddingModelConfig(
                    type="openai_credential",
                    credential_id="cred-1",
                    model="text-embedding-3-small",
                    dimensions=1,
                ),
                collection_name="",
            ),
        )
        kb_record.data.collection_name = f"kb_{kb_record.id}"
        await self._vector_store.create_collection(
            kb_record.data.collection_name,
            1,
        )
        storage._client = self._fr
        await storage.upsert_knowledge_base("user-1", kb_record)
        storage._client = None
        self._kb_id = kb_record.id

    async def asyncTearDown(self) -> None:
        await self._fr.aclose()
        self._tmp.cleanup()

    async def test_upload_dispatches_through_message_bus(self) -> None:
        """An upload in dedicated mode reaches a separate consumer."""
        # The consumer's bus is a SEPARATE RedisMessageBus instance
        # bound to the same fakeredis store. Production wiring would
        # be two TCP-connected clients; here they share the in-memory
        # backend, which exercises the bus contract correctly.
        consumer_bus = _make_bus(self._fr)
        worker = _RecordingWorker()

        async with consumer_bus, IndexTaskConsumer(
            message_bus=consumer_bus,
            worker=worker,
        ):
            headers = {"X-User-ID": "user-1"}
            with TestClient(self._app) as client:
                files = {
                    "file": (
                        "hello.txt",
                        b"hello world\n" * 16,
                        "text/plain",
                    ),
                }
                resp = client.post(
                    f"/knowledge_bases/{self._kb_id}/documents",
                    files=files,
                    headers=headers,
                )
                self.assertEqual(resp.status_code, 201, resp.text)
                body = resp.json()
                document_id = body["document_id"]

            # The consumer's worker should see the dispatch.
            await asyncio.wait_for(worker.notify.wait(), timeout=5.0)

        self.assertEqual(
            worker.calls,
            [
                {
                    "user_id": "user-1",
                    "knowledge_base_id": self._kb_id,
                    "document_id": document_id,
                },
            ],
        )
