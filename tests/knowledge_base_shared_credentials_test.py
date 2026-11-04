# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Shared embedding credentials work through the real KB manager."""
import asyncio
import tempfile
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

from service_knowledge_base_upload_test import (
    _FakeVectorStore,
    _NoopWorkspaceManager,
    _make_bus,
    _make_storage,
)

from agentscope.app import create_app
from agentscope.app._service import ResourceAccessService
from agentscope.app.access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourceRef,
)
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import (
    CollectionPerKbManager,
    KnowledgeBaseNotFoundError,
)
from agentscope.app.storage import EmbeddingModelConfig
from agentscope.credential import OpenAICredential


class _SharingPolicy(ResourceAccessPolicyBase):
    """Revocable credential grant, plus an independent KB read grant."""

    def __init__(self) -> None:
        self.credential_shared = True
        self.knowledge_base_id: str | None = None

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: object,
    ) -> list[ResourceRef]:
        """Only the KB owner can use the provider's credential."""
        del storage
        if (
            viewer_id == "kb-owner"
            and kind == ResourceKind.CREDENTIAL
            and self.credential_shared
        ):
            return [
                ResourceRef(
                    kind=kind,
                    owner_id="provider-owner",
                    resource_id="shared-credential",
                ),
            ]
        if (
            viewer_id == "kb-reader"
            and kind == ResourceKind.KNOWLEDGE_BASE
            and self.knowledge_base_id is not None
        ):
            return [
                ResourceRef(
                    kind=kind,
                    owner_id="kb-owner",
                    resource_id=self.knowledge_base_id,
                ),
            ]
        return []


