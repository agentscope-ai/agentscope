# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""End-to-end wiring test for the knowledge-base upload pipeline.

Boots the full FastAPI app via :func:`create_app` against fakeredis +
in-memory KB-side fakes, then drives the upload → status → list →
delete flow through ``TestClient``.  Verifies that:

* ``create_app`` wires the new ``blob_store`` / dispatcher / sweeper /
  service into ``app.state`` correctly;
* the upload endpoint streams bytes into the blob store, persists a
  ``pending`` record, and dispatches the worker;
* the in-process worker drives the record to ``ready`` through the
  parse → chunk → index phases;
* the status / list endpoints surface the lifecycle correctly;
* delete tears down the vector-store and storage records together.
"""
import asyncio
import tempfile
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
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
# Test doubles
# ----------------------------------------------------------------------


class _FakeVectorStore(VectorStoreBase):
    """In-memory vector store — records are kept by collection.

    The worker never reads back from it, so there is no need for real
    similarity math; we only have to honour the ``insert`` / ``delete``
    / ``has_collection`` contract.
    """

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
        bucket = self._collections.get(collection)
        if bucket is None:
            return
        self._collections[collection] = [
            r for r in bucket if r.document_id != document_id
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


class _FakeKnowledge:
    """Minimal stand-in for :class:`KnowledgeBase` used by the worker.

    Bypasses embedding-model construction — instead just funnels the
    chunks into the bound :class:`_FakeVectorStore` with a fixed
    zero-vector so ``insert_document`` succeeds end-to-end.
    """

    def __init__(
        self,
        vector_store: _FakeVectorStore,
        collection_name: str,
    ) -> None:
        self._vector_store = vector_store
        self._collection_name = collection_name

    async def insert_document(
        self,
        chunks: list,
        document_id: str | None = None,
        document_metadata: dict | None = None,
    ) -> str:
        """Pretend to embed and insert ``chunks`` into the bound store.

        The fake skips the real embedding step — it stamps a single
        scalar vector on every record so the upload pipeline can be
        exercised without an embedding model.

        Args:
            chunks (`list`):
                The parsed and chunked document content.
            document_id (`str | None`, optional):
                Caller-supplied document id; the fake just echoes it
                back rather than generating a UUID.
            document_metadata (`dict | None`, optional):
                Document-level metadata; ignored — the upload tests
                don't assert on metadata propagation.

        Returns:
            `str`:
                The (caller-supplied) document id, or ``""`` when
                none was passed.
        """
        del document_metadata  # unused — see docstring
        records = [
            VectorRecord(
                vector=[0.0],
                document_id=document_id or "",
                chunk=chunk,
            )
            for chunk in chunks
        ]
        await self._vector_store.insert(self._collection_name, records)
        return document_id or ""

    async def delete_document(self, document_id: str) -> None:
        """Remove every record for ``document_id`` from the bound store.

        Args:
            document_id (`str`):
                The document whose records should be deleted.
        """
        await self._vector_store.delete(self._collection_name, document_id)

    async def search(self, queries: list, top_k: int = 5) -> list:
        """Return an empty result list — search is out of scope here.

        Args:
            queries (`list`):
                The query inputs; ignored.
            top_k (`int`, defaults to ``5``):
                The maximum result count; ignored.

        Returns:
            `list`:
                Always empty — the upload tests do not exercise
                retrieval.
        """
        del queries, top_k  # unused — see docstring
        return []


class _FakeKbManager(KnowledgeBaseManagerBase):
    """KB manager that uses the storage + a fake vector store directly.

    Skips the real ``CollectionPerKbManager`` so we don't need a live
    embedding model — the indexing pipeline only requires
    ``insert_document`` / ``delete_document``, which the
    :class:`_FakeKnowledge` returned here implements directly.
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
        record = KnowledgeBaseRecord(
            user_id=user_id,
            name=name,
            description=description,
            embedding_model_config=embedding_model_config,
            collection_name="",
        )
        record.collection_name = f"kb_{record.id}"
        await self._vector_store.create_collection(
            name=record.collection_name,
            dimensions=embedding_model_config.dimensions,
        )
        return await self._storage.upsert_knowledge_base(user_id, record)

    async def delete_knowledge_base(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> bool:
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            return False
        await self._vector_store.delete_collection(record.collection_name)
        return await self._storage.delete_knowledge_base(
            user_id,
            knowledge_base_id,
        )

    async def get_knowledge(
        self,
        user_id: str,
        knowledge_base_id: str,
    ) -> _FakeKnowledge:
        record = await self._storage.get_knowledge_base(
            user_id,
            knowledge_base_id,
        )
        if record is None:
            raise KnowledgeBaseNotFoundError(
                f"Knowledge base {knowledge_base_id!r} not found.",
            )
        return _FakeKnowledge(
            vector_store=self._vector_store,
            collection_name=record.collection_name,
        )


class _NoopWorkspaceManager(WorkspaceManagerBase):
    """Workspace manager that does nothing — the KB pipeline never
    touches it, but ``create_app`` requires one to be wired in."""

    async def get_workspace(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def create_workspace(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def close(self, workspace_id: str) -> None:
        return None

    async def close_all(self) -> None:
        return None


def _make_storage(fr: fakeredis.aioredis.FakeRedis) -> RedisStorage:
    """Build a RedisStorage already bound to *fr*.

    Pre-populates ``_client`` so the lifespan's ``__aenter__`` no-ops on
    the connection-pool side and just reuses our fakeredis handle.
    """

    class _FakeStorage(RedisStorage):
        async def __aenter__(self) -> "_FakeStorage":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeStorage()


def _make_bus(fr: fakeredis.aioredis.FakeRedis) -> RedisMessageBus:
    """Build a RedisMessageBus bound to *fr* (same trick as in the bus
    tests)."""

    class _FakeBus(RedisMessageBus):
        async def __aenter__(self) -> "_FakeBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _FakeBus()


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


class KnowledgeBaseUploadFlowTest(IsolatedAsyncioTestCase):
    """End-to-end wiring of the upload pipeline through ``TestClient``."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._vector_store = _FakeVectorStore()
        storage = _make_storage(self._fr)
        message_bus = _make_bus(self._fr)

        self._app = create_app(
            storage=storage,
            message_bus=message_bus,
            workspace_manager=_NoopWorkspaceManager(),
            knowledge_base_manager=_FakeKbManager(
                storage=storage,
                vector_store=self._vector_store,
            ),
            blob_store=LocalBlobStore(root_dir=self._tmp.name),
        )
        # Seed a knowledge base directly through storage so we don't
        # have to mock the manager's create flow over HTTP.
        kb_record = KnowledgeBaseRecord(
            user_id="user-1",
            name="kb",
            description="",
            embedding_model_config=EmbeddingModelConfig(
                type="openai_credential",
                credential_id="cred-1",
                model="text-embedding-3-small",
                dimensions=1,
            ),
            collection_name="",
        )
        kb_record.collection_name = f"kb_{kb_record.id}"
        await self._vector_store.create_collection(
            kb_record.collection_name,
            1,
        )

        # Drop the seed record into fakeredis directly — we need the KB
        # in place before lifespan starts the sweeper.
        storage._client = self._fr
        await storage.upsert_knowledge_base("user-1", kb_record)
        storage._client = None
        self._kb_id = kb_record.id

    async def asyncTearDown(self) -> None:
        await self._fr.aclose()
        self._tmp.cleanup()

    async def test_upload_drives_document_to_ready(self) -> None:
        """Upload a small text file and observe the lifecycle."""
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
            self.assertEqual(body["filename"], "hello.txt")
            self.assertIn(body["status"], ("pending", "ready"))

            # Wait for the in-process worker to drive the record to
            # ``ready``.  We poll with a generous overall timeout — the
            # actual work is < 100 ms but CI machines can be slow.
            deadline = 5.0
            poll = 0.05
            elapsed = 0.0
            final_status = body["status"]
            while elapsed < deadline:
                resp = client.get(
                    f"/knowledge_bases/{self._kb_id}/documents/status",
                    params={"ids": document_id},
                    headers=headers,
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                items = resp.json()["items"]
                self.assertEqual(len(items), 1, items)
                final_status = items[0]["status"]
                if final_status in ("ready", "error"):
                    break
                await asyncio.sleep(poll)
                elapsed += poll
            self.assertEqual(final_status, "ready", items)

            # Listing shows the document with the same ``ready`` state.
            resp = client.get(
                f"/knowledge_bases/{self._kb_id}/documents",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            documents = resp.json()["documents"]
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["id"], document_id)
            self.assertEqual(documents[0]["status"], "ready")
            self.assertGreaterEqual(documents[0]["chunk_count"], 1)

            # Delete and confirm the listing comes back empty.
            resp = client.delete(
                f"/knowledge_bases/{self._kb_id}/documents/{document_id}",
                headers=headers,
            )
            self.assertEqual(resp.status_code, 204, resp.text)
            resp = client.get(
                f"/knowledge_bases/{self._kb_id}/documents",
                headers=headers,
            )
            self.assertEqual(resp.json()["documents"], [])

    async def test_status_for_unknown_id_is_silently_skipped(self) -> None:
        """Asking for a non-existent doc returns an empty items list."""
        headers = {"X-User-ID": "user-1"}
        with TestClient(self._app) as client:
            resp = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/status",
                params={"ids": "does-not-exist"},
                headers=headers,
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["items"], [])
