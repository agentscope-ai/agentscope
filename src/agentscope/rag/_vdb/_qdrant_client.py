# -*- coding: utf-8 -*-
"""Qdrant client used by the store, loaded with its optional dependency."""
from collections.abc import Callable
from contextvars import ContextVar
from functools import partial, wraps
from importlib.metadata import version
from inspect import BoundArguments, signature
from typing import Any, cast

import numpy as np
from packaging.version import Version
from qdrant_client import AsyncQdrantClient, grpc
from qdrant_client.conversions.conversion import GrpcToRest
from qdrant_client.http import models
from qdrant_client.local import local_collection
from qdrant_client.local.async_qdrant_local import AsyncQdrantLocal

from ._qdrant import QdrantStore
from ._vector_store import (
    _PublishPoints,
    _PublishWindow,
    _RankStages,
    _ReadHits,
    VectorSearchResult,
)
from .._document import Chunk


# qdrant-client 1.19.1 aligned local MMR pagination with the server.
_LEGACY_LOCAL_MMR = Version(version("qdrant-client")) < Version("1.19.1")
_QUERY_POINTS = signature(AsyncQdrantClient.query_points)
_published_points: ContextVar[
    tuple[QdrantStore, str, _PublishPoints] | None
] = ContextVar("published_qdrant_points", default=None)


async def read_published_points(
    store: QdrantStore,
    collection: str,
    read: _ReadHits,
    publish: _PublishPoints,
) -> tuple[list[VectorSearchResult], bool]:
    """Intercept dense candidate reads only within the custom search."""
    handled = False

    async def merge(rank: _RankStages, staged: bool) -> list:
        nonlocal handled
        handled = True
        return await publish(rank, staged)

    token = _published_points.set((store, collection, merge))
    try:
        hits = await read()
        return hits, handled
    finally:
        _published_points.reset(token)


class _PointHit(VectorSearchResult):
    """Keep the SDK point and its caller-selected payload and vectors."""

    point: models.ScoredPoint
    collection: str


def _prefetches(
    value: models.Prefetch | list[models.Prefetch] | None,
) -> list[models.Prefetch]:
    """Normalize the SDK's single and multiple prefetch forms."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class _QueryFilterConverter(GrpcToRest):
    """Keep absent protobuf should clauses optional at every nested level."""

    @classmethod
    def convert_filter(cls, model: grpc.Filter) -> models.Filter:
        """Preserve the semantics of an unset repeated field in local mode."""
        converted = super().convert_filter(model)
        if not model.should:
            converted.should = None
        return converted


_FusionScorer = Callable[
    [list[list[models.ScoredPoint]], int],
    list[models.ScoredPoint],
]


def _fusion_scorer(query: Any) -> _FusionScorer | None:
    """Resolve the SDK algorithm and parameters selected for this stage."""
    if isinstance(query, models.FusionQuery):
        if query.fusion == models.Fusion.RRF:
            return local_collection.reciprocal_rank_fusion
        if query.fusion == models.Fusion.DBSF:
            return local_collection.distribution_based_score_fusion
    elif isinstance(query, getattr(models, "RrfQuery", ())):
        # Older SDKs support k but lack the weights keyword.
        return partial(
            local_collection.reciprocal_rank_fusion,
            ranking_constant_k=query.rrf.k,
            **query.rrf.model_dump(include={"weights"}, exclude_none=True),
        )
    return None


def _fuse_points(
    scorer: _FusionScorer,
    branches: list[list[_PointHit]],
    limit: int,
    offset: int,
    threshold: float | None,
) -> list[_PointHit]:
    """Fuse global branch results without mixing coexisting physical slots."""
    identities = {}
    originals = []
    responses = []
    for branch in branches:
        points = []
        for hit in branch:
            key = (hit.collection, hit.point.id)
            if key not in identities:
                identities[key] = len(originals)
                originals.append(hit)
            # Qdrant accumulates by point ID and updates the scores in place.
            points.append(hit.point.model_copy(update={"id": identities[key]}))
        responses.append(points)

    fused = scorer(responses, offset + limit)
    results = []
    for point in fused[offset:]:
        if threshold is None or point.score >= threshold:
            original = originals[cast(int, point.id)]
            point.id = original.point.id
            results.append(
                original.model_copy(
                    update={"score": point.score, "point": point},
                ),
            )
    return results


def _select_mmr(
    hits: list[_PointHit],
    query: models.NearestQuery,
    distance: models.Distance,
    limit: int,
) -> list[_PointHit]:
    """Diversify one published window, preserving each point's raw score."""
    diversity = query.mmr.diversity
    weight = 1.0 - diversity if diversity is not None else 0.5
    if not 0 <= weight <= 1:
        raise ValueError("MMR lambda must be between 0.0 and 1.0")
    if len(hits) <= 1 or limit == 1:
        return hits[:limit]

    vectors = []
    for hit in hits:
        vector = hit.point.vector
        vectors.append(vector[""] if isinstance(vector, dict) else vector)
    query_vector = np.array(query.nearest)
    if query_vector.ndim == 1:
        dense = np.array(vectors)
        metric = local_collection.calculate_distance_core
        relevance = metric(query_vector, dense, distance)

        def similarities(index: int) -> np.ndarray:
            return metric(dense[index].copy(), dense, distance)

    else:
        multi = [np.array(v) for v in vectors]
        metric = local_collection.calculate_multi_distance_core
        relevance = metric(query_vector, multi, distance)

        def similarities(index: int) -> np.ndarray:
            # MaxSim is directional: score each candidate against the pick.
            return np.array(
                [metric(v, [multi[index]], distance)[0] for v in multi],
            )

    selected = [0]
    closest = np.full(len(hits), -np.inf)
    while len(selected) < min(limit, len(hits)):
        np.maximum(closest, similarities(selected[-1]), out=closest)
        scores = weight * relevance - (1.0 - weight) * closest
        scores[selected] = -np.inf
        best = int(np.argmax(scores))
        if np.isneginf(scores[best]):
            break
        selected.append(best)
    return [hits[i] for i in selected]


