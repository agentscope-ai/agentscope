# -*- coding: utf-8 -*-
"""Message persistence and content-selection contracts."""
import json
from unittest import TestCase

from pydantic import ValidationError

from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    HintBlock,
    Msg,
    SystemMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    URLSource,
    Usage,
    UserMsg,
)
from agentscope.types import ReplyFinishedReason


class MessageSerializationTest(TestCase):
    """Exercise messages across the JSON storage boundary."""

    def test_mixed_assistant_content_survives_json_round_trip(self) -> None:
        """Restore concrete block types, nested data and provider fields."""
        image = DataBlock(
            source=Base64Source(data="aGVsbG8=", media_type="image/png"),
        )
        message = AssistantMsg(
            name="agent",
            content=[
                ThinkingBlock(thinking="reasoning", signature="provider-sig"),
                TextBlock(text="你好 🌍"),
                image,
                HintBlock(hint=[TextBlock(text="context"), image]),
                ToolCallBlock(id="call-1", name="render", input='{"n": 1}'),
                ToolResultBlock(
                    id="call-1",
                    name="render",
                    output=[
                        TextBlock(text="rendered"),
                        DataBlock(
                            source=URLSource(
                                url="https://example.com/chart.png",
                                media_type="image/png",
                            ),
                        ),
                    ],
                    state=ToolResultState.SUCCESS,
                    metadata={"duration": 2},
                ),
            ],
            metadata={"session": {"labels": ["demo"]}},
            structured_output={"answer": [1, 2]},
            usage=Usage(
                input_tokens=12,
                output_tokens=3,
                cache_input_tokens=4,
                cache_creation_input_tokens=2,
            ),
            finished_reason=ReplyFinishedReason.COMPLETED,
            finished_at="2026-01-01T00:00:01Z",
        )

        restored = Msg.model_validate_json(message.model_dump_json())

        self.assertEqual(restored, message)
        self.assertEqual(
            [type(block) for block in restored.content],
            [
                ThinkingBlock,
                TextBlock,
                DataBlock,
                HintBlock,
                ToolCallBlock,
                ToolResultBlock,
            ],
        )
        self.assertEqual(restored.get_text_content(), "你好 🌍")
        # A restored conversation can be edited without changing its source.
        restored.metadata["session"]["labels"].append("restored")
        self.assertEqual(message.metadata["session"]["labels"], ["demo"])

    def test_restoring_json_enforces_role_content_rules(self) -> None:
        """Stored assistant-only content cannot be restored as user/system."""
        message = AssistantMsg(
            name="agent",
            content=[ThinkingBlock(thinking="private reasoning")],
        )
        payload = json.loads(message.model_dump_json())
        for role in ("user", "system"):
            with self.subTest(role=role):
                payload["role"] = role
                with self.assertRaises(ValidationError):
                    Msg.model_validate_json(json.dumps(payload))

    def test_empty_and_textless_messages_remain_distinct(self) -> None:
        """An empty text block is different from having no text blocks."""
        for factory in (UserMsg, SystemMsg, AssistantMsg):
            for content, expected in (([], None), ("", "")):
                with self.subTest(factory=factory.__name__, content=content):
                    message = factory(name="sender", content=content)
                    restored = Msg.model_validate_json(
                        message.model_dump_json(),
                    )
                    self.assertEqual(restored.get_text_content(), expected)
                    self.assertEqual(
                        restored.has_content_blocks(),
                        isinstance(content, str),
                    )

    def test_factories_preserve_explicit_identity_and_timestamps(self) -> None:
        """Persistence IDs and timestamps must survive factory construction."""
        created_at = "2026-01-01T00:00:00Z"
        finished_at = "2026-01-01T00:00:01Z"
        for factory in (UserMsg, SystemMsg, AssistantMsg):
            with self.subTest(factory=factory.__name__):
                message = factory(
                    name="sender",
                    content="hello",
                    id="persisted-id",
                    created_at=created_at,
                    finished_at=finished_at,
                )
                self.assertEqual(message.id, "persisted-id")
                self.assertEqual(message.created_at, created_at)
                self.assertEqual(message.finished_at, finished_at)
                unfinished = factory(
                    name="sender",
                    content=[],
                    created_at=created_at,
                )
                self.assertEqual(
                    unfinished.finished_at,
                    None if factory is AssistantMsg else created_at,
                )

    def test_content_selection_preserves_order_and_original_message(
        self,
    ) -> None:
        """Selecting multiple block types keeps their conversation order."""
        message = AssistantMsg(
            name="agent",
            content=[
                TextBlock(text="first"),
                ThinkingBlock(thinking="hidden"),
                HintBlock(hint="context"),
                TextBlock(text="last"),
            ],
        )
        before = message.model_dump_json()

        self.assertEqual(
            message.get_text_content(separator=" | "),
            "first | last",
        )
        self.assertEqual(
            list(message.get_content_blocks(["hint", "text"])),
            [message.content[0], message.content[2], message.content[3]],
        )
        self.assertEqual(message.get_content_blocks("data"), [])
        self.assertEqual(message.get_content_blocks([]), [])
        self.assertTrue(message.has_content_blocks(["data", "hint"]))
        self.assertFalse(message.has_content_blocks([]))
        self.assertFalse(message.has_content_blocks("tool_call"))
        self.assertEqual(message.model_dump_json(), before)
