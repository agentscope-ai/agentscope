# -*- coding: utf-8 -*-
"""Tests for the structured user-input tool and console interaction."""
import json
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from agentscope.console._console import _request_user_input
from agentscope.event import RequireExternalExecutionEvent
from agentscope.message import ToolCallBlock, ToolCallState, ToolResultState
from agentscope.tool import RequestUserInput


def _pending_event() -> RequireExternalExecutionEvent:
    """Build one pending structured user-input request."""
    return RequireExternalExecutionEvent(
        reply_id="reply-1",
        tool_calls=[
            ToolCallBlock(
                id="call-1",
                name=RequestUserInput.name,
                input=json.dumps(
                    {
                        "question": "Which approach should be used?",
                        "options": [
                            {
                                "label": "Minimal",
                                "description": "Make the smallest change.",
                                "recommended": True,
                            },
                            {
                                "label": "Complete",
                                "description": "Implement the full design.",
                            },
                        ],
                    },
                ),
                state=ToolCallState.SUBMITTED,
            ),
        ],
    )


class RequestUserInputToolTest(TestCase):
    """Test the public contract of ``RequestUserInput``."""

    def test_tool_contract(self) -> None:
        """The tool exposes the complete bounded choice schema."""
        tool = RequestUserInput()
        self.assertEqual(
            {
                "name": tool.name,
                "is_concurrency_safe": tool.is_concurrency_safe,
                "is_read_only": tool.is_read_only,
                "is_external_tool": tool.is_external_tool,
                "input_schema": tool.input_schema,
            },
            {
                "name": "RequestUserInput",
                "is_concurrency_safe": False,
                "is_read_only": True,
                "is_external_tool": True,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 500,
                            "description": (
                                "The question the user must answer."
                            ),
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "description": (
                                "Mutually exclusive choices. Do not include "
                                "Other."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 80,
                                    },
                                    "description": {
                                        "type": "string",
                                        "maxLength": 300,
                                    },
                                    "recommended": {
                                        "type": "boolean",
                                        "default": False,
                                    },
                                },
                                "required": ["label"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["question", "options"],
                    "additionalProperties": False,
                },
            },
        )


class RequestUserInputConsoleTest(IsolatedAsyncioTestCase):
    """Test console collection of preset and custom answers."""

    async def test_invalid_payload_raises_value_error(self) -> None:
        """A corrupted pending event is rejected without a KeyError."""
        pending = RequireExternalExecutionEvent(
            reply_id="reply-1",
            tool_calls=[
                ToolCallBlock(
                    id="call-1",
                    name=RequestUserInput.name,
                    input=json.dumps({"question": "Missing options"}),
                    state=ToolCallState.SUBMITTED,
                ),
            ],
        )

        with self.assertRaisesRegex(
            ValueError,
            "Invalid RequestUserInput payload",
        ):
            await _request_user_input(pending)

    @patch("builtins.input", side_effect=["2"])
    async def test_selects_preset_option(self, _input: object) -> None:
        """A numbered option becomes a structured successful result."""
        result = await _request_user_input(_pending_event())
        block = result.execution_results[0]
        self.assertEqual(
            {
                "reply_id": result.reply_id,
                "id": block.id,
                "name": block.name,
                "state": block.state,
                "payload": json.loads(block.output[0].text),
            },
            {
                "reply_id": "reply-1",
                "id": "call-1",
                "name": "RequestUserInput",
                "state": ToolResultState.SUCCESS,
                "payload": {
                    "type": "option",
                    "option_index": 1,
                    "label": "Complete",
                },
            },
        )

    @patch("builtins.input", side_effect=["3", "", "Custom approach"])
    async def test_other_requires_non_empty_text(self, _input: object) -> None:
        """Other keeps prompting until the custom answer is non-empty."""
        result = await _request_user_input(_pending_event())
        block = result.execution_results[0]
        self.assertEqual(
            json.loads(block.output[0].text),
            {
                "type": "other",
                "text": "Custom approach",
            },
        )
