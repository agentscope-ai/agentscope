# -*- coding: utf-8 -*-
"""Unittests for the AskUser tool."""
from unittest import IsolatedAsyncioTestCase

import jsonschema
from pydantic import ValidationError

from agentscope.message import ToolResultBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.tool import AskUser, AskUserMetadata, AskUserParams


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
