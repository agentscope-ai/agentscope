# -*- coding: utf-8 -*-
"""The Tablestore-based working memory implementation for agentscope."""
import asyncio
import copy
import json
from typing import Any, Optional


from ..._logging import logger
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
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Lazily initialize the knowledge store on first use.

        Uses an ``asyncio.Lock`` to prevent concurrent initialization when
        multiple coroutines call this method simultaneously.
        """
        if self._initialized:
            return
        async with self._init_lock:
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

    _DOCUMENT_ID_SEPARATOR = ":::"

    def _make_document_id(self, msg_id: str) -> str:
        """Convert a message ID to a Tablestore document ID.

        The document ID is formatted as ``{msg_id}:::{session_id}`` to
        ensure uniqueness across sessions within the same tenant.

        Args:
            msg_id (`str`):
                The message ID.

        Returns:
            `str`:
                The Tablestore document ID.
        """
        return f"{msg_id}{self._DOCUMENT_ID_SEPARATOR}{self._session_id}"

    def _extract_msg_id(self, document_id: str) -> str:
        """Extract the message ID from a Tablestore document ID.

        The method verifies that ``document_id`` ends with
        ``:::{session_id}``. If it does not, an error is logged and
        the original ``document_id`` is returned as-is.

        Args:
            document_id (`str`):
                The Tablestore document ID in ``{msg_id}:::{session_id}``
                format.

        Returns:
            `str`:
                The original message ID.
        """
        expected_suffix = f"{self._DOCUMENT_ID_SEPARATOR}{self._session_id}"
        if not document_id.endswith(expected_suffix):
            logger.error(
                "Unexpected document_id format: '%s'. "
                "Expected suffix ':::%s'.",
                document_id,
                self._session_id,
            )
            return document_id
        return document_id[: -len(expected_suffix)]

    def _msg_to_document(self, msg: Msg, marks: list[str]) -> Any:
        """Convert a ``Msg`` to a Tablestore document.

        The document ID is formatted as ``{msg.id}:::{session_id}``.
        ``self._user_id`` is used as ``tenant_id``.

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
            document_id=self._make_document_id(msg.id),
            text=text_content,
            tenant_id=self._user_id,
            metadata=metadata,
        )

    @staticmethod
    def _document_to_msg_and_marks(document: Any) -> tuple[Msg, list[str]]:
        """Convert a Tablestore document back to a ``Msg`` and marks.

        The ``Msg`` is restored entirely from the JSON-serialized text
        stored in ``document.text``. The ``document_id`` is in
        ``{msg_id}:::{session_id}`` format and is not used for restoring
        the ``Msg``.

        Args:
            document:
                The Tablestore document to convert.

        Returns:
            A tuple of (``Msg``, marks list).
        """
        msg_dict = json.loads(document.text)
        msg = Msg.from_dict(msg_dict)

        metadata = document.metadata or {}
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
            # Filter out duplicates
            existing_ids = await self._get_existing_msg_ids_in_session(
                [msg.id for msg in memories],
            )
            memories = [msg for msg in memories if msg.id not in existing_ids]

        put_tasks = []
        for msg in memories:
            document = self._msg_to_document(msg, marks_list)
            put_tasks.append(
                self._knowledge_store.put_document(document),
            )
        await asyncio.gather(*put_tasks)

    async def _get_existing_msg_ids_in_session(
        self,
        msg_ids: list[str],
    ) -> set[str]:
        """Get the IDs that actually exist in the current session from the
        provided list.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to check.

        Returns:
            `set[str]`:
                The set of message IDs that exist in the current session.
        """
        document_ids = [self._make_document_id(mid) for mid in msg_ids]
        existing_docs = await self._knowledge_store.get_documents(
            document_id_list=document_ids,
            tenant_id=self._user_id,
        )
        return {
            self._extract_msg_id(doc.document_id)
            for doc in existing_docs
            if doc is not None
        }

    async def _get_existing_msg_ids_and_marks_in_session(
        self,
        msg_ids: list[str],
    ) -> dict[str, list[str]]:
        """Get the IDs and their marks for messages that actually exist in
        the current session from the provided list.

        Args:
            msg_ids (`list[str]`):
                The list of message IDs to check.

        Returns:
            `dict[str, list[str]]`:
                A mapping from message ID to its list of marks, only for
                messages that exist in the current session.
        """
        document_ids = [self._make_document_id(mid) for mid in msg_ids]
        existing_docs = await self._knowledge_store.get_documents(
            document_id_list=document_ids,
            tenant_id=self._user_id,
        )

        result_map: dict[str, list[str]] = {}
        for doc in existing_docs:
            if doc is None:
                continue
            msg_id = self._extract_msg_id(doc.document_id)
            metadata = doc.metadata or {}
            marks_json = metadata.get("marks_json", "[]")
            try:
                msg_marks = json.loads(marks_json)
            except (json.JSONDecodeError, TypeError):
                msg_marks = []
            result_map[msg_id] = msg_marks
        return result_map

    async def _get_all_msg_ids(self) -> set[str]:
        """Get all message IDs currently stored for this user/session."""
        return await self._search_msg_ids_by_marks()

    async def _get_all_msg_ids_and_marks(self) -> dict[str, list[str]]:
        """Get all message IDs and their marks for this user/session.

        Returns:
            `dict[str, list[str]]`:
                A mapping from message ID to its full list of marks.
        """
        return await self._search_msg_ids_and_marks_by_marks()

    async def _search_msg_ids_by_marks(
        self,
        marks: str | list[str] | None = None,
    ) -> set[str]:
        """Search for message IDs, optionally filtered by marks.

        Uses ``Filters.In`` on the ``marks_json`` field (when marks is
        provided) combined with ``session_id`` and ``tenant_id`` filters
        to query matching documents via ``search_documents``.

        Args:
            marks (`str | list[str] | None`, optional):
                A single mark string or list of marks to filter by.
                If provided, returns messages that contain **any** of
                the specified marks. If ``None``, returns all message
                IDs in the session.

        Returns:
            `set[str]`:
                The set of message IDs matching the filter criteria.
        """
        from tablestore_for_agent_memory.base.filter import Filters

        if isinstance(marks, str):
            marks = [marks]

        conditions = [Filters.eq("session_id", self._session_id)]
        if marks:
            conditions.append(Filters.In("marks_json", marks))

        matched_ids: set[str] = set()
        next_token = None
        while True:
            result = await self._knowledge_store.search_documents(
                tenant_id=self._user_id,
                metadata_filter=Filters.logical_and(conditions),
                next_token=next_token,
            )
            for hit in result.hits:
                document_id = hit.document.document_id
                if document_id:
                    matched_ids.add(self._extract_msg_id(document_id))
            next_token = result.next_token
            if not next_token:
                break
        return matched_ids

    async def _search_msg_ids_and_marks_by_marks(
        self,
        marks: str | list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Search for message IDs and their marks, optionally filtered by
        marks.

        Uses ``Filters.In`` on the ``marks_json`` field (when marks is
        provided) combined with ``session_id`` and ``tenant_id`` filters
        to query matching documents via ``search_documents``.

        Args:
            marks (`str | list[str] | None`, optional):
                A single mark string or list of marks to filter by.
                If provided, returns messages that contain **any** of
                the specified marks. If ``None``, returns all messages
                and their marks.

        Returns:
            `dict[str, list[str]]`:
                A mapping from message ID to its full list of marks.
        """
        from tablestore_for_agent_memory.base.filter import Filters

        if isinstance(marks, str):
            marks = [marks]

        conditions = [Filters.eq("session_id", self._session_id)]
        if marks:
            conditions.append(Filters.In("marks_json", marks))

        result_map: dict[str, list[str]] = {}
        next_token = None
        while True:
            result = await self._knowledge_store.search_documents(
                tenant_id=self._user_id,
                metadata_filter=Filters.logical_and(conditions),
                meta_data_to_get=["marks_json"],
                next_token=next_token,
            )
            for hit in result.hits:
                document_id = hit.document.document_id
                if document_id:
                    msg_id = self._extract_msg_id(document_id)
                    metadata = hit.document.metadata or {}
                    marks_json = metadata.get("marks_json", "[]")
                    try:
                        msg_marks = json.loads(marks_json)
                    except (json.JSONDecodeError, TypeError):
                        msg_marks = []
                    result_map[msg_id] = msg_marks
            next_token = result.next_token
            if not next_token:
                break
        return result_map

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

        # Get only the IDs that actually exist in the current session
        existing_ids = await self._get_existing_msg_ids_in_session(msg_ids)

        delete_tasks = [
            self._knowledge_store.delete_document(
                document_id=self._make_document_id(msg_id),
                tenant_id=self._user_id,
            )
            for msg_id in existing_ids
        ]

        if delete_tasks:
            await asyncio.gather(*delete_tasks)

        return len(existing_ids)

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

        matched_msg_ids = await self._search_msg_ids_by_marks(mark)
        if not matched_msg_ids:
            return 0

        delete_tasks = [
            self._knowledge_store.delete_document(
                document_id=self._make_document_id(msg_id),
                tenant_id=self._user_id,
            )
            for msg_id in matched_msg_ids
        ]
        await asyncio.gather(*delete_tasks)

        return len(matched_msg_ids)

    async def size(self) -> int:
        """Get the number of messages in the storage.

        Returns:
            `int`:
                The number of messages in the storage.
        """
        await self._ensure_initialized()
        all_msg_ids = await self._get_all_msg_ids()
        return len(all_msg_ids)

    async def clear(self) -> None:
        """Clear the memory content for the current session."""
        await self._ensure_initialized()

        all_msg_ids = await self._get_all_msg_ids()
        if not all_msg_ids:
            return

        delete_tasks = [
            self._knowledge_store.delete_document(
                document_id=self._make_document_id(msg_id),
                tenant_id=self._user_id,
            )
            for msg_id in all_msg_ids
        ]
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

        .. note:: If `mark` and `exclude_mark` are both provided, the messages
            will be filtered by both arguments, and they should not overlap.

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

        all_docs = await self._search_documents_by_marks_and_exclude_marks(
            marks=mark,
            exclude_marks=exclude_mark,
        )

        results: list[Msg] = []

        for doc in all_docs:
            msg, _ = self._document_to_msg_and_marks(doc)
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

        # Get msg_ids and their marks
        if msg_ids is not None:
            # Get the marks for the provided msg_ids,
            # use msg id to search is faster than using marks
            id_to_marks = (
                await self._get_existing_msg_ids_and_marks_in_session(
                    msg_ids,
                )
            )
        else:
            id_to_marks = await self._search_msg_ids_and_marks_by_marks(
                old_mark,
            )

        # Collect msg_ids that need mark updates
        ids_to_update: dict[str, list[str]] = {}
        for msg_id, current_marks in id_to_marks.items():
            if old_mark is not None and old_mark not in current_marks:
                continue

            updated_marks = current_marks.copy()
            changed = False

            if new_mark is None:
                if old_mark in updated_marks:
                    updated_marks.remove(old_mark)
                    changed = True
            else:
                if old_mark is not None and old_mark in updated_marks:
                    updated_marks.remove(old_mark)
                    changed = True
                if new_mark not in updated_marks:
                    updated_marks.append(new_mark)
                    changed = True

            if changed:
                ids_to_update[msg_id] = updated_marks

        if not ids_to_update:
            return 0

        from tablestore_for_agent_memory.base.base_knowledge_store import (
            Document as TablestoreDocument,
        )

        update_tasks = []
        for msg_id, updated_marks in ids_to_update.items():
            update_doc = TablestoreDocument(
                document_id=self._make_document_id(msg_id),
                tenant_id=self._user_id,
                metadata={
                    "marks_json": json.dumps(
                        updated_marks,
                        ensure_ascii=False,
                    ),
                },
            )
            update_tasks.append(
                self._knowledge_store.update_document(update_doc),
            )

        await asyncio.gather(*update_tasks)
        return len(ids_to_update)

    async def _search_documents_by_marks_and_exclude_marks(
        self,
        marks: str | list[str] | None = None,
        exclude_marks: str | list[str] | None = None,
    ) -> list:
        """Get all documents filtered by inclusion and/or exclusion marks.

        Dynamically builds filters based on the provided arguments:
        - If ``marks`` is provided, uses ``Filters.In`` to include only
          documents with any of the specified marks.
        - If ``exclude_marks`` is provided, uses ``Filters.not_in`` to
          exclude documents with any of the specified marks.
        - If neither is provided, returns all documents in the session.

        Args:
            marks (`str | list[str] | None`, optional):
                A single mark string or list of marks to include.
                If ``None``, no inclusion filter is applied.
            exclude_marks (`str | list[str] | None`, optional):
                A single mark string or list of marks to exclude.
                If ``None``, no exclusion filter is applied.

        Returns:
            `list`:
                A list of Tablestore documents matching the filter criteria.
        """
        from tablestore_for_agent_memory.base.filter import Filters

        if isinstance(marks, str):
            marks = [marks]
        if isinstance(exclude_marks, str):
            exclude_marks = [exclude_marks]

        conditions = [Filters.eq("session_id", self._session_id)]
        if marks:
            conditions.append(Filters.In("marks_json", marks))
        if exclude_marks:
            conditions.append(Filters.not_in("marks_json", exclude_marks))

        all_docs: list = []
        next_token = None
        while True:
            result = await self._knowledge_store.search_documents(
                tenant_id=self._user_id,
                metadata_filter=Filters.logical_and(conditions),
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

        # Sort documents by timestamp to maintain message order
        all_docs.sort(
            key=lambda doc: (doc.metadata or {}).get("timestamp", ""),
        )
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