def _supported_stages(
    query: Any,
    using: str | None,
    prefetch: models.Prefetch | list[models.Prefetch] | None,
) -> bool:
    """Check every stage before handling any part of the native query."""
    if isinstance(query, models.NearestQuery):
        query = query.nearest
    return (
        not using
        and (
            isinstance(query, (list, np.ndarray))
            or (bool(prefetch) and _fusion_scorer(query) is not None)
        )
        and all(
            isinstance(stage, models.Prefetch)
            and (stage.limit is None or stage.limit > 0)
            and _supported_stages(
                stage.query,
                stage.using,
                stage.prefetch,
            )
            for stage in _prefetches(prefetch)
        )
    )


class _QdrantClient(AsyncQdrantClient):
    """Supply a published native result page to the backend's ranker."""

    @wraps(AsyncQdrantClient.query_points)
    async def query_points(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> models.QueryResponse:
        """Merge the selected collection's candidates before postprocessing."""
        read = super().query_points
        active = _published_points.get()
        if active is None:
            return await read(*args, **kwargs)
        store, collection, publish = active
        call = _QUERY_POINTS.bind(self, *args, **kwargs)
        call.apply_defaults()
        options = call.arguments
        if (
            store.get_client() is not self
            or options["collection_name"] != collection
        ):
            return await read(*args, **kwargs)
        if options["limit"] <= 0 or not _supported_stages(
            options["query"],
            options["using"],
            options["prefetch"],
        ):
            return await read(*args, **kwargs)

        if options["prefetch"] and isinstance(
            options["query_filter"],
            grpc.Filter,
        ):
            query = options["query"]
            if _fusion_scorer(query) is None and not getattr(
                query,
                "mmr",
                None,
            ):
                return await read(*args, **kwargs)
            options["query_filter"] = _QueryFilterConverter.convert_filter(
                options["query_filter"],
            )

        if options["prefetch"] and getattr(options["query"], "mmr", None):
            # Local Qdrant normalizes nested Prefetch objects and lists
            # differently. Use the same stage tree as its native query.
            resolve = getattr(self._client, "_resolve_prefetches_input", None)
            if resolve is not None:
                options["prefetch"] = resolve(options["prefetch"], collection)

        # pylint: disable-next=protected-access
        distance = (await store._vector_params(collection)).distance

        async def rank(window: _PublishWindow) -> list[VectorSearchResult]:
            return list(await self._rank_points(call, store, window, distance))

        hits = await publish(rank, bool(options["prefetch"]))
        points = []
        for hit in hits:
            point = cast(_PointHit, hit).point
            if (
                point.payload
                and "document_id" in point.payload
                and point.payload["document_id"] != hit.document_id
            ):
                point = point.model_copy(
                    update={
                        "payload": {
                            **point.payload,
                            "document_id": hit.document_id,
                        },
                    },
                )
            points.append(point)
        return models.QueryResponse(points=points)

    async def _rank_points(
        self,
        call: BoundArguments,
        store: QdrantStore,
        publish: _PublishWindow,
        distance: models.Distance,
        *,
        propagate_filter: bool = False,
    ) -> list[_PointHit]:
        """Rank a stage over the union of its published prefetch windows."""
        options = call.arguments
        sources = _prefetches(options["prefetch"])
        scorer = _fusion_scorer(options["query"])
        propagate_filter = propagate_filter or scorer is not None
        branches = []
        selected: dict[str, set[models.ExtendedPointId]] = {}
        for stage in sources:
            stage_filter = stage.filter
            if propagate_filter and options["query_filter"] is not None:
                filters = [
                    f for f in (options["query_filter"], stage_filter) if f
                ]
                stage_filter = models.Filter(must=filters)
            child = _QUERY_POINTS.bind(
                self,
                options["collection_name"],
                query=stage.query,
                prefetch=stage.prefetch,
                query_filter=stage_filter,
                with_payload=options["with_payload"]
                if scorer is not None
                else True,
                with_vectors=options["with_vectors"]
                if scorer is not None
                else False,
                search_params=stage.params,
                score_threshold=stage.score_threshold,
                lookup_from=stage.lookup_from,
                # The SDK defaults an omitted prefetch limit to ten.
                limit=stage.limit if stage.limit is not None else 10,
                consistency=options["consistency"],
                shard_key_selector=options["shard_key_selector"],
                timeout=options["timeout"],
            )
            child.apply_defaults()
            hits = await self._rank_points(
                child,
                store,
                publish,
                distance,
                propagate_filter=propagate_filter,
            )
            if scorer is not None:
                branches.append(hits)
            else:
                for hit in hits:
                    selected.setdefault(hit.collection, set()).add(
                        hit.point.id,
                    )

        offset = options["offset"] or 0
        if scorer is not None:
            return _fuse_points(
                scorer,
                branches,
                options["limit"],
                offset,
                options["score_threshold"],
            )

        query = options["query"]
        projection = options["with_vectors"]
        candidate_query, candidate_vectors = query, projection
        count = options["limit"]
        mmr = getattr(query, "mmr", None)
        if mmr is not None:
            # Fetch nearest published points first, with their ranking vector.
            candidate_query = query.model_copy(update={"mmr": None})
            if mmr.candidates_limit is not None:
                count = mmr.candidates_limit
            if projection is not True:
                candidate_vectors = list(
                    dict.fromkeys(["", *(projection or [])]),
                )

        sign = store._score_sign(distance)  # pylint: disable=protected-access
        read = super().query_points

        async def window(
            target: str,
            limit: int,
            scope: dict | None,
            omit: set[str],
        ) -> list[VectorSearchResult]:
            if sources and target not in selected:
                return []
            page = _QUERY_POINTS.bind(*call.args, **call.kwargs)
            page.arguments.update(
                collection_name=target,
                prefetch=None,
                query=candidate_query,
                with_vectors=candidate_vectors,
                limit=limit,
                offset=0,
            )
            # Preserve native filters, including should and must_not clauses.
            # pylint: disable-next=protected-access
            scoped = store._build_metadata_filter(scope)
            if scoped or omit or sources:
                constraints = [
                    f for f in (options["query_filter"], scoped) if f
                ]
                if sources:
                    constraints.append(
                        models.HasIdCondition(
                            has_id=list(selected[target]),
                        ),
                    )
                page.arguments["query_filter"] = models.Filter(
                    must=constraints,
                    must_not=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchAny(any=sorted(omit)),
                        ),
                    ]
                    if omit
                    else None,
                )
            response = await read(*page.args[1:], **page.kwargs)
            points = response.points
            payloads = {p.id: p.payload for p in points}
            if points and options["with_payload"] is not True:
                # Validate full chunks while preserving the native projection.
                records = await self.retrieve(
                    target,
                    ids=list(payloads),
                    with_payload=True,
                    consistency=options["consistency"],
                    shard_key_selector=options["shard_key_selector"],
                )
                payloads = {r.id: r.payload for r in records}
            return [
                _PointHit(
                    point=p,
                    collection=target,
                    score=sign * p.score,
                    document_id=payloads[p.id]["document_id"],
                    chunk=Chunk.model_validate(payloads[p.id]["chunk"]),
                )
                for p in points
                if payloads.get(p.id)
            ]

        candidate_offset = offset
        rank_offset = 0
        if mmr is not None and not (
            _LEGACY_LOCAL_MMR and isinstance(self._client, AsyncQdrantLocal)
        ):
            candidate_offset, rank_offset = 0, offset

        hits = (
            await publish(candidate_offset + count, window)
            if count > 0
            else []
        )
        candidates = cast(list[_PointHit], hits[candidate_offset:])
        if mmr is None:
            return candidates

        selected_hits = _select_mmr(
            candidates,
            query,
            distance,
            rank_offset + options["limit"],
        )[rank_offset:]
        for hit in selected_hits:
            if projection is False or projection is None:
                hit.point.vector = None
            elif projection is not True and "" not in projection:
                vector = hit.point.vector
                hit.point.vector = (
                    {k: v for k, v in vector.items() if k != ""}
                    if isinstance(vector, dict)
                    else {}
                )
        return selected_hits
