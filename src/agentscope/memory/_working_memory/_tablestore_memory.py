# -*- coding: utf-8 -*-
"""The Tablestore-based working memory implementation for agentscope."""
import asyncio
import copy
import json
from typing import Any, Literal, Optional, cast


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
            FieldSchema("document_id", FieldType.KEYWORD),
            FieldSchema("tenant_id", FieldType.KEYWORD),
            FieldSchema("session_id", FieldType.KEYWORD),
            FieldSchema("marks_json", FieldType.KEYWORD, is_array=True),
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
            enable_multi_tenant=True,
            **self._knowledge_store_kwargs,
        )

        await self._knowledge_store.init_table()
        self._initialized = True

    def _msg_to_document(self, msg: Msg, marks: list[str]) -> Any:
        """Convert a ``Msg`` to a Tablestore document.
        Msg.id -> document_id
        self._user_id -> tenant_id

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

        text_content = json.dumps(
            msg.to_dict(),
            ensure_ascii=False,
            default=str,
        )

        metadata = {
            "session_id": self._session_id,
            "name": msg.name,
            "role": msg.role,
            "timestamp": msg.timestamp or "",
            "invocation_id": msg.invocation_id or "",
            "marks_json": json.dumps(marks, ensure_ascii=False),
        }

        return TablestoreDocument(
            document_id=msg.id,
            text=text_content,
            tenant_id=self._user_id,
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

        # Restore Msg from document text (JSON-serialized Msg dict)
        if document.text:
            msg_dict = json.loads(document.text)
            msg = Msg.from_dict(msg_dict)
        else:
            # Fallback for legacy documents without msg_json
            role = cast(
                Literal["user", "assistant", "system"],
                metadata.get("role", "user"),
            )
            msg = Msg(
                name=metadata.get("name", ""),
                content=document.text or "",
                role=role,
                timestamp=metadata.get("timestamp") or None,
                invocation_id=metadata.get("invocation_id") or None,
            )
            msg.id = document.document_id

        # Restore marks
        marks: list[str] = []
        marks_json = metadata.get("marks_json", "[]")
        try:
            marks = json.loads(marks_json)
        except (json.JSONDecodeError, TypeError):
            pass

        return msg, marks

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        allow_duplicates: bool = True,
        **kwargs: Any,
    ) -> None:
        """Add message(s) into the memory storage with the given mark
        (if provided).

        Args:
            memories (`Msg | list[Msg] | None`):
                The message(s) to be added.
            marks (`str | list[str] | None`, optional):
                The mark(s) to associate with the message(s). If `None`, no
                mark is associated.
            allow_duplicates (`bool`, defaults to ``True``):
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
            # Get existing documents for the target message IDs
            msg_ids = [msg.id for msg in memories]
            existing_docs = await self._knowledge_store.get_documents(
                document_id_list=msg_ids,
                tenant_id=self._user_id,
            )
            # Filter by session_id to get only duplicates in current session
            existing_ids = {
                doc.document_id
                for doc in existing_docs
                if doc.metadata
                and doc.metadata.get("session_id") == self._session_id
            }
            memories = [msg for msg in memories if msg.id not in existing_ids]

        put_tasks = []
        for msg in memories:
            document = self._msg_to_document(msg, marks_list)
            put_tasks.append(
                self._knowledge_store.put_document(document),
            )
        await asyncio.gather(*put_tasks)

    async def _get_all_msg_ids(self) -> set[str]:
        """Get all message IDs currently stored for this user/session."""
        from tablestore_for_agent_memory.base.filter import Filters

        all_ids: set[str] = set()
        next_token = None
        while True:
            result = await self._knowledge_store.search_documents(
                tenant_id=self._user_id,
                metadata_filter=Filters.logical_and(
                    [
                        Filters.eq("session_id", self._session_id),
                    ],
                ),
                next_token=next_token,
            )
            for hit in result.hits:
                msg_id = hit.document.document_id
                if msg_id:
                    all_ids.add(msg_id)
            next_token = result.next_token
            if not next_token:
                break
        return all_ids

    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to be removed.

        Returns:
            `int`:
                The number of messages removed.
        """
        await self._ensure_initialized()

        existing_ids = await self._get_all_msg_ids()
        deleted_count = 0
        for msg_id in msg_ids:
            if msg_id in existing_ids:
                await self._knowledge_store.delete_document(
                    document_id=msg_id,
                    tenant_id=self._user_id,
                )
                deleted_count += 1

        return deleted_count

    async def delete_by_mark(
        self,
        mark: str | list[str],
        **kwargs: Any,
    ) -> int:
        """Remove messages from the memory by their marks.

        Args:
            mark (`str | list[str]`):
                The mark(s) of the messages to be removed.

        Raises:
            `TypeError`:
                If the provided mark is not a string or a list of strings.

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
                    document_id=doc.document_id,
                    tenant_id=self._user_id,
                )
                deleted_count += 1

        return deleted_count

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        await self._ensure_initialized()
        all_docs = await self._get_all_documents()
        return len(all_docs)

    async def clear(self) -> None:
        """Clear the memory content."""
        await self._ensure_initialized()

        delete_tasks = [
            self._knowledge_store.delete_document_by_tenant(
                tenant_id=self._user_id,
            ),
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
        """Get the messages from the memory by mark (if provided). Otherwise,
        get all messages.

        . note:: If `mark` and `exclude_mark` are both provided, the messages
         will be filtered by both arguments.

        . note:: `mark` and `exclude_mark` should not overlap.

        Args:
            mark (`str | None`, optional):
                The mark to filter messages. If `None`, return all messages.
            exclude_mark (`str | None`, optional):
                The mark to exclude messages. If provided, messages with
                this mark will be excluded from the results.
            prepend_summary (`bool`, defaults to True):
                Whether to prepend the compressed summary as a message

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
        """A unified method to update marks of messages in the storage (add,
        remove, or change marks).

        - If `msg_ids` is provided, the update will be applied to the messages
         with the specified IDs.
        - If `old_mark` is provided, the update will be applied to the
         messages with the specified old mark. Otherwise, the `new_mark` will
         be added to all messages (or those filtered by `msg_ids`).
        - If `new_mark` is `None`, the mark will be removed from the messages.

        Args:
            new_mark (`str | None`, optional):
                The new mark to set for the messages. If `None`, the mark
                will be removed.
            old_mark (`str | None`, optional):
                The old mark to filter messages. If `None`, this constraint
                is ignored.
            msg_ids (`list[str] | None`, optional):
                The list of message IDs to be updated. If `None`, this
                constraint is ignored.

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
                    document_id=doc.document_id,
                    tenant_id=self._user_id,
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

        all_docs: list = []
        next_token = None
        while True:
            result = await self._knowledge_store.search_documents(
                tenant_id=self._user_id,
                metadata_filter=Filters.logical_and(
                    [
                        Filters.eq("session_id", self._session_id),
                    ],
                ),
                meta_data_to_get=[
                    self._text_field,
                    "name",
                    "role",
                    "timestamp",
                    "marks_json",
                    "session_id",
                    "invocation_id",
                ],
                next_token=next_token,
            )
            all_docs.extend(hit.document for hit in result.hits)
            next_token = result.next_token
            if not next_token:
                break
        return all_docs

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
