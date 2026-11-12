# -*- coding: utf-8 -*-
"""Tests for native AskUser console interaction."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.console import launch_console
from agentscope.console._console import _ask_user, _request_user_input
from agentscope.event import RequireExternalExecutionEvent
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import ChatResponse
from agentscope.tool import AskUser, Toolkit


def _concurrent_agent() -> Agent:
    """Create an agent that asks two questions before its final answer."""
    model = MockModel(context_size=10000)
    calls = [
        ToolCallBlock(
            id=f"call-{index}",
            name="AskUser",
            input=json.dumps(
                {
                    "questions": [
                        {
                            "question": f"Question {index}?",
                            "header": "Pick",
                            "options": [
                                {"label": "A", "description": "First"},
                                {"label": "B", "description": "Second"},
                            ],
                        },
                    ],
                },
            ),
        )
        for index in (1, 2)
    ]
    model.set_responses(
        [
            [ChatResponse(content=calls, is_last=True)],
            [ChatResponse(content=[TextBlock(text="Done")], is_last=True)],
        ],
    )
    return Agent(
        name="Assistant",
        system_prompt="You are a helpful assistant.",
        model=model,
        toolkit=Toolkit(tools=[AskUser()]),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )


class AskUserConsoleTest(IsolatedAsyncioTestCase):
    """Test console collection of preset and custom answers."""

    async def test_concurrent_questions_resume_to_completion(self) -> None:
        """Every concurrent question receives its own answer in order."""
        agent = _concurrent_agent()
        with patch(
            "builtins.input",
            side_effect=["hello", "1", "2", "exit"],
        ) as prompt:
            await launch_console(agent, verbosity="quiet")
        self.assertEqual(prompt.call_count, 4)
        self.assertEqual(
            [
                (block.id, block.state, block.output, block.metadata)
                for message in agent.state.context
                for block in message.content
                if isinstance(block, ToolResultBlock)
            ],
            [
                (
                    f"call-{index}",
                    ToolResultState.SUCCESS,
                    f"Question {index}?\n{answer}",
                    {
                        "answers": [
                            {
                                "question": f"Question {index}?",
                                "selected": [answer],
                                "other": None,
                            },
                        ],
                    },
                )
                for index, answer in ((1, "A"), (2, "B"))
            ],
        )
        self.assertEqual(agent.state.get_unfinished_tool_calls(agent.name), [])
        self.assertEqual(agent.state.context[-1].get_text_content(), "Done")

    async def test_interruption_discards_remaining_questions(self) -> None:
        """Aborted questions do not leak into the next user message."""
        for error in (EOFError, KeyboardInterrupt, ValueError):
            with self.subTest(error=error):
                agent = _concurrent_agent()
                with patch(
                    "builtins.input",
                    side_effect=["hello", error(), "continue", "exit"],
                ) as prompt:
                    await launch_console(agent, verbosity="quiet")
                self.assertEqual(prompt.call_count, 4)
                self.assertEqual(
                    [
                        (block.id, block.state)
                        for message in agent.state.context
                        for block in message.content
                        if isinstance(block, ToolResultBlock)
                    ],
                    [
                        ("call-1", ToolResultState.INTERRUPTED),
                        ("call-2", ToolResultState.INTERRUPTED),
                    ],
                )
                self.assertEqual(
                    agent.state.get_unfinished_tool_calls(agent.name),
                    [],
                )
                self.assertEqual(
                    agent.state.context[-1].get_text_content(),
                    "Done",
                )

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