class KnowledgeBaseSharedCredentialsTest(IsolatedAsyncioTestCase):
    """Exercise selection, creation, runtime access, and revocation."""

    async def asyncSetUp(self) -> None:
        """Seed real owner-scoped Redis storage and build the full app."""
        # enterContext keeps the directory alive through asyncTearDown.
        # pylint: disable=consider-using-with
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        # pylint: enable=consider-using-with
        self._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._storage = _make_storage(self._redis)
        self._vectors = _FakeVectorStore()
        self._manager = CollectionPerKbManager(self._storage, self._vectors)
        self._policy = _SharingPolicy()
        self._config = EmbeddingModelConfig(
            type="openai_credential",
            credential_id="shared-credential",
            model="text-embedding-3-small",
            dimensions=1,
        )
        credential = OpenAICredential(
            id="shared-credential",
            name="Shared embeddings",
            api_key="test-shared-secret",
        )
        async with self._storage as storage:
            await storage.upsert_credential(
                "provider-owner",
                credential,
            )
            self._credential = await storage.get_credential(
                "provider-owner",
                "shared-credential",
            )
        self._app = create_app(
            storage=self._storage,
            message_bus=_make_bus(self._redis),
            workspace_manager=_NoopWorkspaceManager(),
            knowledge_base_manager=self._manager,
            blob_store=LocalBlobStore(root_dir=self._tmp),
            resource_access_policy=self._policy,
            enable_index_worker=False,
        )
        self._embedding = AsyncMock(
            return_value=SimpleNamespace(embeddings=[[0.0]]),
        )
        self._embedding.supports_multimodal = False
        self._builder_patch = patch(
            "agentscope.app.rag.knowledge_base_manager."
            "_collection_per_kb.build_embedding_model",
            return_value=self._embedding,
        )
        self._builder = self._builder_patch.start()

    async def asyncTearDown(self) -> None:
        """Release resources even if a regression assertion fails."""
        self._builder_patch.stop()
        await self._redis.aclose()

    def _create(self, client: TestClient, user_id: str = "kb-owner") -> str:
        """Create a KB through the public endpoint."""
        response = client.post(
            "/knowledge_bases",
            headers={"X-User-ID": user_id},
            json={
                "name": "Shared credential KB",
                "embedding_model_config": self._config.model_dump(),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["knowledge_base_id"]

    def _search(self, client: TestClient, kb_id: str, user_id: str) -> int:
        """Return the HTTP status of a real KB search."""
        response = client.post(
            f"/knowledge_bases/{kb_id}/search",
            headers={"X-User-ID": user_id},
            json={"query": "hello"},
        )
        return response.status_code

    async def test_selected_shared_credential_runs_and_is_named(self) -> None:
        """A credential offered in the picker must work in the runtime."""
        with TestClient(self._app) as client:
            headers = {"X-User-ID": "kb-owner"}
            models = client.get(
                "/knowledge_bases/embedding_models",
                headers=headers,
            )
            self.assertEqual(models.status_code, 200, models.text)
            self.assertIn("shared-credential", models.text)
            self.assertNotIn("test-shared-secret", models.text)
            kb_id = self._create(client)
            self.assertEqual(self._search(client, kb_id, "kb-owner"), 200)
            self.assertEqual(
                self._builder.call_args.kwargs["credential_record"],
                self._credential,
            )
            listing = client.get("/knowledge_bases", headers=headers)
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(
                listing.json()["knowledge_bases"][0]["credential_name"],
                "Shared embeddings",
            )
            self.assertNotIn("test-shared-secret", listing.text)

    async def test_revoked_credential_blocks_next_search(self) -> None:
        """Resolving each runtime must recheck the KB owner's grant."""
        with TestClient(self._app) as client:
            kb_id = self._create(client)
            self.assertEqual(self._search(client, kb_id, "kb-owner"), 200)
            self._policy.credential_shared = False
            self._builder.reset_mock()
            self.assertEqual(self._search(client, kb_id, "kb-owner"), 404)
            self._builder.assert_not_called()

    async def test_unshared_credential_rejected_before_allocating(
        self,
    ) -> None:
        """Forged or revoked selections leave no collection or KB record."""
        self._policy.credential_shared = False
        with TestClient(self._app) as client:
            response = client.post(
                "/knowledge_bases",
                headers={"X-User-ID": "kb-owner"},
                json={
                    "name": "Invalid selection",
                    "embedding_model_config": self._config.model_dump(),
                },
            )
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(self._vectors._collections, {})
            self.assertEqual(
                client.get(
                    "/knowledge_bases",
                    headers={"X-User-ID": "kb-owner"},
                ).json()["total"],
                0,
            )

    async def test_shared_kb_reader_uses_owner_grant(self) -> None:
        """KB read permission neither needs nor grants direct secret access."""
        with TestClient(self._app) as client:
            kb_id = self._create(client)
            self._policy.knowledge_base_id = kb_id
            self.assertEqual(self._search(client, kb_id, "kb-reader"), 200)
            self.assertEqual(self._search(client, kb_id, "outsider"), 404)
            self._policy.credential_shared = False
            self.assertEqual(self._search(client, kb_id, "kb-reader"), 404)

    async def test_owner_credential_remains_usable(self) -> None:
        """Default owner access continues to work without any policy grant."""
        with TestClient(self._app) as client:
            kb_id = self._create(client, "provider-owner")
            self.assertEqual(
                self._search(client, kb_id, "provider-owner"),
                200,
            )

    async def test_shared_credential_indexes_uploaded_document(self) -> None:
        """The embedded worker resolves the same grant when building a KB."""
        self._app.state.enable_index_worker = True
        with TestClient(self._app) as client:
            headers = {"X-User-ID": "kb-owner"}
            kb_id = self._create(client)
            response = client.post(
                f"/knowledge_bases/{kb_id}/documents",
                headers=headers,
                files={"file": ("hello.txt", b"hello world", "text/plain")},
            )
            self.assertEqual(response.status_code, 201, response.text)
            document_id = response.json()["document_id"]
            for _ in range(100):
                statuses = client.get(
                    f"/knowledge_bases/{kb_id}/documents/status",
                    params={"ids": document_id},
                    headers=headers,
                ).json()["items"]
                if statuses[0]["status"] in ("ready", "error"):
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(statuses[0]["status"], "ready", statuses)
            chunks = client.get(
                f"/knowledge_bases/{kb_id}/documents/{document_id}/chunks",
                headers=headers,
            )
            self.assertEqual(chunks.status_code, 200, chunks.text)
            self.assertIn("hello world", chunks.text)
            self.assertEqual(
                self._builder.call_args.kwargs["credential_record"],
                self._credential,
            )

    async def test_standalone_manager_defaults_to_owned_credentials(
        self,
    ) -> None:
        """Standalone owner access needs no application access service."""
        async with self._storage:
            own = await self._manager.create_knowledge_base(
                "provider-owner",
                "own",
                "",
                self._config,
            )
            await self._manager.get_knowledge("provider-owner", own.id)
            with self.assertRaises(KnowledgeBaseNotFoundError):
                await self._manager.create_knowledge_base(
                    "kb-owner",
                    "unshared",
                    "",
                    self._config,
                )

    async def test_standalone_manager_accepts_explicit_access_service(
        self,
    ) -> None:
        """Dedicated workers can opt into the deployment's sharing policy."""
        manager = CollectionPerKbManager(
            self._storage,
            self._vectors,
            resource_access_service=ResourceAccessService(
                self._storage,
                self._policy,
            ),
        )
        async with self._storage:
            record = await manager.create_knowledge_base(
                "kb-owner",
                "shared",
                "",
                self._config,
            )
            await manager.get_knowledge("kb-owner", record.id)
            self._policy.credential_shared = False
            with self.assertRaises(KnowledgeBaseNotFoundError):
                await manager.get_knowledge("kb-owner", record.id)
