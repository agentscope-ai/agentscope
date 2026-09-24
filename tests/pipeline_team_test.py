# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Test the team pipeline."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    ConfirmResult,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.pipeline import TeamMember, TeamPipeline
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class AskTool(ToolBase):
    """A tool that asks the user before running."""

    name: str = "ask_tool"
    description: str = "A tool that needs confirmation"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always ask."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Needs confirmation",
        )

    async def call(self, input: str) -> ToolChunk:
        """Echo the input."""
        return ToolChunk(content=[TextBlock(text=f"ask result: {input}")])


def _tool_call(
    id: str,
    input: str,
    name: str = "TeamAssign",
) -> list[ChatResponse]:
    """A streamed model reply that calls one tool."""
    block = ToolCallBlock(id=id, name=name, input=input)
    return [
        ChatResponse(content=[block], is_last=False, usage=None),
        ChatResponse(content=[block], is_last=True, usage=None),
    ]


def _text(text: str) -> list[ChatResponse]:
    """A streamed model reply with plain text."""
    return [
        ChatResponse(
            content=[TextBlock(text=text)],
            is_last=False,
            usage=None,
        ),
        ChatResponse(content=[TextBlock(text=text)], is_last=True, usage=None),
    ]


def _agent(name: str, tools: list[ToolBase] | None = None) -> Agent:
    """An agent driven by its own mock model."""
    return Agent(
        name=name,
        system_prompt=f"You are {name}.",
        model=MockModel(),
        toolkit=Toolkit(tools=tools),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )


