# -*- coding: utf-8 -*-
"""Compare hosted Qdrant rankings with a single published collection."""
from importlib.util import find_spec
from typing import Any
from unittest import skipUnless
from unittest.mock import Mock, patch

from service_knowledge_base_update_test import _KnowledgeBaseServiceTestCase

from agentscope.app.rag.knowledge_base_manager._vector_slots import (
    version_knowledge,
)
from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    KnowledgeBase,
    QdrantStore,
    VectorRecord,
    VectorSearchResult,
)

if find_spec("qdrant_client"):
    from qdrant_client import AsyncQdrantClient, grpc
    from qdrant_client.async_qdrant_remote import AsyncQdrantRemote
    from qdrant_client.conversions.conversion import RestToGrpc
    from qdrant_client.http import models as qdrant_models


class QdrantFusionTest(_KnowledgeBaseServiceTestCase):
    """Exercise fusion through the knowledge base service."""

    @skipUnless(find_spec("qdrant_client"), "qdrant-client is required")
    async def test_qdrant_fusion_uses_published_candidates(self) -> None:
        """Match single-collection fusion across a partial rebuild."""
        owner, kb = "user-1", self._knowledge_base_id
        raw = await self._manager.get_knowledge(owner, kb)
        sources = [
            qdrant_models.Prefetch(query=[1.0], limit=4),
            qdrant_models.Prefetch(query=[2.0], limit=3),
        ]
        pages = []

        class FusionStore(QdrantStore):
            """Return native fusion scores and record the SDK page."""

            fusion_query = qdrant_models.FusionQuery(
                fusion=qdrant_models.Fusion.RRF,
            )

            offset = 0
            threshold: float | None = None
            root_filter: qdrant_models.Filter | grpc.Filter | None = None

            async def search(
                self,
                collection: str,
                query_vector: list,
                **options: Any,
            ) -> list:
                response = await self.get_client().query_points(
                    collection,
                    query=self.fusion_query,
                    query_filter=self.root_filter,
                    prefetch=sources,
                    limit=options.get("top_k", 2),
                    offset=self.offset,
                    score_threshold=self.threshold,
                    with_vectors=True,
                    with_payload=["chunk"],
                )
                pages.append(response.points)
                return [
                    VectorSearchResult(
                        document_id=p.payload["chunk"]["source"],
                        chunk=Chunk.model_validate(p.payload["chunk"]),
                        score=p.score,
                    )
                    for p in response.points
                ]

        class DirectKnowledge(KnowledgeBase):
            """Forward a logical search with an explicit or omitted limit."""

            async def search(self, queries: list, top_k: int = 5) -> list:
                options = {} if queries == ["omit"] else {"top_k": top_k}
                return await self.vector_store.search(
                    self.collection,
                    [1.0],
                    **options,
                )

        def row(i: int | str, score: int) -> VectorRecord:
            return VectorRecord(
                document_id=str(i),
                vector=[score],
                chunk=Chunk(
                    source=str(i),
                    content=TextBlock(text=str(score)),
                    chunk_index=0,
                    total_chunks=1,
                ),
            )

        async with FusionStore(location=":memory:", distance="Dot") as store:
            primary = DirectKnowledge(
                raw.name,
                raw.description,
                raw.embedding_model,
                store,
                raw.collection,
            )
            versions = version_knowledge(primary)
            await versions.ensure_collection()
            reference = "rrf-published-reference"
            await store.create_collection(reference, dimensions=1)
            rows = [
                row(i, v) for i, v in enumerate([7, 6, 5, 4, 11, 10, 3, 2])
            ]
            await store.insert(reference, rows)
            await store.insert(primary.collection, rows[:4])
            await store.insert(versions.collection, rows[4:])
            for i in range(8):
                await self._store_document(str(i), "ready")
                if i >= 4:
                    await self._storage.update_knowledge_document_status(
                        owner,
                        kb,
                        str(i),
                        "ready",
                        alternate_index=True,
                        migrate_vector_slot=True,
                    )

            async def compare(
                expected: list[str],
                reference_filter: qdrant_models.Filter | None = None,
            ) -> None:
                with patch.object(
                    store,
                    "root_filter",
                    reference_filter or store.root_filter,
                ):
                    native = await store.search(reference, [1.0], top_k=2)
                native_page = pages[-1]
                self.assertEqual([hit.document_id for hit in native], expected)
                for mode in ("explicit", "omit"):
                    pages.clear()
                    hits = await self._service.search(owner, kb, mode, 2)
                    self.assertEqual(
                        [hit.document_id for hit in hits],
                        expected,
                    )
                    self.assertEqual(hits, native)
                    self.assertEqual(pages, [native_page])

            with patch.object(
                self._manager,
                "get_knowledge",
                return_value=primary,
            ):
                queries: list[Any] = [store.fusion_query]
                if hasattr(qdrant_models, "RrfQuery"):
                    queries.append(
                        qdrant_models.RrfQuery(
                            rrf=qdrant_models.Rrf(k=2),
                        ),
                    )
                if hasattr(qdrant_models.Fusion, "DBSF"):
                    queries.append(
                        qdrant_models.FusionQuery(
                            fusion=qdrant_models.Fusion.DBSF,
                        ),
                    )
                for stage, stale in (
                    ("partial rebuild", []),
                    ("retired leaders", [row(i, i + 20) for i in range(4, 8)]),
                ):
                    if stale:
                        await store.insert(primary.collection, stale)
                    for query in queries:
                        with self.subTest(
                            stage=stage,
                            query=query,
                        ), patch.object(
                            store,
                            "fusion_query",
                            query,
                        ):
                            await compare(["4", "5"])
                cases: list[tuple[int, float | None, list[str]]] = [
                    (1, None, ["5", "0"]),
                    (1, 0.6, ["5"]),
                    (0, 1.1, []),
                ]
                for offset, threshold, expected in cases:
                    with self.subTest(offset=offset, threshold=threshold):
                        store.offset, store.threshold = offset, threshold
                        await compare(expected)
                store.offset, store.threshold = 0, None

                def exclude(key: str) -> qdrant_models.Filter:
                    return qdrant_models.Filter(
                        must_not=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=key),
                            ),
                        ],
                    )

                # Use a physically filtered reference even on local clients
                # that ignore the root filter on native fusion queries.
                await store.delete(reference, "4")
                store.root_filter = exclude("4")
                sources[0] = sources[0].model_copy(
                    update={"filter": exclude("5")},
                )
                with self.subTest(stage="root and branch filters"):
                    await compare(["0", "1"])
                rest_filter = qdrant_models.Filter(must=[exclude("4")])
                store.root_filter = RestToGrpc.convert_filter(rest_filter)
                with self.subTest(stage="nested protobuf fusion filter"):
                    await compare(["0", "1"], reference_filter=rest_filter)
                store.root_filter = None
                sources[0] = qdrant_models.Prefetch(query=[1.0], limit=4)
                await store.insert(reference, [rows[4]])
                # The second source now ranks the opposite end of the corpus.
                sources[1] = qdrant_models.Prefetch(query=[-1.0], limit=3)
                with self.subTest(stage="distinct branch order"):
                    await compare(["4", "7"])

                if hasattr(qdrant_models, "Rrf") and (
                    "weights" in qdrant_models.Rrf.model_fields
                ):
                    weighted = qdrant_models.RrfQuery(
                        rrf=qdrant_models.Rrf(k=7, weights=[1.0, 4.0]),
                    )
                    with self.subTest(
                        stage="weighted opposing ranks",
                    ), patch.object(
                        store,
                        "fusion_query",
                        weighted,
                    ):
                        await compare(["7", "6"])

                native_query = AsyncQdrantClient.query_points
                switched = False
                replacement = row(4, -20)
                await store.insert(reference, [replacement])

                async def switch_on_second_branch(
                    client: AsyncQdrantClient,
                    collection_name: str,
                    query: Any,
                    *args: Any,
                    **kwargs: Any,
                ) -> qdrant_models.QueryResponse:
                    nonlocal switched
                    if (
                        not switched
                        and collection_name != reference
                        and query == [-1.0]
                    ):
                        switched = True
                        await store.insert(primary.collection, [replacement])
                        await self._storage.update_knowledge_document_status(
                            owner,
                            kb,
                            "4",
                            "ready",
                            alternate_index=False,
                            migrate_vector_slot=True,
                        )
                    return await native_query(
                        client,
                        collection_name,
                        query,
                        *args,
                        **kwargs,
                    )

                with self.subTest(stage="publish during fusion"), patch.object(
                    AsyncQdrantClient,
                    "query_points",
                    switch_on_second_branch,
                ):
                    await compare(["5", "4"])
                    self.assertTrue(switched)

                with self.subTest(stage="coexisting physical point IDs"):
                    await self._store_document("legacy", "ready")
                    legacy = await self._storage.get_knowledge_document(
                        owner,
                        kb,
                        "legacy",
                    )
                    legacy.data.alternate_index = True
                    legacy.data.reindex_id = "legacy-batch"
                    await self._storage.upsert_knowledge_document(
                        owner,
                        legacy,
                    )
                    upload, sdk = row("legacy", 60), row("~legacy", 50)
                    await store.insert(reference, [upload, sdk])
                    await store.insert(
                        primary.collection,
                        [
                            upload.model_copy(
                                update={"document_id": "~legacy"},
                            ),
                        ],
                    )
                    await store.insert(versions.collection, [sdk])
                    sources[1] = qdrant_models.Prefetch(query=[2.0], limit=3)
                    native = await store.search(reference, [1.0], top_k=2)
                    hits = await self._service.search(owner, kb, "omit", 2)
                    self.assertEqual(
                        [h.document_id for h in hits],
                        ["legacy", "~legacy"],
                    )
                    self.assertEqual(hits, native)
                    first, second = pages[-1]
                    self.assertEqual(first.id, second.id)


