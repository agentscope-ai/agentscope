# -*- coding: utf-8 -*-
"""Unittests for the AskUser tool."""
import json
from unittest import IsolatedAsyncioTestCase

import jsonschema
from pydantic import ValidationError
from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import RequireExternalExecutionEvent
from agentscope.message import (
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.tool import AskUser, AskUserMetadata, AskUserParams, Toolkit


class AskUserTest(IsolatedAsyncioTestCase):
    """Test the AskUser tool."""

    def setUp(self) -> None:
        """Build the tool."""
        self.tool = AskUser()

    async def test_the_call_is_left_to_whoever_can_ask(self) -> None:
        """Nothing executes it here — the agent yields it instead."""
        self.assertTrue(self.tool.is_external_tool)
        with self.assertRaises(RuntimeError):
            await self.tool()

    async def test_it_never_asks_to_ask(self) -> None:
        """A confirmation in front of a question is a prompt about a
        prompt."""
        decision = await self.tool.check_permissions(
            {"questions": []},
            PermissionContext(),
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_a_question_needs_between_two_and_four_options(
        self,
    ) -> None:
        """One option is not a choice, and five is a menu."""
        option = {"label": "a", "description": "d"}
        for count in (1, 5):
            with self.assertRaises(ValidationError):
                AskUserParams.model_validate(
                    {
                        "questions": [
                            {
                                "question": "Which?",
                                "header": "Pick",
                                "options": [option] * count,
                            },
                        ],
                    },
                )

    async def test_a_header_stays_short_enough_to_be_a_chip(self) -> None:
        """It is rendered as a tag, so it cannot run on."""
        with self.assertRaises(ValidationError):
            AskUserParams.model_validate(
                {
                    "questions": [
                        {
                            "question": "Which?",
                            "header": "a header far too long to be a chip",
                            "options": [
                                {"label": "a", "description": "d"},
                                {"label": "b", "description": "d"},
                            ],
                        },
                    ],
                },
            )

    async def test_question_texts_must_be_unique(self) -> None:
        """Answers use the question text as their correlation key."""
        question = {
            "question": "Which?",
            "header": "Pick",
            "options": [
                {"label": "a", "description": "first"},
                {"label": "b", "description": "second"},
            ],
        }
        with self.assertRaises(ValidationError):
            AskUserParams.model_validate({"questions": [question, question]})

    async def test_option_labels_must_be_unique(self) -> None:
        """A selected label must identify exactly one option."""
        payload = {
            "questions": [
                {
                    "question": "Which?",
                    "header": "Pick",
                    "options": [
                        {"label": "same", "description": "first"},
                        {"label": "same", "description": "second"},
                    ],
                },
            ],
        }
        with self.assertRaises(ValidationError):
            AskUserParams.model_validate(payload)

    async def test_runtime_rejects_duplicates_before_external_execution(
        self,
    ) -> None:
        """Invalid calls return errors so the model can ask valid questions."""
        question = {
            "question": "Which?",
            "header": "Pick",
            "options": [
                {"label": "A", "description": "First"},
                {"label": "B", "description": "Second"},
            ],
        }
        valid_payload = {"questions": [question]}
        cases = [
            (
                {
                    "questions": [
                        {
                            **question,
                            "options": [
                                {"label": "A", "description": "First"},
                                {"label": "A", "description": "Second"},
                            ],
                        },
                    ],
                },
                "option labels must be unique within a question",
            ),
            (
                {"questions": [question, {**question, "header": "Again"}]},
                "question texts must be unique across the batch",
            ),
        ]
        for invalid_payload, error in cases:
            with self.subTest(error=error):
                # Schema validation alone cannot express these constraints.
                jsonschema.validate(invalid_payload, self.tool.input_schema)
                model = MockModel(context_size=10000)
                model.set_responses(
                    [
                        ChatResponse(
                            content=[
                                ToolCallBlock(
                                    id=call_id,
                                    name="AskUser",
                                    input=json.dumps(payload),
                                ),
                            ],
                            is_last=True,
                        )
                        for call_id, payload in (
                            ("invalid", invalid_payload),
                            ("corrected", valid_payload),
                        )
                    ],
                )
                agent = Agent(
                    name="Assistant",
                    system_prompt="Help the user.",
                    model=model,
                    toolkit=Toolkit(tools=[self.tool]),
                    injection_config=InjectionConfig(
                        inject_runtime_state=False,
                    ),
                )
                requests = [
                    event
                    async for event in agent.reply_stream(
                        UserMsg(name="user", content="Ask me."),
                    )
                    if isinstance(event, RequireExternalExecutionEvent)
                ]
                self.assertEqual(
                    [
                        (call.id, call.name, json.loads(call.input))
                        for request in requests
                        for call in request.tool_calls
                    ],
                    [("corrected", "AskUser", valid_payload)],
                )
                results = [
                    block
                    for message in agent.state.context
                    for block in message.content
                    if isinstance(block, ToolResultBlock)
                ]
                self.assertEqual(
                    [(block.id, block.name, block.state) for block in results],
                    [("invalid", "AskUser", ToolResultState.ERROR)],
                )
                self.assertIn(error, results[0].output)
                self.assertEqual(
                    [
                        call.id
                        for call in agent.state.get_unfinished_tool_calls(
                            agent.name,
                        )
                    ],
                    ["corrected"],
                )

    async def test_a_result_that_breaks_its_promise_is_refused(self) -> None:
        """The shape is the caller's to keep; prose alone is not enough."""
        result = ToolResultBlock(
            id="call-1",
            name="AskUser",
            output="通过",
            state=ToolResultState.SUCCESS,
        )
        with self.assertRaises(jsonschema.ValidationError):
            await self.tool.check_external_result(result)

        result.metadata = AskUserMetadata(
            answers=[
                {"question": "批准吗？", "selected": ["通过"], "other": None},
            ],
        ).model_dump()
        await self.tool.check_external_result(result)

    async def test_a_failed_result_owes_no_metadata(self) -> None:
        """The schema describes a successful run, not an error."""
        result = ToolResultBlock(
            id="call-1",
            name="AskUser",
            output="Invalid AskUser input",
            state=ToolResultState.ERROR,
        )
        await self.tool.check_external_result(result)

    async def test_the_schema_reaches_the_model_whole(self) -> None:
        """Nested models mean $defs, which the model layers inline."""
        schema = self.tool.input_schema
        self.assertListEqual(
            sorted(schema["$defs"]),
            ["_Option", "_Question"],
        )
        self.assertEqual(
            schema["properties"]["questions"]["items"]["$ref"],
            "#/$defs/_Question",
        )
