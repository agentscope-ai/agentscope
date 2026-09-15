# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Router tests for document chunk browsing and raw-file preview.

Boots the full FastAPI app via :func:`create_app` against fakeredis +
the in-memory KB fakes from the upload-flow test, seeds a knowledge
base, a ready document and its chunks directly through storage / the
fake vector store, then exercises the three endpoints added for
issue #2360:

* ``GET  /knowledge_bases/{kb}/documents/{doc}/chunks`` — ordered,
  stable ``page`` / ``page_size`` pagination, 404s, and the 501
  contract for vector stores without ``list_chunks``;
* ``POST /knowledge_bases/{kb}/documents/{doc}/download_token`` — the
  browser-native capability mint;
* ``GET  /knowledge_bases/{kb}/documents/{doc}`` — streamed raw bytes
  with inline-vs-attachment disposition and token / header auth.
"""
import io
import tempfile
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from service_knowledge_base_upload_test import (
    _FakeKbManager,
    _FakeVectorStore,
    _NoopWorkspaceManager,
    _make_bus,
    _make_storage,
)

from agentscope.app import create_app
from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.storage import (
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock
from agentscope.rag import Chunk
from agentscope.rag._vdb._vector_store import VectorRecord


class _NoChunkListingVectorStore(_FakeVectorStore):
    """Fake store that pretends chunk listing is unsupported."""

    async def list_chunks(
        self,
        collection: str,
        document_id: str,
        *,
        offset: int = 0,
        limit: int = 30,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list:
        """Refuse — models a backend predating ``list_chunks``."""
        raise NotImplementedError("no chunk listing here")


class _ShareToViewerPolicy(ResourceAccessPolicyBase):
    """Grant ``user-shared`` read access to one knowledge base.

    ``knowledge_base_id`` is filled in by the test setup once the
    seeded record's id is known; every other viewer gets nothing, so
    the foreign-viewer 404 tests keep their meaning.
    """

    def __init__(self) -> None:
        self.knowledge_base_id: str | None = None

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: object,
    ) -> list[ResourceRef]:
        """Return the single read grant for ``user-shared``."""
        del storage  # static grant — nothing to look up
        if (
            viewer_id == "user-shared"
            and kind == ResourceKind.KNOWLEDGE_BASE
            and self.knowledge_base_id is not None
        ):
            return [
                ResourceRef(
                    kind=ResourceKind.KNOWLEDGE_BASE,
                    owner_id="user-1",
                    resource_id=self.knowledge_base_id,
                    permission=ResourcePermission.READ,
                ),
            ]
        return []


class _KnowledgeBaseDetailTestBase(IsolatedAsyncioTestCase):
    """Shared app bootstrap + seed data for the detail endpoints."""

    vector_store_cls: type[_FakeVectorStore] = _FakeVectorStore

    async def asyncSetUp(self) -> None:
        """Boot the app and seed a KB, a ready document and chunks."""
        # pylint: disable=consider-using-with
        self._tmp = tempfile.TemporaryDirectory()
        self._fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._vector_store = self.vector_store_cls()
        storage = _make_storage(self._fr)
        message_bus = _make_bus(self._fr)
        self._blob_store = LocalBlobStore(root_dir=self._tmp.name)

        self._share_policy = _ShareToViewerPolicy()
        self._app = create_app(
            storage=storage,
            message_bus=message_bus,
            workspace_manager=_NoopWorkspaceManager(),
            knowledge_base_manager=_FakeKbManager(
                storage=storage,
                vector_store=self._vector_store,
            ),
            blob_store=self._blob_store,
            resource_access_policy=self._share_policy,
        )

        # Seed a knowledge base, a ready document (blob included) and
        # its chunks directly, bypassing the async indexing pipeline.
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
        collection = kb_record.data.collection_name
        await self._vector_store.create_collection(collection, 1)
        self._kb_id = kb_record.id
        self._share_policy.knowledge_base_id = kb_record.id

        self._file_bytes = b"# Hello\n\nchunked markdown body\n"
        async with self._blob_store as blob_store:
            blob_uri = await blob_store.write_stream(
                key=f"kb/{self._kb_id}/doc-1",
                stream=io.BytesIO(self._file_bytes),
            )
        document = KnowledgeDocumentRecord(
            id="doc-1",
            user_id="user-1",
            knowledge_base_id=self._kb_id,
            status="ready",
            data=KnowledgeDocumentData(
                filename="hello.md",
                size=len(self._file_bytes),
                content_type="text/markdown",
                blob_uri=blob_uri,
                chunk_count=5,
            ),
        )
        await self._vector_store.insert(
            collection,
            [
                VectorRecord(
                    vector=[0.0],
                    document_id="doc-1",
                    chunk=Chunk(
                        content=TextBlock(text=f"chunk-{index}"),
                        source="hello.md",
                        chunk_index=index,
                        total_chunks=5,
                    ),
                )
                # Insert out of order to prove ordering is restored.
                for index in (3, 0, 4, 1, 2)
            ],
        )

        storage._client = self._fr
        await storage.upsert_knowledge_base("user-1", kb_record)
        await storage.upsert_knowledge_document("user-1", document)
        await storage.upsert_credential(
            "user-1",
            OpenAICredential(
                id="cred-1",
                name="My OpenAI Key",
                api_key="sk-secret",
            ),
        )
        storage._client = None

    async def asyncTearDown(self) -> None:
        """Release fakeredis and the temporary blob directory."""
        await self._fr.aclose()
        self._tmp.cleanup()


class DocumentChunkBrowsingTest(_KnowledgeBaseDetailTestBase):
    """``GET .../documents/{doc}/chunks`` behaviour."""

    def test_pages_are_ordered_and_stable(self) -> None:
        """Pages come back chunk_index-ascending with a stable window."""
        headers = {"X-User-ID": "user-1"}
        with TestClient(self._app) as client:
            first = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                params={"page": 1, "page_size": 2},
                headers=headers,
            )
            self.assertEqual(first.status_code, 200)
            body = first.json()
            self.assertEqual(body["total"], 5)
            self.assertEqual(body["page"], 1)
            self.assertEqual(body["page_size"], 2)
            self.assertEqual(
                [c["chunk_index"] for c in body["chunks"]],
                [0, 1],
            )
            self.assertEqual(body["chunks"][0]["content"]["text"], "chunk-0")

            last = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                params={"page": 3, "page_size": 2},
                headers=headers,
            )
            self.assertEqual(
                [c["chunk_index"] for c in last.json()["chunks"]],
                [4],
            )

            past_end = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                params={"page": 4, "page_size": 2},
                headers=headers,
            )
            self.assertEqual(past_end.json()["chunks"], [])
            self.assertEqual(past_end.json()["total"], 5)

    def test_missing_document_and_foreign_viewer_are_404(self) -> None:
        """Unknown documents and invisible KBs both surface 404."""
        with TestClient(self._app) as client:
            missing = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/nope/chunks",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(missing.status_code, 404)

            foreign = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                headers={"X-User-ID": "user-2"},
            )
            self.assertEqual(foreign.status_code, 404)


class DocumentChunkBrowsingUnsupportedTest(_KnowledgeBaseDetailTestBase):
    """Vector stores without ``list_chunks`` surface HTTP 501."""

    vector_store_cls = _NoChunkListingVectorStore

    def test_not_implemented_maps_to_501(self) -> None:
        """A store without list_chunks maps to HTTP 501."""
        with TestClient(self._app) as client:
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 501)


class DocumentContentTest(_KnowledgeBaseDetailTestBase):
    """``GET .../documents/{doc}`` raw-file streaming behaviour."""

    def test_header_auth_streams_inline(self) -> None:
        """Header-authenticated fetch streams the bytes inline."""
        with TestClient(self._app) as client:
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, self._file_bytes)
            self.assertTrue(
                response.headers["content-type"].startswith(
                    "text/markdown",
                ),
            )
            self.assertTrue(
                response.headers["content-disposition"].startswith(
                    "inline;",
                ),
            )
            self.assertIn(
                "hello.md",
                response.headers["content-disposition"],
            )
            self.assertEqual(
                response.headers["content-length"],
                str(len(self._file_bytes)),
            )

    def test_content_length_is_measured_not_declared(self) -> None:
        """The header comes from the blob, so a wrong record cannot lie.

        A determinate download progress bar needs Content-Length, and a
        value that disagrees with the body truncates the response — so
        it is measured on the stored bytes, even when the record's
        declared size is stale or zero.
        """

        async def _corrupt_declared_size() -> None:
            storage = self._app.state.storage
            record = await storage.get_knowledge_document(
                "user-1",
                self._kb_id,
                "doc-1",
            )
            record.data.size = 0
            await storage.upsert_knowledge_document("user-1", record)

        with TestClient(self._app) as client:
            client.portal.call(_corrupt_declared_size)
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers["content-length"],
                str(len(self._file_bytes)),
            )
            self.assertEqual(response.content, self._file_bytes)

    def test_download_flag_forces_attachment(self) -> None:
        """``download=true`` switches the disposition to attachment."""
        with TestClient(self._app) as client:
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                params={"download": "true"},
                headers={"X-User-ID": "user-1"},
            )
            self.assertTrue(
                response.headers["content-disposition"].startswith(
                    "attachment;",
                ),
            )

    def test_scriptable_media_type_is_never_inline(self) -> None:
        """Scriptable media types are forced to attachment."""

        async def _seed_html_document() -> None:
            async with self._blob_store as blob_store:
                blob_uri = await blob_store.write_stream(
                    key=f"kb/{self._kb_id}/doc-html",
                    stream=io.BytesIO(b"<script>alert(1)</script>"),
                )
            # Lifespan has already bound the storage client.
            await self._app.state.storage.upsert_knowledge_document(
                "user-1",
                KnowledgeDocumentRecord(
                    id="doc-html",
                    user_id="user-1",
                    knowledge_base_id=self._kb_id,
                    status="ready",
                    data=KnowledgeDocumentData(
                        filename="page.html",
                        size=25,
                        content_type="text/html",
                        blob_uri=blob_uri,
                        chunk_count=0,
                    ),
                ),
            )

        with TestClient(self._app) as client:
            client.portal.call(_seed_html_document)
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-html",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                response.headers["content-disposition"].startswith(
                    "attachment;",
                ),
            )

    def test_raster_image_previews_inline(self) -> None:
        """Safe raster formats (PNG here) are served inline for <img>."""

        async def _seed_png_document() -> None:
            async with self._blob_store as blob_store:
                blob_uri = await blob_store.write_stream(
                    key=f"kb/{self._kb_id}/doc-png",
                    stream=io.BytesIO(b"\x89PNG fake bytes"),
                )
            await self._app.state.storage.upsert_knowledge_document(
                "user-1",
                KnowledgeDocumentRecord(
                    id="doc-png",
                    user_id="user-1",
                    knowledge_base_id=self._kb_id,
                    status="ready",
                    data=KnowledgeDocumentData(
                        filename="picture.png",
                        size=15,
                        content_type="image/png",
                        blob_uri=blob_uri,
                        chunk_count=0,
                    ),
                ),
            )

        with TestClient(self._app) as client:
            client.portal.call(_seed_png_document)
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-png",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                response.headers["content-disposition"].startswith("inline;"),
            )
            self.assertEqual(
                response.headers["x-content-type-options"],
                "nosniff",
            )

    def test_svg_is_never_inline(self) -> None:
        """SVG can carry script, so it must always be an attachment."""

        async def _seed_svg_document() -> None:
            async with self._blob_store as blob_store:
                blob_uri = await blob_store.write_stream(
                    key=f"kb/{self._kb_id}/doc-svg",
                    stream=io.BytesIO(b"<svg><script>alert(1)</script></svg>"),
                )
            await self._app.state.storage.upsert_knowledge_document(
                "user-1",
                KnowledgeDocumentRecord(
                    id="doc-svg",
                    user_id="user-1",
                    knowledge_base_id=self._kb_id,
                    status="ready",
                    data=KnowledgeDocumentData(
                        filename="image.svg",
                        size=37,
                        content_type="image/svg+xml",
                        blob_uri=blob_uri,
                        chunk_count=0,
                    ),
                ),
            )

        with TestClient(self._app) as client:
            client.portal.call(_seed_svg_document)
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-svg",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                response.headers["content-disposition"].startswith(
                    "attachment;",
                ),
            )
            self.assertEqual(
                response.headers["x-content-type-options"],
                "nosniff",
            )

    def test_missing_auth_is_401_and_foreign_viewer_404(self) -> None:
        """No credentials is 401; an invisible KB is 404."""
        with TestClient(self._app) as client:
            anonymous = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
            )
            self.assertEqual(anonymous.status_code, 401)

            foreign = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                headers={"X-User-ID": "user-2"},
            )
            self.assertEqual(foreign.status_code, 404)

    def test_missing_blob_is_404(self) -> None:
        """A record whose blob is gone yields 404, not a 500."""

        async def _seed_blobless_document() -> None:
            # Lifespan has already bound the storage client.
            await self._app.state.storage.upsert_knowledge_document(
                "user-1",
                KnowledgeDocumentRecord(
                    id="doc-gone",
                    user_id="user-1",
                    knowledge_base_id=self._kb_id,
                    status="ready",
                    data=KnowledgeDocumentData(
                        filename="gone.md",
                        size=1,
                        content_type="text/markdown",
                        blob_uri="local://kb/nowhere/doc-gone",
                        chunk_count=0,
                    ),
                ),
            )

        with TestClient(self._app) as client:
            client.portal.call(_seed_blobless_document)
            response = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-gone",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 404)


class DocumentDownloadTokenTest(_KnowledgeBaseDetailTestBase):
    """``POST .../download_token`` mint + token-authenticated fetch."""

    def test_token_round_trip(self) -> None:
        """A minted token fetches the file with no header at all."""
        with TestClient(self._app) as client:
            minted = client.post(
                f"/knowledge_bases/{self._kb_id}"
                "/documents/doc-1/download_token",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(minted.status_code, 200)
            token = minted.json()["token"]

            # The token alone fetches the file — no header needed.
            fetched = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                params={"token": token},
            )
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.content, self._file_bytes)

    def test_token_is_bound_to_one_document(self) -> None:
        """Tokens replayed against another document or garbage are 401."""
        with TestClient(self._app) as client:
            minted = client.post(
                f"/knowledge_bases/{self._kb_id}"
                "/documents/doc-1/download_token",
                headers={"X-User-ID": "user-1"},
            )
            token = minted.json()["token"]

            replayed = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-other",
                params={"token": token},
            )
            self.assertEqual(replayed.status_code, 401)

            garbage = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                params={"token": "not-a-token"},
            )
            self.assertEqual(garbage.status_code, 401)

    def test_mint_requires_visibility(self) -> None:
        """Minting is gated by the same 404 visibility rule."""
        with TestClient(self._app) as client:
            foreign = client.post(
                f"/knowledge_bases/{self._kb_id}"
                "/documents/doc-1/download_token",
                headers={"X-User-ID": "user-2"},
            )
            self.assertEqual(foreign.status_code, 404)


class KnowledgeBaseListEnrichmentTest(_KnowledgeBaseDetailTestBase):
    """``GET /knowledge_bases/`` filters, pagination and enrichment."""

    def test_list_serves_counts_and_credential_name(self) -> None:
        """The list carries counts and the resolved credential name."""
        with TestClient(self._app) as client:
            response = client.get(
                "/knowledge_bases/",
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["page"], 1)
            self.assertEqual(body["page_size"], 30)
            view = body["knowledge_bases"][0]
            self.assertEqual(view["document_count"], 1)
            self.assertEqual(view["chunk_count"], 5)
            self.assertEqual(view["credential_name"], "My OpenAI Key")
            self.assertEqual(view["status_counts"]["ready"], 1)
            # The masked credential secret must never ride along.
            self.assertNotIn("api_key", str(body))

    def test_id_filter_doubles_as_get_single(self) -> None:
        """``?id=`` narrows the list to one knowledge base."""
        with TestClient(self._app) as client:
            hit = client.get(
                "/knowledge_bases/",
                params={"id": self._kb_id},
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(hit.json()["total"], 1)

            miss = client.get(
                "/knowledge_bases/",
                params={"id": "kb-nope"},
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(miss.json()["total"], 0)
            self.assertEqual(miss.json()["knowledge_bases"], [])

    def test_name_filter_and_pagination_window(self) -> None:
        """Name filtering and page windows behave as documented."""
        with TestClient(self._app) as client:
            named = client.get(
                "/knowledge_bases/",
                params={"name": "KB"},  # case-insensitive substring
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(named.json()["total"], 1)

            beyond = client.get(
                "/knowledge_bases/",
                params={"page": 2, "page_size": 30},
                headers={"X-User-ID": "user-1"},
            )
            self.assertEqual(beyond.json()["knowledge_bases"], [])
            self.assertEqual(beyond.json()["total"], 1)


class KnowledgeDocumentListFilterTest(_KnowledgeBaseDetailTestBase):
    """``GET .../documents`` filters and pagination."""

    def test_filters_and_pagination(self) -> None:
        """Document filters, status validation and paging all work."""
        headers = {"X-User-ID": "user-1"}
        base = f"/knowledge_bases/{self._kb_id}/documents"
        with TestClient(self._app) as client:
            plain = client.get(base, headers=headers)
            body = plain.json()
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["page"], 1)
            self.assertEqual(body["page_size"], 30)
            self.assertEqual(body["documents"][0]["id"], "doc-1")

            by_id = client.get(base, params={"id": "doc-1"}, headers=headers)
            self.assertEqual(by_id.json()["total"], 1)

            by_kw = client.get(
                base,
                params={"keywords": "HELLO"},
                headers=headers,
            )
            self.assertEqual(by_kw.json()["total"], 1)

            kw_miss = client.get(
                base,
                params={"keywords": "nothing"},
                headers=headers,
            )
            self.assertEqual(kw_miss.json()["total"], 0)

            by_status = client.get(
                base,
                params={"status": "ready"},
                headers=headers,
            )
            self.assertEqual(by_status.json()["total"], 1)

            status_miss = client.get(
                base,
                params={"status": "error"},
                headers=headers,
            )
            self.assertEqual(status_miss.json()["total"], 0)

            bad_status = client.get(
                base,
                params={"status": "bogus"},
                headers=headers,
            )
            self.assertEqual(bad_status.status_code, 422)

            beyond = client.get(
                base,
                params={"page": 2},
                headers=headers,
            )
            self.assertEqual(beyond.json()["documents"], [])
            self.assertEqual(beyond.json()["total"], 1)


class SharedViewerAccessTest(_KnowledgeBaseDetailTestBase):
    """A read-only shared viewer can use every new read endpoint."""

    def test_shared_viewer_sees_enriched_list(self) -> None:
        """The shared KB lists with counts + the owner's credential name."""
        with TestClient(self._app) as client:
            response = client.get(
                "/knowledge_bases/",
                headers={"X-User-ID": "user-shared"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["total"], 1)
            view = body["knowledge_bases"][0]
            self.assertEqual(view["id"], self._kb_id)
            self.assertFalse(view["editable"])
            self.assertEqual(view["document_count"], 1)
            self.assertEqual(view["chunk_count"], 5)
            self.assertEqual(view["credential_name"], "My OpenAI Key")
            self.assertNotIn("api_key", str(body))

    def test_shared_viewer_lists_documents_and_chunks(self) -> None:
        """Documents and chunk pages resolve through the owner's data."""
        headers = {"X-User-ID": "user-shared"}
        with TestClient(self._app) as client:
            documents = client.get(
                f"/knowledge_bases/{self._kb_id}/documents",
                headers=headers,
            )
            self.assertEqual(documents.status_code, 200)
            self.assertEqual(documents.json()["total"], 1)

            chunks = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1/chunks",
                params={"page": 1, "page_size": 2},
                headers=headers,
            )
            self.assertEqual(chunks.status_code, 200)
            self.assertEqual(chunks.json()["total"], 5)
            self.assertEqual(
                [c["chunk_index"] for c in chunks.json()["chunks"]],
                [0, 1],
            )

    def test_shared_viewer_previews_and_mints_tokens(self) -> None:
        """Raw-file fetch works via header auth and via a minted token."""
        headers = {"X-User-ID": "user-shared"}
        with TestClient(self._app) as client:
            direct = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                headers=headers,
            )
            self.assertEqual(direct.status_code, 200)
            self.assertEqual(direct.content, self._file_bytes)

            minted = client.post(
                f"/knowledge_bases/{self._kb_id}"
                "/documents/doc-1/download_token",
                headers=headers,
            )
            self.assertEqual(minted.status_code, 200)
            fetched = client.get(
                f"/knowledge_bases/{self._kb_id}/documents/doc-1",
                params={"token": minted.json()["token"]},
            )
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.content, self._file_bytes)
