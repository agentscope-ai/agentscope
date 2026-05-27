# -*- coding: utf-8 -*-
"""The Valkey vector store implementation using Valkey Search module.

This implementation provides a vector database store using Valkey's Search
module with HNSW indexing for vector similarity search. It uses the
valkey-glide client library for async communication with Valkey.
"""
import json
import struct
import asyncio
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata
from ..._utils._common import _map_text_to_uuid
from ..._logging import logger
from ...types import Embedding

if TYPE_CHECKING:
    from glide import GlideClient, GlideClusterClient
else:
    GlideClient = "glide.GlideClient"
    GlideClusterClient = "glide.GlideClusterClient"


def _float_list_to_bytes(floats: list[float]) -> bytes:
    """Convert a list of floats to a binary blob (FLOAT32 little-endian)."""
    return struct.pack(f"<{len(floats)}f", *floats)


def _escape_tag_value(value: str) -> str:
    """Escape special characters for Valkey Search tag queries."""
    special = r'\{}|@$"' + "'"
    return "".join(f"\\{c}" if c in special else c for c in value)


class ValkeyStore(VDBStoreBase):
    """Valkey vector store using the Valkey Search module with HNSW indexing.

    This class provides a vector database store implementation using Valkey's
    Search module for HNSW-based vector similarity search. Documents are
    stored as Valkey Hash keys with vector embeddings and JSON-serialized
    metadata.

    .. note:: Requires a Valkey instance with the Search module loaded.

    Example:
        .. code-block:: python

            store = ValkeyStore(
                host="localhost",
                port=6379,
                index_name="my_index",
                dimensions=768,
                distance="COSINE",
            )
            await store.add(documents)
            results = await store.search(query_embedding, limit=5)
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        index_name: str = "agentscope_idx",
        prefix: str = "agentscope:doc:",
        dimensions: int = 768,
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        use_tls: bool = False,
        use_cluster: bool = False,
        hnsw_m: int | None = None,
        hnsw_ef_construction: int | None = None,
        hnsw_ef_runtime: int | None = None,
        initial_cap: int | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Valkey vector store.

        Args:
            host (`str`, defaults to "localhost"):
                The Valkey server host.
            port (`int`, defaults to 6379):
                The Valkey server port.
            index_name (`str`, defaults to "agentscope_idx"):
                The name of the search index to create/use.
            prefix (`str`, defaults to "agentscope:doc:"):
                The key prefix for stored documents. The index will only
                cover keys matching this prefix.
            dimensions (`int`, defaults to 768):
                The dimension of the embedding vectors.
            distance (`Literal["COSINE", "L2", "IP"]`, defaults to "COSINE"):
                The distance metric for vector similarity. Can be one of
                "COSINE" (cosine similarity), "L2" (Euclidean distance),
                or "IP" (inner product).
            use_tls (`bool`, defaults to False):
                Whether to use TLS for the connection.
            use_cluster (`bool`, defaults to False):
                Whether to connect to a Valkey cluster. If True, uses
                GlideClusterClient instead of GlideClient.
            hnsw_m (`int | None`, optional):
                The number of maximum edges per node in the HNSW graph.
                Higher values improve recall but increase memory usage.
            hnsw_ef_construction (`int | None`, optional):
                The number of vectors examined during index construction.
                Higher values improve recall but slow down indexing.
            hnsw_ef_runtime (`int | None`, optional):
                The number of vectors examined during search.
                Higher values improve recall but slow down queries.
            initial_cap (`int | None`, optional):
                Initial capacity for the number of vectors in the index.
            client_kwargs (`dict[str, Any] | None`, optional):
                Additional keyword arguments for the Glide client
                configuration.

        Raises:
            ImportError: If valkey-glide is not installed.
        """
        try:
            from glide import GlideClient, GlideClusterClient
        except ImportError as e:
            raise ImportError(
                "valkey-glide is not installed. Please install it with "
                "`pip install valkey-glide`.",
            ) from e

        self.index_name = index_name
        self.prefix = prefix
        self.dimensions = dimensions
        self.distance = distance
        self.hnsw_m = hnsw_m
        self.hnsw_ef_construction = hnsw_ef_construction
        self.hnsw_ef_runtime = hnsw_ef_runtime
        self.initial_cap = initial_cap
        self._use_cluster = use_cluster

        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._client_kwargs = client_kwargs or {}

        self._client: GlideClient | GlideClusterClient | None = None
        self._client_lock = asyncio.Lock()
        self._index_created = False

    async def _get_client(
        self,
    ) -> "GlideClient | GlideClusterClient":
        """Get or create the Glide client connection."""
        if self._client is not None:
            return self._client

        async with self._client_lock:
            # Double-check after acquiring lock
            if self._client is not None:
                return self._client

            from glide import (
                GlideClient,
                GlideClusterClient,
                GlideClientConfiguration,
                GlideClusterClientConfiguration,
                NodeAddress,
            )

            address = NodeAddress(host=self._host, port=self._port)

            if self._use_cluster:
                config = GlideClusterClientConfiguration(
                    addresses=[address],
                    use_tls=self._use_tls,
                    client_name="agentscope_rag_store_client",
                    **self._client_kwargs,
                )
                self._client = await GlideClusterClient.create(
                    config,
                )
            else:
                config = GlideClientConfiguration(
                    addresses=[address],
                    use_tls=self._use_tls,
                    client_name="agentscope_rag_store_client",
                    **self._client_kwargs,
                )
                self._client = await GlideClient.create(config)

            return self._client

    async def _ensure_index(self) -> None:
        """Ensure the search index exists, creating it if necessary."""
        if self._index_created:
            return

        from glide import ft

        # NOTE: glide_shared is an internal module path in valkey-glide.
        # The version constraint (<3.0.0) guards against breaking changes.
        from glide_shared.commands.server_modules.ft_options import (
            ft_create_options,
        )

        FtCreateOptions = ft_create_options.FtCreateOptions
        DataType = ft_create_options.DataType
        VectorField = ft_create_options.VectorField
        VectorAlgorithm = ft_create_options.VectorAlgorithm
        VectorFieldAttributesHnsw = ft_create_options.VectorFieldAttributesHnsw
        DistanceMetricType = ft_create_options.DistanceMetricType
        VectorType = ft_create_options.VectorType
        TagField = ft_create_options.TagField
        NumericField = ft_create_options.NumericField

        client = await self._get_client()

        # Check if index already exists
        try:
            existing_indices = await ft.list(client)
            index_names = [
                idx.decode() if isinstance(idx, bytes) else idx
                for idx in existing_indices
            ]
            if self.index_name in index_names:
                self._index_created = True
                return
        except Exception as e:
            err_msg = str(e).lower()
            if "unknown command" in err_msg or "no such module" in err_msg:
                logger.warning(
                    "FT._LIST unavailable, attempting FT.CREATE: %s",
                    e,
                )
            else:
                raise

        # Build HNSW vector field attributes
        hnsw_attrs = VectorFieldAttributesHnsw(
            dimensions=self.dimensions,
            distance_metric=getattr(
                DistanceMetricType,
                self.distance,
            ),
            type=VectorType.FLOAT32,
            initial_cap=self.initial_cap,
            number_of_edges=self.hnsw_m,
            vectors_examined_on_construction=self.hnsw_ef_construction,
            vectors_examined_on_runtime=self.hnsw_ef_runtime,
        )

        # Define the schema: vector field + metadata fields for filtering
        schema = [
            VectorField(
                name="vector",
                algorithm=VectorAlgorithm.HNSW,
                attributes=hnsw_attrs,
            ),
            TagField("doc_id"),
            NumericField("chunk_id"),
        ]

        options = FtCreateOptions(
            data_type=DataType.HASH,
            prefixes=[self.prefix],
        )

        await ft.create(
            client,
            self.index_name,
            schema=schema,
            options=options,
        )
        self._index_created = True

    def _make_key(self, document: Document) -> str:
        """Generate a deterministic Valkey key for a document."""
        unique_id = _map_text_to_uuid(
            json.dumps(
                {
                    "doc_id": document.metadata.doc_id,
                    "chunk_id": document.metadata.chunk_id,
                    "content": document.metadata.content,
                },
                ensure_ascii=False,
            ),
        )
        return f"{self.prefix}{unique_id}"

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add documents with embeddings to the Valkey vector store.

        Each document is stored as a Valkey Hash with the following fields:
        - ``vector``: The embedding as a binary FLOAT32 blob.
        - ``doc_id``: The document ID (used as a tag for filtering).
        - ``metadata``: JSON-serialized DocMetadata.

        Args:
            documents (`list[Document]`):
                A list of Document objects to store. Each must have a
                non-None ``embedding`` field.
            **kwargs (`Any`):
                Additional keyword arguments (unused).
        """
        await self._ensure_index()
        client = await self._get_client()

        from glide import Batch

        batch = Batch(is_atomic=False)
        for doc in documents:
            if doc.embedding is None:
                raise ValueError(
                    f"Document (doc_id={doc.metadata.doc_id}, "
                    f"chunk_id={doc.metadata.chunk_id}) has no "
                    f"embedding.",
                )

            key = self._make_key(doc)
            metadata_json = json.dumps(
                {
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                    "total_chunks": doc.metadata.total_chunks,
                    "content": doc.metadata.content,
                },
                ensure_ascii=False,
            )

            field_map: dict[str, str | bytes] = {
                "vector": _float_list_to_bytes(doc.embedding),
                "doc_id": doc.metadata.doc_id,
                "chunk_id": str(doc.metadata.chunk_id),
                "metadata": metadata_json,
            }

            batch.hset(key, field_map)  # type: ignore[arg-type]

        if documents:
            await client.exec(batch, raise_on_error=True)

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search for relevant documents using vector similarity.

        Uses Valkey's FT.SEARCH with a KNN vector query to find the most
        similar documents.

        Args:
            query_embedding (`Embedding`):
                The embedding vector to search for.
            limit (`int`):
                Maximum number of documents to return.
            score_threshold (`float | None`, optional):
                Minimum similarity score threshold. For COSINE distance,
                scores range from 0 to 1 (higher is more similar).
                Documents below this threshold are filtered out.
            **kwargs (`Any`):
                Additional keyword arguments. Supports:
                - ``filter_expression`` (`str`): A Valkey Search filter
                  expression to apply, e.g. ``"@doc_id:{my_doc}"``.

        Returns:
            `list[Document]`:
                A list of Document objects with metadata and scores,
                sorted by relevance (most similar first).
        """
        await self._ensure_index()
        client = await self._get_client()

        from glide import ft
        from glide_shared.commands.server_modules.ft_options import (
            ft_search_options,
        )

        FtSearchOptions = ft_search_options.FtSearchOptions
        FtSearchLimit = ft_search_options.FtSearchLimit
        ReturnField = ft_search_options.ReturnField

        # Build KNN query
        filter_expr = kwargs.pop("filter_expression", "*")
        if "=>" in filter_expr:
            raise ValueError(
                "filter_expression must not contain '=>'.",
            )
        query = (
            f"{filter_expr}=>"
            f"[KNN {limit} @vector $query_vec AS vector_score]"
        )

        query_vec_bytes = _float_list_to_bytes(query_embedding)

        options = FtSearchOptions(
            params={"query_vec": query_vec_bytes},
            return_fields=[
                ReturnField(field_identifier="metadata"),
                ReturnField(field_identifier="vector_score"),
            ],
            limit=FtSearchLimit(offset=0, count=limit),
        )

        response = await ft.search(
            client,
            self.index_name,
            query,
            options=options,
        )

        # FT.SEARCH returns [total_count: int, results: Mapping]
        if (
            not response
            or len(response) < 2
            or not isinstance(response[1], dict)
        ):
            return []

        results_map = response[1]
        collected: list[Document] = []

        for _key, fields in results_map.items():
            # Parse the vector score (aliased as "vector_score")
            # Valkey returns distance (lower = more similar for COSINE)
            # Convert to similarity: score = 1 - distance for COSINE
            raw_score = fields.get(
                b"vector_score",
                fields.get("vector_score", None),
            )
            if raw_score is not None:
                distance = float(
                    raw_score.decode()
                    if isinstance(raw_score, bytes)
                    else raw_score,
                )
                if self.distance == "COSINE":
                    score = 1.0 - distance
                elif self.distance == "IP":
                    score = 1.0 - distance
                else:
                    # L2: lower distance = more similar, invert
                    score = 1.0 / (1.0 + distance)
            else:
                score = 0.0

            # Apply score threshold
            if score_threshold is not None and score < score_threshold:
                continue

            # Parse metadata
            metadata_raw = fields.get(
                b"metadata",
                fields.get("metadata", "{}"),
            )
            if isinstance(metadata_raw, bytes):
                metadata_raw = metadata_raw.decode()
            metadata_dict = json.loads(metadata_raw)
            metadata = DocMetadata(**metadata_dict)

            collected.append(
                Document(
                    metadata=metadata,
                    score=score,
                ),
            )

        return collected

    async def delete(
        self,
        ids: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Delete documents from the Valkey vector store.

        Args:
            ids (`str | list[str] | None`, optional):
                Document IDs (doc_id values) to delete. All chunks
                belonging to the specified doc_ids will be removed.
            **kwargs (`Any`):
                Additional keyword arguments. Supports:
                - ``keys`` (`list[str]`): Specific Valkey keys to delete
                  directly.
        """
        client = await self._get_client()

        # If specific keys are provided, delete them directly
        keys = kwargs.pop("keys", None)
        if keys:
            for key in keys:
                await client.delete([key])
            return

        if not ids:
            return

        if isinstance(ids, str):
            ids = [ids]

        # Use FT.SEARCH to find keys matching the doc_ids, then delete
        from glide import ft
        from glide_shared.commands.server_modules.ft_options import (
            ft_search_options,
        )

        FtSearchOptions = ft_search_options.FtSearchOptions
        FtSearchLimit = ft_search_options.FtSearchLimit

        await self._ensure_index()

        _batch_size = 500
        for doc_id in ids:
            escaped_id = _escape_tag_value(doc_id)
            query = f"@doc_id:{{{escaped_id}}}"

            while True:
                options = FtSearchOptions(
                    limit=FtSearchLimit(
                        offset=0,
                        count=_batch_size,
                    ),
                )
                response = await ft.search(
                    client,
                    self.index_name,
                    query,
                    options=options,
                )

                if not response or len(response) < 2:
                    break

                results_map = response[1]
                if not results_map:
                    break

                keys_to_delete = [
                    key.decode() if isinstance(key, bytes) else key
                    for key in results_map
                ]
                if keys_to_delete:
                    await client.delete(keys_to_delete)

                # If we got fewer than batch_size, we're done
                if len(results_map) < _batch_size:
                    break

    def get_client(self) -> "GlideClient | GlideClusterClient | None":
        """Get the underlying Glide client for advanced operations.

        Returns:
            The GlideClient or GlideClusterClient instance, or None if
            not yet connected.
        """
        return self._client

    async def drop_index(self) -> None:
        """Drop the search index without deleting the underlying data.

        .. warning::
            After dropping the index, search operations will fail until
            a new index is created.
        """
        from glide import ft

        client = await self._get_client()
        existing = await ft.list(client)
        names = [
            i.decode() if isinstance(i, bytes) else i for i in (existing or [])
        ]
        if self.index_name in names:
            await ft.dropindex(client, self.index_name)
        self._index_created = False

    async def close(self) -> None:
        """Close the Valkey client connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._index_created = False
