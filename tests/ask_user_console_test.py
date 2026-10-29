# -*- coding: utf-8 -*-
"""Tests for native AskUser console interaction."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.console._console import _ask_user, _request_user_input
from agentscope.event import RequireExternalExecutionEvent
from agentscope.message import ToolCallBlock, ToolCallState, ToolResultState
from agentscope.tool import AskUser


class AskUserConsoleTest(IsolatedAsyncioTestCase):
    """Test console collection of preset and custom answers."""

    async def test_single_choice_and_non_empty_other(self) -> None:
        """Single-select and explicit Other follow the native result format."""
        payload = {
            "questions": [
                {
                    "question": "Which approach?",
                    "header": "Approach",
                    "options": [
                        {"label": "A", "description": "First"},
                        {"label": "B", "description": "Second"},
                    ],
                },
            ],
        }
        for inputs, selected, other, text in [
            (["1,2", "2"], ["B"], None, "B"),
            (["3", "", "Custom"], [], "Custom", "Custom"),
        ]:
            with self.subTest(inputs=inputs), patch(
                "builtins.input",
                side_effect=inputs,
            ):
                self.assertEqual(
                    await _ask_user(json.dumps(payload)),
                    (
                        f"Which approach?\n{text}",
                        {
                            "answers": [
                                {
                                    "question": "Which approach?",
                                    "selected": selected,
                                    "other": other,
                                },
                            ],
                        },
                    ),
                )

    async def test_native_batch_returns_readable_output_and_metadata(
        self,
    ) -> None:
        """Native multi-select and free text obey the shared result schema."""
        payload = {
            "questions": [
                {
                    "question": "Which features?",
                    "header": "Features",
                    "multi_select": True,
                    "context": "Review the plan.",
                    "options": [
                        {"label": "A", "description": "First"},
                        {"label": "B", "description": "Second"},
                    ],
                },
                {
                    "question": "Which approach?",
                    "header": "Approach",
                    "options": [
                        {"label": "A", "description": "First"},
                        {"label": "B", "description": "Second"},
                    ],
                },
            ],
        }
        pending = RequireExternalExecutionEvent(
            reply_id="native-reply",
            tool_calls=[
                ToolCallBlock(
                    id="native-call",
                    name="AskUser",
                    input=json.dumps(payload),
                    state=ToolCallState.SUBMITTED,
                ),
            ],
        )
        with patch("builtins.input", side_effect=["9", "1,2", "My plan"]):
            result = await _request_user_input(pending)
        block = result.execution_results[0]
        await AskUser().check_external_result(block)
        self.assertEqual(
            {
                "reply_id": result.reply_id,
                "id": block.id,
                "name": block.name,
                "output": block.output,
                "metadata": block.metadata,
                "state": block.state,
            },
            {
                "reply_id": "native-reply",
                "id": "native-call",
                "name": "AskUser",
                "state": ToolResultState.SUCCESS,
                "output": "Which features?\nA, B\n\nWhich approach?\nMy plan",
                "metadata": {
                    "answers": [
                        {
                            "question": "Which features?",
                            "selected": ["A", "B"],
                            "other": None,
                        },
                        {
                            "question": "Which approach?",
                            "selected": [],
                            "other": "My plan",
                        },
                    ],
                },
            },
        )

    async def test_native_invalid_payload_does_not_prompt(self) -> None:
        """Malformed native requests reach the console interruption path."""
        pending = RequireExternalExecutionEvent(
            reply_id="native-reply",
            tool_calls=[
                ToolCallBlock(
                    id="native-call",
                    name="AskUser",
                    input="{}",
                ),
            ],
        )
        with patch("builtins.input") as prompt, self.assertRaises(ValueError):
            await _request_user_input(pending)
        prompt.assert_not_called()