class QdrantMmrTest(_KnowledgeBaseServiceTestCase):
    """Compare hosted diversification with a native collection."""

    @skipUnless(
        find_spec("qdrant_client") and hasattr(qdrant_models, "Mmr"),
        "Qdrant MMR is required",
    )
    async def test_mmr_uses_one_published_candidate_window(self) -> None:
        """Preserve selection order, original scores and SDK projection."""
        for distance in ("Dot", "Cosine", "Euclid", "Manhattan"):
            with self.subTest(distance=distance):
                await self._compare_mmr(distance)

    async def _compare_mmr(self, distance: str) -> None:
        """Run the same reads against native and partially rebuilt data."""
        owner, kb = "user-1", self._knowledge_base_id
        raw = await self._manager.get_knowledge(owner, kb)
        raw.embedding_model.dimensions = 2
        query = [1.0, 0.0]
        pages = []

        class MmrStore(QdrantStore):
            """Submit an MMR query through the shared Qdrant client."""

            options: dict[str, Any] = {}
            mmr = qdrant_models.Mmr(diversity=0.7, candidates_limit=4)

            async def search(
                self,
                collection: str,
                query_vector: list,
                top_k: int = 2,
                **_: Any,
            ) -> list:
                page = await self.get_client().query_points(
                    collection,
                    query=qdrant_models.NearestQuery(
                        nearest=query_vector,
                        mmr=self.mmr,
                    ),
                    limit=top_k,
                    with_payload=["chunk"],
                    **self.options,
                )
                pages.append(page.points)
                return [
                    VectorSearchResult(
                        document_id=p.payload["chunk"]["source"],
                        chunk=Chunk.model_validate(p.payload["chunk"]),
                        score=p.score,
                    )
                    for p in page.points
                ]

        class DirectKnowledge(KnowledgeBase):
            """Forward the caller's limit to the custom store."""

            async def search(self, queries: list, top_k: int = 5) -> list:
                options = {} if queries == ["omit"] else {"top_k": top_k}
                return await self.vector_store.search(
                    self.collection,
                    query,
                    **options,
                )

        rows = [
            VectorRecord(
                document_id=key,
                vector=vector,
                chunk=Chunk(
                    source=key,
                    content=TextBlock(text=key),
                    chunk_index=0,
                    total_chunks=1,
                ),
            )
            for key, vector in (
                ("lead", [1.0, 1.0]),
                ("near", [0.99, 1.1]),
                ("different", [0.8, -1.0]),
                ("other", [0.75, -0.8]),
            )
        ]
        async with MmrStore(location=":memory:", distance=distance) as store:
            primary = DirectKnowledge(
                raw.name,
                raw.description,
                raw.embedding_model,
                store,
                raw.collection,
            )
            versions = version_knowledge(primary)
            reference = "mmr-published-reference"
            await store.create_collection(reference, dimensions=2)
            await primary.ensure_collection()
            await store.insert(reference, rows)
            await store.insert(primary.collection, rows)
            for row in rows:
                await self._store_document(row.document_id, "ready")

            async def compare(
                top_k: int,
                expected: list[str] | None,
                mode: str = "explicit",
            ) -> None:
                native = await store.search(reference, query, top_k)
                native_page = pages[-1]
                if distance == "Dot" and expected is not None:
                    self.assertEqual([h.document_id for h in native], expected)
                pages.clear()
                actual = await self._service.search(owner, kb, mode, top_k)
                self.assertEqual(actual, native)
                self.assertEqual(pages, [native_page])

            with patch.object(
                self._manager,
                "get_knowledge",
                return_value=primary,
            ):
                with self.subTest(stage="original order"):
                    await compare(4, ["lead", "different", "other", "near"])
                await versions.ensure_collection()
                await store.insert(versions.collection, rows[:2])
                for row in rows[:2]:
                    await self._storage.update_knowledge_document_status(
                        owner,
                        kb,
                        row.document_id,
                        "ready",
                        alternate_index=True,
                        migrate_vector_slot=True,
                    )
                with self.subTest(stage="split across collections"):
                    await compare(2, ["lead", "different"])

                def exclude(key: str) -> qdrant_models.Filter:
                    return qdrant_models.Filter(
                        must_not=[
                            qdrant_models.FieldCondition(
                                key="document_id",
                                match=qdrant_models.MatchValue(value=key),
                            ),
                        ],
                    )

                branches = [
                    qdrant_models.Prefetch(
                        query=query,
                        filter=exclude("other"),
                        limit=2,
                    ),
                    qdrant_models.Prefetch(
                        query=[0.0, -1.0],
                        filter=exclude("different"),
                        limit=3,
                    ),
                ]
                store.options = {"prefetch": branches}
                for mode in ("explicit", "omit"):
                    with self.subTest(stage="filtered prefetch", mode=mode):
                        await compare(2, ["lead", "other"], mode)
                with self.subTest(stage="all prefetched candidates"):
                    await compare(4, ["lead", "other", "near"])
                store.options = {
                    "prefetch": [
                        qdrant_models.Prefetch(
                            query=qdrant_models.NearestQuery(
                                nearest=query,
                                mmr=store.mmr,
                            ),
                            prefetch=branches,
                            limit=2,
                        ),
                    ],
                }
                with self.subTest(stage="nested MMR"):
                    await compare(4, ["lead", "other"])
                store.options["prefetch"] = store.options["prefetch"][0]
                with self.subTest(stage="nested MMR object"):
                    native = await store.search(reference, query, 4)
                    actual = await self._service.search(owner, kb, "q", 4)
                    self.assertEqual(actual, native)
                # Serve the remote client's candidate reads from the fixture.
                client = store.get_client()
                # pylint: disable-next=protected-access
                remote = Mock(spec=AsyncQdrantRemote, wraps=client._client)
                for sources in (None, branches):
                    with self.subTest(
                        stage="remote MMR pagination",
                        prefetch=bool(sources),
                    ), patch.object(client, "_client", remote):
                        store.options = {"prefetch": sources}
                        first_three = await self._service.search(
                            owner,
                            kb,
                            "q",
                            3,
                        )
                        self.assertEqual(len(first_three), 3)
                        self.assertEqual(
                            first_three,
                            await store.search(reference, query, 3),
                        )
                        store.options["offset"] = 1
                        page = await self._service.search(owner, kb, "q", 2)
                        self.assertEqual(page, first_three[1:3])
                        with patch.object(
                            store,
                            "mmr",
                            store.mmr.model_copy(
                                update={"candidates_limit": 2},
                            ),
                        ):
                            store.options["offset"] = 2
                            self.assertEqual(
                                await self._service.search(owner, kb, "q", 2),
                                [],
                            )
                store.options = {"offset": 1, "with_vectors": False}
                with self.subTest(stage="offset"):
                    await compare(2, None)
                store.options = {"score_threshold": 0.9, "with_vectors": True}
                with self.subTest(stage="threshold and vectors"):
                    await compare(2, ["lead", "near"])
                store.options = {"with_vectors": []}
                with self.subTest(stage="empty vector projection"):
                    await compare(2, ["lead", "different"])
                store.options = {}
                store.mmr = qdrant_models.Mmr(diversity=0.7)
                with self.subTest(stage="default candidate count"):
                    await compare(2, ["lead", "near"])
