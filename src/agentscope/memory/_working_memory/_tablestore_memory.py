# -*- coding: utf-8 -*-
"""The Tablestore-based working memory implementation for agentscope."""
import asyncio
import copy
import json
import uuid
from typing import Any, Optional


from ...message import Msg
from ._base import MemoryBase


class TablestoreMemory(MemoryBase):
    """A Tablestore-based working memory implementation using
    ``tablestore_for_agent_memory``'s ``AsyncKnowledgeStore``.

    This memory stores messages in Alibaba Cloud Tablestore, enabling
    persistent and searchable memory across distributed environments.
    Messages are stored as documents with optional embedding vectors
    for semantic search.
    """

    _SEARCH_INDEX_NAME = "agentscope_memory_search_index"

    def __init__(
        self,
        end_point: str,
        instance_name: str,
        access_key_id: str,
        access_key_secret: str,
        user_id: str = "default",
        session_id: str = "default",
        sts_token: Optional[str] = None,
        table_name: str = "agentscope_memory",
        text_field: str = "text",
        embedding_field: str = "embedding",
        vector_dimension: int = 0,
        **kwargs: Any,
    ) -> None:
        """Initialize the Tablestore memory.

        Args:
            end_point (`str`):
                The endpoint of the Tablestore instance.
            instance_name (`str`):
                The name of the Tablestore instance.
            access_key_id (`str`):
                The access key ID for authentication.
            access_key_secret (`str`):
                The access key secret for authentication.
            user_id (`str`, defaults to ``"default"``):
                The user ID for multi-tenant isolation.
            session_id (`str`, defaults to ``"default"``):
                The session ID for session-level isolation.
            sts_token (`str | None`, optional):
                The STS token for temporary credentials.
            table_name (`str`, defaults to ``"agentscope_memory"``):
                The table name for storing memory documents.
            text_field (`str`, defaults to ``"text"``):
                The field name for text content in Tablestore.
            embedding_field (`str`, defaults to ``"embedding"``):
                The field name for embedding vectors in Tablestore.
            vector_dimension (`int`, defaults to ``0``):
                The dimension of the embedding vectors. Set to ``0``
                if not using vector search.
            **kwargs (`Any`):
                Additional keyword arguments passed to the
                ``AsyncKnowledgeStore``.
        """
        super().__init__()

        try:
            from tablestore import (
                AsyncOTSClient as AsyncTablestoreClient,
                WriteRetryPolicy,
                FieldSchema,
                FieldType,
            )
        except ImportError as exc:
            raise ImportError(
                "The 'tablestore' and 'tablestore-for-agent-memory' packages "
                "are required for TablestoreMemory. Please install them via "
                "'pip install tablestore tablestore-for-agent-memory'.",
            ) from exc

        self._user_id = user_id
        self._session_id = session_id
        self._table_name = table_name
        self._text_field = text_field
        self._embedding_field = embedding_field
        self._vector_dimension = vector_dimension

        self._tablestore_client = AsyncTablestoreClient(
            end_point=end_point,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            instance_name=instance_name,
            sts_token=None if sts_token == "" else sts_token,
            retry_policy=WriteRetryPolicy(),
        )

        self._search_index_schema = [
            FieldSchema("user_id", FieldType.KEYWORD),
            FieldSchema("session_id", FieldType.KEYWORD),
            FieldSchema("msg_id", FieldType.KEYWORD),
        ]

        self._knowledge_store = None
        self._knowledge_store_kwargs = kwargs
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Lazily initialize the knowledge store on first use."""
        if self._initialized:
            return

        from tablestore_for_agent_memory.knowledge.async_knowledge_store import (  # noqa: E501
            AsyncKnowledgeStore,
        )

        self._knowledge_store = AsyncKnowledgeStore(
            tablestore_client=self._tablestore_client,
            vector_dimension=self._vector_dimension,
            table_name=self._table_name,
            search_index_name=self._SEARCH_INDEX_NAME,
            search_index_schema=copy.deepcopy(self._search_index_schema),
            text_field=self._text_field,
            embedding_field=self._embedding_field,
            enable_multi_tenant=False,
            **self._knowledge_store_kwargs,
        )

        await self._knowledge_store.init_table()
        self._initialized = True

    def _msg_to_document(self, msg: Msg, marks: list[str]) -> Any:
        """Convert a ``Msg`` to a Tablestore document.

        Args:
            msg (`Msg`):
                The message to convert.
            marks (`list[str]`):
                The marks associated with the message.

        Returns:
            A ``Document`` object for Tablestore.
        """
        from tablestore_for_agent_memory.base.base_knowledge_store import (
            Document as TablestoreDocument,
        )

        content = msg.content
        text_content = None
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_content = block.get("text", "")
                    break

        metadata = {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "msg_id": msg.id,
            "name": msg.name,
            "role": msg.role,
            "content_json": json.dumps(
                msg.content,
                ensure_ascii=False,
                default=str,
            ),
            "metadata_json": json.dumps(
                msg.metadata,
                ensure_ascii=False,
                default=str,
            ),
            "timestamp": msg.timestamp or "",
            "marks_json": json.dumps(marks, ensure_ascii=False),
        }

        return TablestoreDocument(
            document_id=msg.id,
            text=text_content,
            metadata=metadata,
        )

    @staticmethod
    def _document_to_msg_and_marks(document: Any) -> tuple[Msg, list[str]]:
        """Convert a Tablestore document back to a ``Msg`` and marks.

        Args:
            document:
                The Tablestore document to convert.

        Returns:
            A tuple of (``Msg``, marks list).
        """
        metadata = document.metadata or {}

        content_json = metadata.get("content_json", '""')
        try:
            content = json.loads(content_json)
        except (json.JSONDecodeError, TypeError):
            content = document.text or ""

        metadata_dict = {}
        metadata_json = metadata.get("metadata_json", "{}")
        try:
            metadata_dict = json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass

        marks = []
        marks_json = metadata.get("marks_json", "[]")
        try:
            marks = json.loads(marks_json)
        except (json.JSONDecodeError, TypeError):
            pass

        msg = Msg(
            name=metadata.get("name", ""),
            content=content,
            role=metadata.get("role", "user"),
            metadata=metadata_dict if metadata_dict else None,
            timestamp=metadata.get("timestamp") or None,
        )
        msg.id = metadata.get("msg_id", document.document_id)

        return msg, marks

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        allow_duplicates: bool = False,
        **kwargs: Any,
    ) -> None:
        """Add message(s) into the Tablestore memory.

        Args:
            memories (`Msg | list[Msg] | None`):
                The message(s) to be added.
            marks (`str | list[str] | None`, optional):
                The mark(s) to associate with the message(s).
            allow_duplicates (`bool`, defaults to ``False``):
                Whether to allow duplicate messages.
        """
        if memories is None:
            return

        await self._ensure_initialized()

        if isinstance(memories, Msg):
            memories = [memories]

        if marks is None:
            marks_list: list[str] = []
        elif isinstance(marks, str):
            marks_list = [marks]
        elif isinstance(marks, list) and all(
            isinstance(m, str) for m in marks
        ):
            marks_list = marks
        else:
            raise TypeError(
                f"The mark should be a string, a list of strings, or None, "
                f"but got {type(marks)}.",
            )

        if not allow_duplicates:
            existing_ids = await self._get_all_msg_ids()
            memories = [msg for msg in memories if msg.id not in existing_ids]

        put_tasks = []
        for msg in memories:
            document = self._msg_to_document(msg, marks_list)
            if allow_duplicates:
                document.document_id = str(uuid.uuid4())
            put_tasks.append(
                self._knowledge_store.put_document(document),
            )
        await asyncio.gather(*put_tasks)

    async def _get_all_msg_ids(self) -> set[str]:
        """Get all message IDs currently stored for this user/session."""
        from tablestore_for_agent_memory.base.filter import Filters

        all_ids: set[str] = set()
        result = await self._knowledge_store.search_documents(
            metadata_filter=Filters.logical_and(
                [
                    Filters.eq("user_id", self._user_id),
                    Filters.eq("session_id", self._session_id),
                ],
            ),
            meta_data_to_get=["msg_id"],
        )
        for hit in result.hits:
            msg_id = hit.document.metadata.get("msg_id")
            if msg_id:
                all_ids.add(msg_id)
        return all_ids

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from Tablestore by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        await self._ensure_initialized()

        from tablestore_for_agent_memory.base.filter import Filters

        deleted_count = 0
        for msg_id in msg_ids:
            result = await self._knowledge_store.search_documents(
                metadata_filter=Filters.logical_and(
                    [
                        Filters.eq("user_id", self._user_id),
                        Filters.eq("session_id", self._session_id),
                        Filters.eq("msg_id", msg_id),
                    ],
                ),
                meta_data_to_get=["msg_id"],
            )
            for hit in result.hits:
                await self._knowledge_store.delete_document(
                    hit.document.document_id,
                )
                deleted_count += 1

        return deleted_count

    async def delete_by_mark(
        self,
        mark: str | list[str],
        **kwargs: Any,
    ) -> int:
        """Remove messages from Tablestore by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        if isinstance(mark, str):
            mark = [mark]

        if not isinstance(mark, list) or not all(
            isinstance(m, str) for m in mark
        ):
            raise TypeError(
                f"The mark should be a string or a list of strings, "
                f"but got {type(mark)}.",
            )

        await self._ensure_initialized()

        all_docs = await self._get_all_documents()
        deleted_count = 0

        for doc in all_docs:
            _, doc_marks = self._document_to_msg_and_marks(doc)
            if any(m in doc_marks for m in mark):
                await self._knowledge_store.delete_document(
                    doc.document_id,
                )
                deleted_count += 1

        return deleted_count

    async def size(self) -> int:
        """Get the number of messages in Tablestore for this user/session.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        await self._ensure_initialized()
        all_docs = await self._get_all_documents()
        return len(all_docs)

    async def clear(self) -> None:
        """Clear all messages from Tablestore for this user/session."""
        await self._ensure_initialized()

        all_docs = await self._get_all_documents()
        delete_tasks = [
            self._knowledge_store.delete_document(doc.document_id)
            for doc in all_docs
        ]
        if delete_tasks:
            await asyncio.gather(*delete_tasks)

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get messages from Tablestore, optionally filtered by mark.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If ``None``, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages.
            prepend_summary (`bool`, defaults to ``True``):
                Whether to prepend the compressed summary as a message.

        Returns:
            `list[Msg]`:
                The list of messages retrieved from the storage.
        """
        if not (mark is None or isinstance(mark, str)):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        if not (exclude_mark is None or isinstance(exclude_mark, str)):
            raise TypeError(
                f"The exclude_mark should be a string or None, but got "
                f"{type(exclude_mark)}.",
            )

        await self._ensure_initialized()

        all_docs = await self._get_all_documents()
        results: list[Msg] = []

        for doc in all_docs:
            msg, doc_marks = self._document_to_msg_and_marks(doc)

            if mark is not None and mark not in doc_marks:
                continue

            if exclude_mark is not None and exclude_mark in doc_marks:
                continue

            results.append(msg)

        if prepend_summary and self._compressed_summary:
            return [
                Msg("user", self._compressed_summary, "user"),
                *results,
            ]

        return results

    async def update_messages_mark(
        self,
        new_mark: str | None,
        old_mark: str | None = None,
        msg_ids: list[str] | None = None,
    ) -> int:
        """Update marks of messages in Tablestore.

        Args:
            new_mark (`str | None`):
                The new mark to set. If ``None``, the old mark is removed.
            old_mark (`str | None`, optional):
                The old mark to filter messages.
            msg_ids (`list[str] | None`, optional):
                The list of message IDs to be updated.

        Returns:
            `int`:
                The number of messages updated.
        """
        await self._ensure_initialized()

        all_docs = await self._get_all_documents()
        updated_count = 0

        for doc in all_docs:
            msg, doc_marks = self._document_to_msg_and_marks(doc)

            if msg_ids is not None and msg.id not in msg_ids:
                continue

            if old_mark is not None and old_mark not in doc_marks:
                continue

            original_marks = doc_marks.copy()

            if new_mark is None:
                if old_mark in doc_marks:
                    doc_marks.remove(old_mark)
            else:
                if old_mark is not None and old_mark in doc_marks:
                    doc_marks.remove(old_mark)
                if new_mark not in doc_marks:
                    doc_marks.append(new_mark)

            if doc_marks != original_marks:
                # Re-save the document with updated marks
                await self._knowledge_store.delete_document(
                    doc.document_id,
                )
                await self._knowledge_store.put_document(
                    self._msg_to_document(msg, doc_marks),
                )
                updated_count += 1

        return updated_count

    async def _get_all_documents(self) -> list:
        """Get all documents for the current user/session from Tablestore.

        Returns:
            A list of Tablestore documents.
        """
        from tablestore_for_agent_memory.base.filter import Filters

        result = await self._knowledge_store.search_documents(
            metadata_filter=Filters.logical_and(
                [
                    Filters.eq("user_id", self._user_id),
                    Filters.eq("session_id", self._session_id),
                ],
            ),
            meta_data_to_get=[
                "msg_id",
                "name",
                "role",
                "content_json",
                "metadata_json",
                "timestamp",
                "marks_json",
                "user_id",
                "session_id",
            ],
        )
        return [hit.document for hit in result.hits]

    async def close(self) -> None:
        """Close the Tablestore client connection."""
        if self._knowledge_store is not None:
            await self._knowledge_store.close()
            self._knowledge_store = None
            self._initialized = False

    def state_dict(self) -> dict:
        """Get the state dictionary for serialization.

        Note: Only the compressed summary is serialized. The actual memory
        content is persisted in Tablestore.
        """
        return {
            "_compressed_summary": self._compressed_summary,
        }

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary for deserialization.

        Args:
            state_dict (`dict`):
                The state dictionary to load.
            strict (`bool`, defaults to ``True``):
                If ``True``, raises an error if required keys are missing.
        """
        self._compressed_summary = state_dict.get("_compressed_summary", "")
