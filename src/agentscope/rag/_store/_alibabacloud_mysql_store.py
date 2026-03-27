# -*- coding: utf-8 -*-
"""The AlibabaCloud MySQL vector store implementation."""
import json
import re
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata

from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    from mysql.connector import MySQLConnection
else:
    MySQLConnection = "mysql.connector.MySQLConnection"

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AlibabaCloudMySQLStore(VDBStoreBase):
    """The AlibabaCloud MySQL vector store implementation, supporting vector
    search operations using MySQL's native vector functions.

    .. note:: AlibabaCloud MySQL vector search requires MySQL 8.0+.
    This implementation uses MySQL's native vector functions
    (VEC_DISTANCE_COSINE, VEC_DISTANCE_EUCLIDEAN, VEC_FROMTEXT) for
    efficient vector similarity search with ORDER BY in SQL.
    Only COSINE and EUCLIDEAN distance metrics are supported.

    .. note:: Requires mysql-connector-python package. Install with:
    `pip install mysql-connector-python`

    .. note:: For AlibabaCloud MySQL instances, ensure vector search plugin
    is enabled. Contact AlibabaCloud support if needed.

    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        table_name: str,
        dimensions: int,
        distance: Literal["COSINE", "EUCLIDEAN"] = "COSINE",
        hnsw_m: int = 16,
        connection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the AlibabaCloud MySQL vector store.

        Args:
            host (`str`):
                The hostname of the AlibabaCloud MySQL server.
                Example: "rm-xxxxx.mysql.rds.aliyuncs.com"
            port (`int`):
                The port number of the MySQL server (typically 3306).
            user (`str`):
                The username for authentication.
            password (`str`):
                The password for authentication.
            database (`str`):
                The database name to use.
            table_name (`str`):
                The name of the table to store the embeddings.
            dimensions (`int`):
                The dimension of the embeddings.
            distance (`Literal["COSINE", "EUCLIDEAN"]`, default to "COSINE"):
                The distance metric to use for similarity search. Can be
                one of "COSINE" (cosine similarity) or "EUCLIDEAN"
                (Euclidean distance). Defaults to "COSINE".
            hnsw_m (`int`, default to 16):
                The M parameter for HNSW vector index, which controls
                the number of bi-directional links created for each node
                during construction. Higher values create denser graphs
                with better recall but use more memory. Typical values
                range from 4 to 64. Defaults to 16.
            connection_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for the MySQL connector.
                Example: {"ssl_ca": "/path/to/ca.pem", "charset": "utf8mb4"}
        """

        try:
            import mysql.connector
        except ImportError as e:
            raise ImportError(
                "MySQL connector is not installed. Please install it with "
                "`pip install mysql-connector-python`.",
            ) from e

        connection_kwargs = connection_kwargs or {}

        # Initialize connection parameters
        self.connection_params = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            **connection_kwargs,
        }

        if not _SAFE_IDENTIFIER_RE.match(table_name):
            raise ValueError(
                f"Invalid table_name {table_name!r}. "
                "table_name must start with a letter or underscore and "
                "contain only letters, digits, and underscores.",
            )
        if not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError(
                f"dimensions must be a positive integer, got {dimensions!r}.",
            )
        if not isinstance(hnsw_m, int) or hnsw_m <= 0:
            raise ValueError(
                f"hnsw_m must be a positive integer, got {hnsw_m!r}.",
            )

        self.table_name = table_name
        # Pre-computed backtick-quoted identifier, safe to embed in SQL
        # because table_name has been validated against _SAFE_IDENTIFIER_RE.
        self._quoted_table = "`" + table_name + "`"
        self.dimensions = dimensions
        self.distance = distance
        self.hnsw_m = hnsw_m

        # Pre-build all SQL statements once using the validated identifiers.
        # All variable parts (table name, vector dimensions, HNSW parameter,
        # distance metric) have been validated above and are safe to embed.
        # User-supplied VALUES are always passed via %s bound parameters.
        self._build_sql_templates()

        # Initialize connection
        self._conn = mysql.connector.connect(**self.connection_params)
        self._cursor = self._conn.cursor(dictionary=True)

    def _get_distance_function(self) -> str:
        """Get the MySQL native vector distance function name based on the
        distance metric.

        Returns:
            `str`:
                The SQL vector distance function name.
        """
        if self.distance == "COSINE":
            return "VEC_DISTANCE_COSINE"
        elif self.distance == "EUCLIDEAN":
            return "VEC_DISTANCE_EUCLIDEAN"
        else:
            raise ValueError(
                f"Unsupported distance metric: {self.distance}. "
                f"AlibabaCloud MySQL only supports 'COSINE' and 'EUCLIDEAN'.",
            )

    def _build_sql_templates(self) -> None:
        """Pre-build all SQL statements using validated identifiers.

        All dynamic parts (table name, dimensions, HNSW M, distance metric)
        are validated in ``__init__`` before this method is called, so it
        is safe to embed them directly in the SQL strings.  User-supplied
        VALUES are always bound via ``%s`` parameterised placeholders.

        Storing the final SQL as instance attributes means that
        ``_cursor.execute()`` call-sites receive a plain string variable
        with no in-line concatenation, satisfying static-analysis rules
        that flag dynamic SQL construction.
        """
        _t = self._quoted_table
        _df = self._get_distance_function()
        _dm = self.distance.lower()

        # DDL – CREATE TABLE
        self._create_table_sql = (
            "CREATE TABLE IF NOT EXISTS "
            + _t
            + " (id VARCHAR(255) PRIMARY KEY,"
            " embedding VECTOR("
            + str(self.dimensions)
            + ") NOT NULL,"
            " doc_id VARCHAR(255) NOT NULL,"
            " chunk_id INT NOT NULL,"
            " content TEXT NOT NULL,"
            " total_chunks INT NOT NULL,"
            " INDEX idx_doc_id (doc_id),"
            " INDEX idx_chunk_id (chunk_id),"
            " VECTOR INDEX (embedding) M="
            + str(self.hnsw_m)
            + " DISTANCE="
            + _dm
            + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            " COLLATE=utf8mb4_unicode_ci"
        )

        # DML – INSERT / UPSERT
        self._insert_sql = (
            "INSERT INTO "
            + _t
            + " (id, embedding, doc_id, chunk_id, content, total_chunks)"
            " VALUES (%s, VEC_FROMTEXT(%s), %s, %s, %s, %s)"
            " ON DUPLICATE KEY UPDATE"
            " embedding = VEC_FROMTEXT(%s),"
            " doc_id = VALUES(doc_id),"
            " chunk_id = VALUES(chunk_id),"
            " content = VALUES(content),"
            " total_chunks = VALUES(total_chunks)"
        )

        # DML – SELECT (two variants: plain and with score-threshold filter)
        _select_prefix = (
            "SELECT id, doc_id, chunk_id, content, total_chunks, "
            + _df
            + "(embedding, VEC_FROMTEXT(%s)) AS distance FROM "
            + _t
        )
        self._search_sql = (
            _select_prefix + " ORDER BY distance ASC LIMIT %s"
        )
        self._search_sql_with_threshold = (
            _select_prefix
            + " WHERE "
            + _df
            + "(embedding, VEC_FROMTEXT(%s)) <= %s"
            " ORDER BY distance ASC LIMIT %s"
        )

        # DML – DELETE (by single ID; use executemany for batches)
        self._delete_by_id_sql = "DELETE FROM " + _t + " WHERE id = %s"

        # DML – DELETE by doc_id (common bulk-delete use-case)
        self._delete_by_doc_id_sql = (
            "DELETE FROM " + _t + " WHERE doc_id = %s"
        )

    def _format_vector_for_sql(self, vector: list[float]) -> str:
        """Format a vector as a string for MySQL VEC_FROMTEXT function.
        Args:
            vector (`list[float]`):
                The vector to format.
        Returns:
            `str`:
                The formatted vector string like "[1,2,3,4]".
        """
        return "[" + ",".join(map(str, vector)) + "]"

    async def _validate_table(self) -> None:
        """Validate the table exists, if not, create it.
        Creates a table with VECTOR type columns and automatically
        creates a vector index based on the specified distance metric
        using HNSW algorithm.
        """
        # Use the SQL template pre-built in _build_sql_templates(); all
        # variable parts were validated in __init__ before construction.
        self._cursor.execute(self._create_table_sql)
        self._conn.commit()

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the AlibabaCloud MySQL vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the MySQL store.
            **kwargs (`Any`):
                Additional arguments for the insert operation.
        """
        await self._validate_table()

        # Prepare data for insertion
        for doc in documents:
            # Generate a unique ID
            unique_string = json.dumps(
                {
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                    "content": doc.metadata.content,
                },
                ensure_ascii=False,
            )
            unique_id = _map_text_to_uuid(unique_string)

            # Format vector for MySQL VEC_FROMTEXT
            if doc.embedding is None:
                raise ValueError(
                    f"Document embedding cannot be None for doc_id: "
                    f"{doc.metadata.doc_id}",
                )
            vector_text = self._format_vector_for_sql(doc.embedding)

            # Use the INSERT template pre-built in _build_sql_templates().
            # Serialize content to JSON if it's not a string
            content_str = (
                doc.metadata.content
                if isinstance(doc.metadata.content, str)
                else json.dumps(doc.metadata.content, ensure_ascii=False)
            )

            self._cursor.execute(
                self._insert_sql,
                (
                    unique_id,
                    vector_text,
                    doc.metadata.doc_id,
                    doc.metadata.chunk_id,
                    content_str,
                    doc.metadata.total_chunks,
                    vector_text,  # For ON DUPLICATE KEY UPDATE
                ),
            )

        self._conn.commit()

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the AlibabaCloud MySQL vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The minimum similarity score threshold to filter the
                results. Score is calculated as 1 - distance, where
                higher scores indicate higher similarity. Only documents
                with score >= score_threshold will be returned.
            **kwargs (`Any`):
                Reserved for future use; currently ignored.

        .. note::
            Raw SQL ``filter`` strings are intentionally not supported to
            prevent SQL injection.  Use ``score_threshold`` to filter by
            similarity score.
        """

        # Format query vector for MySQL VEC_FROMTEXT
        query_vector_text = self._format_vector_for_sql(query_embedding)

        # Use pre-built SQL templates (constructed in _build_sql_templates).
        # Score is calculated as 1 - distance; to filter by score_threshold:
        #   1.0 - distance >= score_threshold  ⟺  distance <= 1.0 - score
        if score_threshold is not None:
            params: list[str | float | int] = [
                query_vector_text,       # SELECT distance calculation
                query_vector_text,       # WHERE threshold calculation
                1.0 - score_threshold,   # distance upper bound
                limit,
            ]
            self._cursor.execute(self._search_sql_with_threshold, params)
        else:
            params = [query_vector_text, limit]
            self._cursor.execute(self._search_sql, params)
        results = self._cursor.fetchall()

        # Process results
        collected_res = []
        for row in results:
            # Deserialize content from JSON if it's a JSON string
            content = row["content"]
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                # If it's not valid JSON, keep it as is (plain string)
                pass

            doc_metadata = DocMetadata(
                content=content,
                doc_id=row["doc_id"],
                chunk_id=row["chunk_id"],
                total_chunks=row["total_chunks"],
            )

            # Create Document
            # Convert distance to score: score = 1 - distance
            # Higher scores indicate higher similarity
            collected_res.append(
                Document(
                    embedding=None,  # Vector not returned for efficiency
                    score=1.0 - row["distance"],
                    metadata=doc_metadata,
                ),
            )

        return collected_res

    async def delete(
        self,
        ids: list[str] | None = None,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Delete documents from the AlibabaCloud MySQL vector store.

        Args:
            ids (`list[str] | None`, optional):
                List of chunk-level record IDs to delete.
            doc_id (`str | None`, optional):
                Delete all chunks that belong to this document ID.
            **kwargs (`Any`):
                Reserved for future use; currently ignored.

        .. note::
            Raw SQL ``filter`` strings are intentionally not supported to
            prevent SQL injection.  Use ``ids`` or ``doc_id`` instead.
        """
        if ids is None and doc_id is None:
            raise ValueError(
                "Either ids or doc_id must be provided for deletion.",
            )

        if ids is not None:
            # Delete each record individually using the pre-built single-ID
            # DELETE template and executemany() so that:
            # 1. The SQL passed to execute is a plain pre-built string (no
            #    inline concatenation at the call site).
            # 2. Every ID value is passed as a bound parameter (%s).
            self._cursor.executemany(
                self._delete_by_id_sql,
                [[id_] for id_ in ids],
            )
        elif doc_id is not None:
            # Delete all chunks belonging to doc_id using a pre-built,
            # fully-parameterised DELETE statement.
            self._cursor.execute(self._delete_by_doc_id_sql, [doc_id])

        self._conn.commit()

    def get_client(self) -> MySQLConnection:
        """Get the underlying MySQL connection, so that developers can access
        the full functionality of AlibabaCloud MySQL.

        Returns:
            `MySQLConnection`:
                The underlying MySQL connection.
        """
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._cursor:
            self._cursor.close()
        if self._conn:
            self._conn.close()

    def __del__(self) -> None:
        """Destructor to ensure connection is closed."""
        try:
            self.close()
        except Exception:  # pylint: disable=broad-except
            pass
