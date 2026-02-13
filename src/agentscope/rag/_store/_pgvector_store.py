# -*- coding: utf-8 -*-
"""The pgvector store implementation for PostgreSQL."""
import json
from typing import Any, Literal, TYPE_CHECKING

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata

from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    import psycopg2
else:
    psycopg2 = "psycopg2"


class PgVectorStore(VDBStoreBase):
    """The pgvector store implementation for PostgreSQL with vector similarity
    search capabilities.

    .. note:: pgvector is a PostgreSQL extension for vector similarity search.
    This implementation uses pgvector's operators (<->, <#>, <=>) for
    efficient vector similarity search with different distance metrics.

    .. note:: Requires psycopg2 and pgvector extension. Install with:
    `pip install psycopg2-binary` and enable pgvector extension in PostgreSQL.

    .. note:: The pgvector extension must be enabled in your PostgreSQL
    database. Run `CREATE EXTENSION IF NOT EXISTS vector;` in your database.

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
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        connection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the pgvector store.

        Args:
            host (`str`):
                The hostname of the PostgreSQL server.
                Example: "localhost" or "your-db.example.com"
            port (`int`):
                The port number of the PostgreSQL server (typically 5432).
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
            distance (`Literal["COSINE", "L2", "IP"]`, default to "COSINE"):
                The distance metric to use for similarity search. Can be
                one of "COSINE" (cosine distance), "L2" (Euclidean distance),
                or "IP" (inner product/negative dot product).
                Defaults to "COSINE".
            connection_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for the psycopg2 connection.
                Example: {"sslmode": "require", "connect_timeout": 10}
        """

        try:
            import psycopg2
        except ImportError as e:
            raise ImportError(
                "psycopg2 is not installed. Please install it with "
                "`pip install psycopg2-binary`.",
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

        self.table_name = table_name
        self.dimensions = dimensions
        self.distance = distance

        # Initialize connection
        self._conn = psycopg2.connect(**self.connection_params)
        # Use autocommit mode for DDL operations to avoid transaction issues
        self._conn.autocommit = True
        self._cursor = self._conn.cursor()

    def _get_distance_operator(self) -> str:
        """Get the pgvector distance operator based on the distance metric.

        Returns:
            `str`:
                The pgvector distance operator.
        """
        if self.distance == "COSINE":
            return "<=>"  # Cosine distance
        elif self.distance == "L2":
            return "<->"  # L2 distance (Euclidean)
        elif self.distance == "IP":
            return "<#>"  # Inner product (negative dot product)
        else:
            raise ValueError(
                f"Unsupported distance metric: {self.distance}. "
                f"pgvector supports 'COSINE', 'L2', and 'IP'.",
            )

    def _format_vector_for_sql(self, vector: list[float]) -> str:
        """Format a vector as a string for PostgreSQL vector type.

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
        Creates a table with vector type columns and creates an index
        for efficient similarity search using IVFFlat or HNSW algorithm.
        """
        # First, ensure pgvector extension is enabled
        self._cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # Create table with vector type
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} (
            id VARCHAR(255) PRIMARY KEY,
            embedding vector({self.dimensions}) NOT NULL,
            doc_id VARCHAR(255) NOT NULL,
            chunk_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            total_chunks INTEGER NOT NULL
        )
        """
        self._cursor.execute(create_table_sql)

        # Create indexes for metadata fields
        self._cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_doc_id "
            f"ON {self.table_name}(doc_id)",
        )
        self._cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_chunk_id "
            f"ON {self.table_name}(chunk_id)",
        )

        # Create vector index for efficient similarity search
        # Using IVFFlat index with 100 lists (adjust based on data size)
        # Note: HNSW index is also available in newer pgvector versions
        operator = self._get_distance_operator()
        index_name = f"idx_{self.table_name}_embedding_{self.distance.lower()}"

        # Check if index already exists
        self._cursor.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE tablename = %s AND indexname = %s
            """,
            (self.table_name, index_name),
        )

        if not self._cursor.fetchone():
            # Create IVFFlat index
            # Note: Table must have some data before creating IVFFlat index
            # For empty tables, the index will be created after first insert
            try:
                self._cursor.execute(
                    f"CREATE INDEX {index_name} ON {self.table_name} "
                    f"USING ivfflat (embedding {operator}) WITH (lists = 100)",
                )
            except Exception:  # pylint: disable=broad-except
                # If index creation fails (e.g., empty table), continue
                # Index can be created manually later
                pass

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the pgvector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records to be recorded in the pgvector
                store.
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

            # Format vector for PostgreSQL
            if doc.embedding is None:
                raise ValueError(
                    f"Document embedding cannot be None for doc_id: "
                    f"{doc.metadata.doc_id}",
                )
            vector_text = self._format_vector_for_sql(doc.embedding)

            # Serialize content to JSON if it's not a string
            content_str = (
                doc.metadata.content
                if isinstance(doc.metadata.content, str)
                else json.dumps(doc.metadata.content, ensure_ascii=False)
            )

            # Insert data with ON CONFLICT for upsert behavior
            insert_sql = f"""
            INSERT INTO {self.table_name}
            (id, embedding, doc_id, chunk_id, content, total_chunks)
            VALUES (%s, %s::vector, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                embedding = EXCLUDED.embedding,
                doc_id = EXCLUDED.doc_id,
                chunk_id = EXCLUDED.chunk_id,
                content = EXCLUDED.content,
                total_chunks = EXCLUDED.total_chunks
            """

            self._cursor.execute(
                insert_sql,
                (
                    unique_id,
                    vector_text,
                    doc.metadata.doc_id,
                    doc.metadata.chunk_id,
                    content_str,
                    doc.metadata.total_chunks,
                ),
            )

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the pgvector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The maximum distance threshold to filter the results.
                Lower distances indicate higher similarity.
                Note: For COSINE and L2, lower is better. For IP (inner
                product), higher is better (less negative).
            **kwargs (`Any`):
                Additional arguments for the search operation.
                - filter (`str`): WHERE clause to filter the search results.
        """

        # Format query vector
        query_vector_text = self._format_vector_for_sql(query_embedding)

        # Get the distance operator
        operator = self._get_distance_operator()

        # Build WHERE clause
        where_conditions = []
        where_params: list[str | float] = []

        if "filter" in kwargs and kwargs["filter"]:
            # Escape % in filter to avoid psycopg2 treating it as placeholder
            filter_clause = kwargs["filter"].replace("%", "%%")
            where_conditions.append(filter_clause)

        # Add distance threshold condition if specified
        if score_threshold is not None:
            where_conditions.append(
                f"(embedding {operator} %s::vector) <= %s",
            )
            where_params.extend([query_vector_text, score_threshold])

        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)

        # Build and execute the search query
        search_sql = f"""
        SELECT
            id,
            doc_id,
            chunk_id,
            content,
            total_chunks,
            (embedding {operator} %s::vector) as distance
        FROM {self.table_name}
        {where_clause}
        ORDER BY embedding {operator} %s::vector
        LIMIT %s
        """

        # Prepare parameters in the correct order
        params: list[str | float | int] = [query_vector_text]
        params.extend(where_params)
        params.append(query_vector_text)
        params.append(limit)

        self._cursor.execute(search_sql, tuple(params))
        results = self._cursor.fetchall()

        # Process results
        collected_res = []
        for row in results:
            # Unpack row
            (
                _,
                doc_id,
                chunk_id,
                content,
                total_chunks,
                distance,
            ) = row

            # Deserialize content from JSON if it's a JSON string
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                # If it's not valid JSON, keep it as is (plain string)
                pass

            doc_metadata = DocMetadata(
                content=content,
                doc_id=doc_id,
                chunk_id=chunk_id,
                total_chunks=total_chunks,
            )

            # Create Document
            # Note: distance is returned as-is. Lower is better for
            # COSINE and L2, higher (less negative) is better for IP
            collected_res.append(
                Document(
                    embedding=None,  # Vector not returned for efficiency
                    score=float(distance),
                    metadata=doc_metadata,
                ),
            )

        return collected_res

    async def delete(
        self,
        ids: list[str] | None = None,
        filter: str | None = None,  # pylint: disable=redefined-builtin
        **kwargs: Any,
    ) -> None:
        """Delete documents from the pgvector store.

        Args:
            ids (`list[str] | None`, optional):
                List of entity IDs to delete.
            filter (`str | None`, optional):
                WHERE clause expression to filter documents to delete.
            **kwargs (`Any`):
                Additional arguments for the delete operation.
        """
        if ids is None and filter is None:
            raise ValueError(
                "Either ids or filter must be provided for deletion.",
            )

        if ids is not None:
            # Delete by IDs
            placeholders = ",".join(["%s"] * len(ids))
            delete_sql = (
                f"DELETE FROM {self.table_name} WHERE id IN ({placeholders})"
            )
            self._cursor.execute(delete_sql, tuple(ids))
        elif filter is not None:
            # Delete by filter
            delete_sql = f"DELETE FROM {self.table_name} WHERE {filter}"
            self._cursor.execute(delete_sql)

    def get_client(self) -> Any:
        """Get the underlying PostgreSQL connection, so that developers can
        access the full functionality of PostgreSQL.

        Returns:
            `psycopg2.extensions.connection`:
                The underlying PostgreSQL connection.
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
