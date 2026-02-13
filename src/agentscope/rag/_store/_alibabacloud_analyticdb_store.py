# -*- coding: utf-8 -*-
"""The AlibabaCloud AnalyticDB MySQL vector store implementation."""
import json
from typing import Any, Literal, TYPE_CHECKING, Optional

from .._reader import Document
from ._store_base import VDBStoreBase
from .._document import DocMetadata

from ..._utils._common import _map_text_to_uuid
from ...types import Embedding

if TYPE_CHECKING:
    from pymysql.connections import Connection
else:
    Connection = "pymysql.connections.Connection"


class AlibabaCloudAnalyticDBStore(VDBStoreBase):
    """The AlibabaCloud AnalyticDB MySQL vector store implementation.

    .. note:: Requires pymysql package. Install with:
    `pip install pymysql`
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
        distance: Literal["EUCLIDEAN", "COSINE"] = "EUCLIDEAN",
        connection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the AnalyticDB MySQL vector store.

        Args:
            host (`str`):
                The hostname of the AnalyticDB MySQL server.
            port (`int`):
                The port number of the AnalyticDB MySQL server.
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
            distance (`Literal["EUCLIDEAN", "COSINE"]`, default "EUCLIDEAN"):
                The distance metric to use for similarity search. Can be
                one of "COSINE" (cosine similarity) or "EUCLIDEAN"
                (Euclidean distance).
            connection_kwargs (`dict[str, Any] | None`, optional):
                Other keyword arguments for the MySQL connector.
                Example: {"ssl_ca": "/path/to/ca.pem", "charset": "utf8mb4"}
        """

        try:
            import pymysql
        except ImportError as e:
            raise ImportError(
                "Could not import pymysql python package. "
                "Please install it with `pip install pymysql`.",
            ) from e

        self.database = database
        self.table_name = table_name
        self.dimensions = dimensions
        self.distance = distance

        # Create a connection to AnalyticDB MySQL
        conn_kwargs = connection_kwargs or {}
        self._conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            **conn_kwargs,
        )

    def _execute_sql(
        self,
        sql: str,
        data: Optional[dict[str, Any] | list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """Execute sql.

        Args:
            sql (`str`):
                dml sql.
            data (`Optional[dict[str, Any] | list[dict[str, Any]]]`):
                sql parameters or list data.
        """

        cursor = self._conn.cursor()
        if data is None:
            cursor.execute(sql)
        else:
            if isinstance(data, list):
                cursor.executemany(sql, data)
            else:
                cursor.execute(sql, data)

        columns = cursor.description
        result = []
        for value in cursor.fetchall():
            r = {}
            for idx, datum in enumerate(value):
                k = columns[idx][0]
                r[k] = datum
            result.append(r)
        cursor.close()
        return result

    def _get_distance_function(self) -> str:
        """Get the vector distance function name.

        Returns:
            `str`:
                The SQL vector distance function name.
        """

        if self.distance == "COSINE":
            return "cosine_similarity"
        elif self.distance == "EUCLIDEAN":
            return "l2_distance"
        else:
            raise ValueError(
                f"Unsupported distance metric: {self.distance}. "
                f"AnalyticDB MySQL only supports 'COSINE' and 'EUCLIDEAN'.",
            )

    def _get_distance_score(self, score_threshold: float) -> float:
        """Recompute distance sore threshold based on distance metric.

        Args:
            score_threshold (`float`):
                distance score threshold.

        Returns:
            `float`:
                recomputed distance score threshold.
        """

        if self.distance == "COSINE":
            return 1 - score_threshold
        elif self.distance == "EUCLIDEAN":
            return score_threshold
        else:
            raise ValueError(
                f"Unsupported distance metric: {self.distance}. "
                f"AnalyticDB MySQL only supports 'COSINE' and 'EUCLIDEAN'.",
            )

    async def _validate_table(self) -> None:
        """Validate the table exists, if not, create it."""
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.database}.{self.table_name} (
            id VARCHAR(255) PRIMARY KEY,
            embedding ARRAY<FLOAT>({self.dimensions}) NOT NULL,
            doc_id VARCHAR(255) NOT NULL,
            chunk_id INT NOT NULL,
            content TEXT NOT NULL,
            total_chunks INT NOT NULL,
            ANN INDEX idx_vector_embedding(embedding)
        )
        """
        self._execute_sql(create_table_sql)

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the AnalyticDB MySQL vector store.

        Args:
            documents (`list[Document]`):
                A list of embedding records.
            **kwargs (`Any`):
                Additional arguments for the insert operation.
                - batch_size (`int`): batch rows to insert.
        """
        await self._validate_table()

        batch_size = kwargs.get("batch_size", 32)
        datas = []
        insert_sql = f"""
        REPLACE INTO {self.database}.{self.table_name}
        (id, embedding, doc_id, chunk_id, content, total_chunks)
        VALUES (
            %(id)s, %(embedding)s, %(doc_id)s,
            %(chunk_id)s, %(content)s, %(total_chunks)s
        )
        """

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

            # Format vector
            if doc.embedding is None:
                raise ValueError(
                    f"Document embedding cannot be None for doc_id: "
                    f"{doc.metadata.doc_id}",
                )

            # Serialize content to JSON if it's not a string
            content_str = (
                doc.metadata.content
                if isinstance(doc.metadata.content, str)
                else json.dumps(doc.metadata.content, ensure_ascii=False)
            )

            # Insert data
            val = {
                "id": _map_text_to_uuid(unique_string),
                "embedding": json.dumps(doc.embedding),
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "content": content_str,
                "total_chunks": doc.metadata.total_chunks,
            }
            datas.append(val)
            if len(datas) >= batch_size:
                self._execute_sql(insert_sql, datas)
                datas = []

        if len(datas) > 0:
            self._execute_sql(insert_sql, datas)

    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the AnalyticDB MySQL vector store.

        Args:
            query_embedding (`Embedding`):
                The embedding of the query text.
            limit (`int`):
                The number of relevant documents to retrieve.
            score_threshold (`float | None`, optional):
                The minimum similarity score threshold to filter.
            **kwargs (`Any`):
                Additional arguments for the search operation.
        """

        # Format query vector
        vector_str = "[" + ",".join(map(str, query_embedding)) + "]"

        # Get the distance function
        distance_func = self._get_distance_function()

        # Build WHERE clause
        where_clause = ""
        if score_threshold is not None:
            score = self._get_distance_score(score_threshold)
            where_clause = (
                f"WHERE {distance_func}(embedding, '{vector_str}') <= {score}"
            )

        # Build and execute the search query
        search_sql = f"""
        SELECT
            id,
            doc_id,
            chunk_id,
            content,
            total_chunks,
            {distance_func}(embedding, '{vector_str}') as distance
        FROM {self.database}.{self.table_name}
        {where_clause}
        ORDER BY distance ASC
        LIMIT {limit}
        """
        results = self._execute_sql(search_sql)

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
            collected_res.append(
                Document(
                    embedding=None,
                    score=row["distance"],
                    metadata=doc_metadata,
                ),
            )

        return collected_res

    async def delete(
        self,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Delete documents from the AnalyticDB MySQL vector store,
        truncate table when ids is None and filter is None.

        Args:
            ids (`list[str] | None`, optional):
                List of entity IDs to delete.
            **kwargs (`Any`):
                Additional arguments for the delete operation.
        """

        if ids is not None:
            # Delete by IDs
            params = {"ids": ",".join([f"'{id}'" for id in ids])}
            delete_sql = (
                f"DELETE FROM {self.database}.{self.table_name} "
                f"WHERE id IN (%(ids)s)"
            )
            self._execute_sql(delete_sql, params)
        else:
            delete_sql = f"TRUNCATE TABLE {self.database}.{self.table_name}"
            self._execute_sql(delete_sql)

    def get_client(self) -> Connection:
        """Get the underlying MySQL connection, so that developers can access
        the full functionality of AnalyticDB MySQL.

        Returns:
            `Connection`:
                The underlying MySQL connection.
        """
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
