# -*- coding: utf-8 -*-
"""Tests for the Tablestore memory implementation."""
# pylint: disable=protected-access,too-many-public-methods
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.memory._working_memory._tablestore_memory import (
    TablestoreMemory,
)
from agentscope.message import Msg


def _create_mock_document(
    msg: Msg,
    marks: "list[str] | None" = None,
    user_id: str = "default",
    session_id: str = "default",
) -> MagicMock:
    """Create a mock Tablestore document from a Msg."""
    if marks is None:
        marks = []

    doc = MagicMock()
    doc.document_id = f"{msg.id}:::{session_id}"
    doc.text = json.dumps(
        msg.to_dict(),
        ensure_ascii=False,
        default=str,
    )
    doc.tenant_id = user_id
    doc.metadata = {
        "session_id": session_id,
        "name": msg.name,
        "role": msg.role,
        "timestamp": msg.timestamp or "",
        "invocation_id": msg.invocation_id or "",
        "marks_json": json.dumps(marks, ensure_ascii=False),
    }
    return doc


def _create_memory_with_mocks() -> "TablestoreMemory":
    """Create a TablestoreMemory instance with mocked dependencies."""
    memory = object.__new__(TablestoreMemory)
    # Initialize StateModule base
    from collections import OrderedDict

    memory._module_dict = OrderedDict()
    memory._attribute_dict = OrderedDict()
    memory._compressed_summary = ""
    memory.register_state("_compressed_summary")

    memory._user_id = "test_user"
    memory._session_id = "test_session"
    memory._table_name = "test_memory"
    memory._text_field = "text"
    memory._embedding_field = "embedding"
    memory._tablestore_client = MagicMock()
    memory._search_index_schema = []
    memory._knowledge_store = AsyncMock()
    memory._knowledge_store_kwargs = {}
    memory._initialized = True
    return memory