class TeamPipelineTest(IsolatedAsyncioTestCase):
    """The team pipeline test case."""

    async def asyncSetUp(self) -> None:
        """Build a leader with a researcher it can delegate to."""
        self.leader = _agent("leader")
        self.researcher = _agent("researcher", [AskTool()])
        self.pipeline = TeamPipeline(
            leader=self.leader,
            members=[
                TeamMember(agent=self.researcher, description="Researches"),
            ],
        )
        self.query = UserMsg(name="user", content="What is A?")

    async def _run(self, inputs: Any) -> list[tuple[str, str]]:
        """Drain one run into (event type, owner) pairs."""
        events = [_ async for _ in self.pipeline.reply_stream(inputs)]
        agents = [
            self.leader,
            *[_.agent for _ in self.pipeline.members.values()],
        ]
        owners = {_.state.reply_id: _.name for _ in agents}
        return [(_.type.value, owners[_.reply_id]) for _ in events]

    def _msg_base(self, name: str) -> dict:
        """The fields shared by every assistant message."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "finished_at": None,
            "finished_reason": None,
            "structured_output": None,
            "error": None,
            "metadata": {},
            "name": name,
            "role": "assistant",
            "usage": None,
        }

    def _user_msg(self, text: str) -> dict:
        """The dumped user message carrying the given text."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "finished_at": AnyString(),
            "finished_reason": None,
            "structured_output": None,
            "error": None,
            "metadata": {},
            "name": "user",
            "role": "user",
            "usage": None,
            "content": [
                {
                    "type": "text",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "id": AnyString(),
                    "text": text,
                },
            ],
        }

    async def test_delegation_round_trip(self) -> None:
        """The researcher's final reply becomes the leader's tool result,
        its intermediate events are streamed, and its context is reset once
        the leader's reply ends."""
        self.leader.model.set_responses(
            [
                _tool_call(
                    "call-1",
                    '{"member": "researcher", "prompt": "Find A"}',
                ),
                _text("A is 42."),
            ],
        )
        self.researcher.model.set_responses([_text("Found: A = 42")])

        events = await self._run(self.query)

        self.assertListEqual(
            events,
            [
                ("REPLY_START", "leader"),
                ("MODEL_CALL_START", "leader"),
                ("TOOL_CALL_START", "leader"),
                ("TOOL_CALL_DELTA", "leader"),
                ("TOOL_CALL_END", "leader"),
                ("MODEL_CALL_END", "leader"),
                ("TOOL_RESULT_START", "leader"),
                ("REPLY_START", "researcher"),
                ("MODEL_CALL_START", "researcher"),
                ("TEXT_BLOCK_START", "researcher"),
                ("TEXT_BLOCK_DELTA", "researcher"),
                ("TEXT_BLOCK_END", "researcher"),
                ("MODEL_CALL_END", "researcher"),
                ("REPLY_END", "researcher"),
                ("TOOL_RESULT_TEXT_DELTA", "leader"),
                ("TOOL_RESULT_END", "leader"),
                ("MODEL_CALL_START", "leader"),
                ("TEXT_BLOCK_START", "leader"),
                ("TEXT_BLOCK_DELTA", "leader"),
                ("TEXT_BLOCK_END", "leader"),
                ("MODEL_CALL_END", "leader"),
                ("REPLY_END", "leader"),
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in self.leader.state.context],
            [
                self._user_msg("What is A?"),
                {
                    **self._msg_base("leader"),
                    "content": [
                        {
                            "type": "tool_call",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": "call-1",
                            "name": "TeamAssign",
                            "input": '{"member": "researcher", '
                            '"prompt": "Find A"}',
                            "state": "finished",
                            "suggested_rules": [],
                        },
                        {
                            "type": "tool_result",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": "call-1",
                            "name": "TeamAssign",
                            "output": [
                                {
                                    "type": "text",
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                    "id": AnyString(),
                                    "text": "Found: A = 42",
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                        },
                        {
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": AnyString(),
                            "text": "A is 42.",
                        },
                    ],
                },
            ],
        )
        self.assertListEqual(self.researcher.state.context, [])

    async def test_member_hitl_round_trip(self) -> None:
        """A researcher parked on confirmation ends the stream with the
        leader parked on the delegation; the confirmation is routed back
        and the leader continues with the researcher's reply."""
        self.pipeline.reset_members = False
        self.leader.model.set_responses(
            [
                _tool_call(
                    "call-1",
                    '{"member": "researcher", "prompt": "Find A"}',
                ),
                _text("A is 42."),
            ],
        )
        self.researcher.model.set_responses(
            [
                _tool_call("call-r1", '{"input": "A"}', name="ask_tool"),
                _text("Found: A = 42"),
            ],
        )

        events = await self._run(self.query)

        self.assertListEqual(
            events[6:],
            [
                ("TOOL_RESULT_START", "leader"),
                ("REPLY_START", "researcher"),
                ("MODEL_CALL_START", "researcher"),
                ("TOOL_CALL_START", "researcher"),
                ("TOOL_CALL_DELTA", "researcher"),
                ("TOOL_CALL_END", "researcher"),
                ("MODEL_CALL_END", "researcher"),
                ("REQUIRE_USER_CONFIRM", "researcher"),
            ],
        )
        self.assertListEqual(
            [
                (_.name, _.state)
                for _ in self.leader.state.get_awaiting_tool_calls("leader")
            ],
            [("TeamAssign", "submitted")],
        )
        self.assertListEqual(
            [
                (_.name, _.state)
                for _ in self.researcher.state.get_awaiting_tool_calls(
                    "researcher",
                )
            ],
            [("ask_tool", "asking")],
        )

        events = await self._run(
            UserConfirmResultEvent(
                reply_id=self.researcher.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=self.researcher.state.context[-1].content[0],
                    ),
                ],
            ),
        )

        self.assertListEqual(
            events,
            [
                ("TOOL_RESULT_START", "researcher"),
                ("TOOL_RESULT_TEXT_DELTA", "researcher"),
                ("TOOL_RESULT_END", "researcher"),
                ("MODEL_CALL_START", "researcher"),
                ("TEXT_BLOCK_START", "researcher"),
                ("TEXT_BLOCK_DELTA", "researcher"),
                ("TEXT_BLOCK_END", "researcher"),
                ("MODEL_CALL_END", "researcher"),
                ("REPLY_END", "researcher"),
                ("TOOL_RESULT_TEXT_DELTA", "leader"),
                ("TOOL_RESULT_END", "leader"),
                ("MODEL_CALL_START", "leader"),
                ("TEXT_BLOCK_START", "leader"),
                ("TEXT_BLOCK_DELTA", "leader"),
                ("TEXT_BLOCK_END", "leader"),
                ("MODEL_CALL_END", "leader"),
                ("REPLY_END", "leader"),
            ],
        )
        self.assertListEqual(
            [_.model_dump() for _ in self.researcher.state.context],
            [
                self._user_msg("Find A"),
                {
                    **self._msg_base("researcher"),
                    "content": [
                        {
                            "type": "tool_call",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": "call-r1",
                            "name": "ask_tool",
                            "input": '{"input": "A"}',
                            "state": "finished",
                            "suggested_rules": [
                                {
                                    "tool_name": "ask_tool",
                                    "rule_content": None,
                                    "behavior": PermissionBehavior.ALLOW,
                                    "source": "suggested",
                                },
                            ],
                        },
                        {
                            "type": "tool_result",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": "call-r1",
                            "name": "ask_tool",
                            "output": [
                                {
                                    "type": "text",
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                    "id": AnyString(),
                                    "text": "ask result: A",
                                },
                            ],
                            "state": "success",
                            "metadata": {},
                        },
                        {
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": AnyString(),
                            "text": "Found: A = 42",
                        },
                    ],
                },
            ],
        )
        self.assertEqual(
            self.leader.state.context[-1].get_text_content(),
            "A is 42.",
        )

    async def test_concurrent_delegations(self) -> None:
        """Delegations to different members in one round run together and
        land in the leader's context in call order."""
        coder = _agent("coder")
        self.pipeline = TeamPipeline(
            leader=self.leader,
            members=[
                TeamMember(agent=self.researcher, description="Researches"),
                TeamMember(agent=coder, description="Codes"),
            ],
        )
        self.leader.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="call-1",
                                name="TeamAssign",
                                input='{"member": "researcher", '
                                '"prompt": "Find A"}',
                            ),
                            ToolCallBlock(
                                id="call-2",
                                name="TeamAssign",
                                input='{"member": "coder", '
                                '"prompt": "Write A"}',
                            ),
                        ],
                        is_last=True,
                        usage=None,
                    ),
                ],
                _text("Both done."),
            ],
        )
        self.researcher.model.set_responses([_text("Found: A = 42")])
        coder.model.set_responses([_text("Wrote a.py")])

        events = await self._run(self.query)

        self.assertListEqual(
            sorted(owner for kind, owner in events if kind == "REPLY_START"),
            ["coder", "leader", "researcher"],
        )
        self.assertListEqual(
            [
                (_.id, _.name, _.output[0].text, _.state)
                for _ in self.leader.state.context[-1].get_content_blocks(
                    "tool_result",
                )
            ],
            [
                ("call-1", "TeamAssign", "Found: A = 42", "success"),
                ("call-2", "TeamAssign", "Wrote a.py", "success"),
            ],
        )
        self.assertEqual(
            self.leader.state.context[-1].get_text_content(),
            "Both done.",
        )

    async def test_interrupt_parked_member(self) -> None:
        """An interrupt closes the parked researcher and the leader's
        delegation alike, and resets the researcher afterwards."""
        self.leader.model.set_responses(
            [
                _tool_call(
                    "call-1",
                    '{"member": "researcher", "prompt": "Find A"}',
                ),
            ],
        )
        self.researcher.model.set_responses(
            [_tool_call("call-r1", '{"input": "A"}', name="ask_tool")],
        )
        await self._run(self.query)

        events = await self._run(
            UserInterruptEvent(reply_id=self.researcher.state.reply_id),
        )

        self.assertListEqual(
            events,
            [
                ("TOOL_RESULT_START", "researcher"),
                ("TOOL_RESULT_TEXT_DELTA", "researcher"),
                ("TOOL_RESULT_END", "researcher"),
                ("REPLY_END", "researcher"),
                ("TOOL_RESULT_TEXT_DELTA", "leader"),
                ("TOOL_RESULT_END", "leader"),
                ("REPLY_END", "leader"),
            ],
        )
        self.assertListEqual(
            [
                (_.id, _.state)
                for _ in self.leader.state.context[-1].get_content_blocks(
                    "tool_result",
                )
            ],
            [("call-1", "interrupted")],
        )
        self.assertListEqual(self.researcher.state.context, [])