class TablestoreMemoryTest(IsolatedAsyncioTestCase):
    """Test cases for the Tablestore memory module."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.memory = _create_memory_with_mocks()
        self.msgs = []
        for i in range(10):
            msg = Msg("user", f"message {i}", "user")
            msg.id = str(i)
            self.msgs.append(msg)

    async def test_add_messages(self) -> None:
        """Test adding messages to Tablestore memory."""
        # Mock _search_msg_ids_by_marks to return empty set (no duplicates)
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        await self.memory.add(self.msgs[:3])

        # Verify put_document was called 3 times
        self.assertEqual(
            self.memory._knowledge_store.put_document.call_count,
            3,
        )

    async def test_add_single_message(self) -> None:
        """Test adding a single message."""
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        await self.memory.add(self.msgs[0])

        self.memory._knowledge_store.put_document.assert_called_once()

    async def test_add_none(self) -> None:
        """Test adding None does nothing."""
        await self.memory.add(None)

        self.memory._knowledge_store.put_document.assert_not_called()

    async def test_add_with_marks(self) -> None:
        """Test adding messages with marks."""
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        await self.memory.add(self.msgs[:2], marks=["important", "todo"])

        self.assertEqual(
            self.memory._knowledge_store.put_document.call_count,
            2,
        )

        # Verify marks are included in the document
        call_args = self.memory._knowledge_store.put_document.call_args_list
        for call in call_args:
            doc = call[0][0]
            marks = json.loads(doc.metadata["marks_json"])
            self.assertIn("important", marks)
            self.assertIn("todo", marks)

    async def test_add_no_duplicates(self) -> None:
        """Test that duplicate messages are filtered out."""
        # Mock get_documents to return existing documents for IDs "0" and "1"
        existing_docs = [
            MagicMock(
                document_id="0:::test_session",
            ),
            MagicMock(
                document_id="1:::test_session",
            ),
        ]
        self.memory._knowledge_store.get_documents = AsyncMock(
            return_value=existing_docs,
        )

        await self.memory.add(self.msgs[:5], allow_duplicates=False)

        # Only messages 2, 3, 4 should be added
        self.assertEqual(
            self.memory._knowledge_store.put_document.call_count,
            3,
        )

    async def test_add_allow_duplicates(self) -> None:
        """Test adding with allow_duplicates=True."""
        # When allow_duplicates=True, get_documents should not be called
        await self.memory.add(self.msgs[:5], allow_duplicates=True)

        self.memory._knowledge_store.get_documents.assert_not_called()

        # All 5 messages should be added
        self.assertEqual(
            self.memory._knowledge_store.put_document.call_count,
            5,
        )

        await self.memory.add(self.msgs[:5], allow_duplicates=True)
        # All 5 messages should be added
        self.assertEqual(
            self.memory._knowledge_store.put_document.call_count,
            10,
        )

    async def test_delete_messages(self) -> None:
        """Test deleting messages by ID."""
        self.memory._get_existing_msg_ids_in_session = AsyncMock(
            return_value={"0"},
        )

        deleted = await self.memory.delete(msg_ids=["0"])

        self.assertEqual(deleted, 1)
        self.memory._knowledge_store.delete_document.assert_called_once_with(
            document_id="0:::test_session",
            tenant_id="test_user",
        )

    async def test_delete_nonexistent(self) -> None:
        """Test deleting non-existent messages returns 0."""
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        deleted = await self.memory.delete(msg_ids=["nonexistent"])

        self.assertEqual(deleted, 0)
        self.memory._knowledge_store.delete_document.assert_not_called()

    async def test_get_memory_all(self) -> None:
        """Test getting all messages from memory."""
        docs = [
            _create_mock_document(
                self.msgs[i],
                user_id="test_user",
                session_id="test_session",
            )
            for i in range(5)
        ]
        self.memory._search_documents_by_marks_and_exclude_marks = AsyncMock(
            return_value=docs,
        )

        result = await self.memory.get_memory(prepend_summary=False)

        self.assertEqual(len(result), 5)
        for i, msg in enumerate(result):
            self.assertEqual(msg.id, str(i))
        mock = self.memory._search_documents_by_marks_and_exclude_marks
        mock.assert_called_once_with(
            marks=None,
            exclude_marks=None,
        )

    async def test_get_memory_with_mark_filter(self) -> None:
        """Test getting messages filtered by mark."""
        # When mark is provided, _search_documents_by_marks_and_exclude_marks
        # is used and only matching docs are returned from the database layer
        docs = [
            _create_mock_document(self.msgs[1], marks=["important"]),
            _create_mock_document(self.msgs[2], marks=["important", "todo"]),
        ]
        self.memory._search_documents_by_marks_and_exclude_marks = AsyncMock(
            return_value=docs,
        )

        result = await self.memory.get_memory(
            mark="important",
            prepend_summary=False,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, "1")
        self.assertEqual(result[1].id, "2")
        mock = self.memory._search_documents_by_marks_and_exclude_marks
        mock.assert_called_once_with(
            marks="important",
            exclude_marks=None,
        )

    async def test_get_memory_with_exclude_mark(self) -> None:
        """Test getting messages with excluded mark."""
        # exclude_mark filtering is now done at the database layer
        docs = [
            _create_mock_document(self.msgs[0], marks=[]),
            _create_mock_document(self.msgs[3], marks=[]),
        ]
        self.memory._search_documents_by_marks_and_exclude_marks = AsyncMock(
            return_value=docs,
        )

        result = await self.memory.get_memory(
            exclude_mark="important",
            prepend_summary=False,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, "0")
        self.assertEqual(result[1].id, "3")
        mock = self.memory._search_documents_by_marks_and_exclude_marks
        mock.assert_called_once_with(
            marks=None,
            exclude_marks="important",
        )

    async def test_get_memory_with_summary(self) -> None:
        """Test that compressed summary is prepended when available."""
        docs = [
            _create_mock_document(self.msgs[0]),
        ]
        self.memory._search_documents_by_marks_and_exclude_marks = AsyncMock(
            return_value=docs,
        )
        self.memory._compressed_summary = "Previous conversation summary."

        result = await self.memory.get_memory(prepend_summary=True)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0].content,
            "Previous conversation summary.",
        )
        self.assertEqual(result[1].id, "0")

    async def test_size(self) -> None:
        """Test getting the size of memory."""
        msg_ids = [MagicMock() for _ in range(7)]
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=msg_ids)

        result = await self.memory.size()

        self.assertEqual(result, 7)

    async def test_clear(self) -> None:
        """Test clearing all messages."""
        self.memory._search_msg_ids_by_marks = AsyncMock(
            return_value={"msg_0", "msg_1"},
        )

        await self.memory.clear()

        self.assertEqual(
            self.memory._knowledge_store.delete_document.call_count,
            2,
        )
        deleted_doc_ids = {
            call.kwargs["document_id"]
            for call in (
                self.memory._knowledge_store.delete_document.call_args_list
            )
        }
        self.assertEqual(
            deleted_doc_ids,
            {"msg_0:::test_session", "msg_1:::test_session"},
        )

    async def test_clear_empty(self) -> None:
        """Test clearing when memory is already empty."""
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        await self.memory.clear()

        self.memory._knowledge_store.delete_document.assert_not_called()

    async def test_delete_by_mark(self) -> None:
        """Test deleting messages by mark."""
        self.memory._search_msg_ids_by_marks = AsyncMock(
            return_value={"1", "2"},
        )

        deleted = await self.memory.delete_by_mark("important")

        self.assertEqual(deleted, 2)
        self.memory._search_msg_ids_by_marks.assert_called_once_with(
            ["important"],
        )
        self.assertEqual(
            self.memory._knowledge_store.delete_document.call_count,
            2,
        )

    async def test_delete_by_mark_list(self) -> None:
        """Test deleting messages by multiple marks."""
        self.memory._search_msg_ids_by_marks = AsyncMock(
            return_value={"1", "2"},
        )

        deleted = await self.memory.delete_by_mark(["important", "todo"])

        self.assertEqual(deleted, 2)
        self.memory._search_msg_ids_by_marks.assert_called_once_with(
            ["important", "todo"],
        )

    async def test_update_messages_mark_add(self) -> None:
        """Test adding a mark to messages."""
        # msg_ids is provided, so _get_existing_msg_ids_and_marks_in_session
        # is used
        self.memory._get_existing_msg_ids_and_marks_in_session = AsyncMock(
            return_value={"0": [], "1": []},
        )
        self.memory._knowledge_store.update_document = AsyncMock()

        updated = await self.memory.update_messages_mark(
            msg_ids=["0", "1"],
            new_mark="review",
        )

        self.assertEqual(updated, 2)
        mock = self.memory._get_existing_msg_ids_and_marks_in_session
        mock.assert_called_once_with(["0", "1"])
        self.assertEqual(
            self.memory._knowledge_store.update_document.call_count,
            2,
        )

    async def test_update_messages_mark_remove(self) -> None:
        """Test removing a mark from messages."""
        # msg_ids is provided, so _get_existing_msg_ids_and_marks_in_session
        # is used
        self.memory._get_existing_msg_ids_and_marks_in_session = AsyncMock(
            return_value={"0": ["important"]},
        )
        self.memory._knowledge_store.update_document = AsyncMock()

        updated = await self.memory.update_messages_mark(
            msg_ids=["0"],
            old_mark="important",
            new_mark=None,
        )

        self.assertEqual(updated, 1)
        mock = self.memory._get_existing_msg_ids_and_marks_in_session
        mock.assert_called_once_with(["0"])
        self.assertEqual(
            self.memory._knowledge_store.update_document.call_count,
            1,
        )

    async def test_update_messages_mark_replace(self) -> None:
        """Test replacing a mark on messages."""
        # msg_ids is provided, so _get_existing_msg_ids_and_marks_in_session
        # is used
        self.memory._get_existing_msg_ids_and_marks_in_session = AsyncMock(
            return_value={"0": ["important"], "1": ["important"]},
        )
        self.memory._knowledge_store.update_document = AsyncMock()

        updated = await self.memory.update_messages_mark(
            msg_ids=["0", "1"],
            old_mark="important",
            new_mark="archived",
        )

        self.assertEqual(updated, 2)
        mock = self.memory._get_existing_msg_ids_and_marks_in_session
        mock.assert_called_once_with(["0", "1"])
        self.assertEqual(
            self.memory._knowledge_store.update_document.call_count,
            2,
        )

    async def test_state_dict(self) -> None:
        """Test state_dict serialization."""
        self.memory._compressed_summary = "Test summary"

        state = self.memory.state_dict()

        self.assertEqual(state["_compressed_summary"], "Test summary")

    async def test_load_state_dict(self) -> None:
        """Test load_state_dict deserialization."""
        self.memory.load_state_dict(
            {
                "_compressed_summary": "Loaded summary",
            },
        )

        self.assertEqual(
            self.memory._compressed_summary,
            "Loaded summary",
        )

    async def test_close(self) -> None:
        """Test closing the Tablestore memory."""
        mock_store = self.memory._knowledge_store
        await self.memory.close()

        mock_store.close.assert_called_once()
        self.assertIsNone(self.memory._knowledge_store)
        self.assertFalse(self.memory._initialized)

    async def test_close_when_not_initialized(self) -> None:
        """Test closing when not initialized."""
        self.memory._knowledge_store = None
        self.memory._initialized = False

        # Should not raise
        await self.memory.close()

    async def test_msg_to_document_string_content(self) -> None:
        """Test converting a Msg with string content to document."""
        msg = Msg("Alice", "Hello world!", "user")

        doc = self.memory._msg_to_document(msg, ["mark1"])

        self.assertEqual(
            doc.document_id,
            f"{msg.id}:::test_session",
        )
        # Verify text contains full Msg JSON
        msg_dict = json.loads(doc.text)
        self.assertEqual(msg_dict["id"], msg.id)
        self.assertEqual(msg_dict["name"], "Alice")
        self.assertEqual(msg_dict["content"], "Hello world!")
        self.assertEqual(msg_dict["role"], "user")
        self.assertEqual(doc.tenant_id, "test_user")
        self.assertEqual(doc.metadata["name"], "Alice")
        self.assertEqual(doc.metadata["role"], "user")
        self.assertEqual(doc.metadata["session_id"], "test_session")
        marks = json.loads(doc.metadata["marks_json"])
        self.assertIn("mark1", marks)
        # Verify msg_json is NOT in metadata
        self.assertNotIn("msg_json", doc.metadata)
        # Verify old fields are removed
        self.assertNotIn("content_json", doc.metadata)
        self.assertNotIn("metadata_json", doc.metadata)

    async def test_msg_to_document_list_content(self) -> None:
        """Test converting a Msg with list content to document."""
        content = [{"type": "text", "text": "Hello from blocks!"}]
        msg = Msg("Bob", content, "assistant")

        doc = self.memory._msg_to_document(msg, [])

        # Verify text contains full Msg JSON with list content
        msg_dict = json.loads(doc.text)
        self.assertEqual(msg_dict["content"], content)
        self.assertEqual(doc.tenant_id, "test_user")
        # Verify msg_json is NOT in metadata
        self.assertNotIn("msg_json", doc.metadata)

    async def test_document_to_msg_roundtrip(self) -> None:
        """Test roundtrip conversion Msg -> Document -> Msg."""
        original_msg = Msg(
            "Alice",
            "Test content",
            "user",
            metadata={"key": "value", "number": 42},
        )
        original_marks = ["important", "todo"]

        doc = self.memory._msg_to_document(original_msg, original_marks)
        (
            restored_msg,
            restored_marks,
        ) = TablestoreMemory._document_to_msg_and_marks(doc)

        self.assertEqual(restored_msg.name, original_msg.name)
        self.assertEqual(restored_msg.content, original_msg.content)
        self.assertEqual(restored_msg.role, original_msg.role)
        self.assertEqual(restored_msg.id, original_msg.id)
        self.assertEqual(restored_msg.metadata, original_msg.metadata)
        self.assertListEqual(restored_marks, original_marks)

    async def test_invalid_mark_type(self) -> None:
        """Test that invalid mark types raise TypeError."""
        self.memory._search_msg_ids_by_marks = AsyncMock(return_value=set())

        with self.assertRaises(TypeError):
            await self.memory.add(self.msgs[0], marks=123)

    async def test_get_memory_invalid_mark_type(self) -> None:
        """Test that invalid mark type in get_memory raises TypeError."""
        with self.assertRaises(TypeError):
            await self.memory.get_memory(mark=123)

    async def test_make_document_id(self) -> None:
        """Test _make_document_id produces correct format."""
        document_id = self.memory._make_document_id("msg_123")
        self.assertEqual(document_id, "msg_123:::test_session")

    async def test_extract_msg_id(self) -> None:
        """Test _extract_msg_id extracts msg ID from document ID."""
        msg_id = self.memory._extract_msg_id("msg_123:::test_session")
        self.assertEqual(msg_id, "msg_123")

    async def test_extract_msg_id_invalid_suffix(self) -> None:
        """Test _extract_msg_id logs error for invalid suffix."""
        with self.assertLogs("as", level="ERROR") as log_context:
            msg_id = self.memory._extract_msg_id("msg_123:::wrong_session")
        self.assertEqual(msg_id, "msg_123:::wrong_session")
        self.assertTrue(
            any(
                "Unexpected document_id format" in m
                for m in log_context.output
            ),
        )

    async def test_extract_msg_id_no_separator(self) -> None:
        """Test _extract_msg_id logs error when no separator found."""
        with self.assertLogs("as", level="ERROR") as log_context:
            msg_id = self.memory._extract_msg_id("msg_123")
        self.assertEqual(msg_id, "msg_123")
        self.assertTrue(
            any(
                "Unexpected document_id format" in m
                for m in log_context.output
            ),
        )

    async def test_make_and_extract_roundtrip(self) -> None:
        """Test roundtrip of _make_document_id and _extract_msg_id."""
        original_id = "test_msg_id_456"
        document_id = self.memory._make_document_id(original_id)
        extracted_id = self.memory._extract_msg_id(document_id)
        self.assertEqual(extracted_id, original_id)

    async def test_delete_by_mark_invalid_type(self) -> None:
        """Test that invalid mark type in delete_by_mark raises TypeError."""
        with self.assertRaises(TypeError):
            await self.memory.delete_by_mark(123)
