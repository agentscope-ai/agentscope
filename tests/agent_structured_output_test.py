# -*- coding: utf-8 -*-
"""Test the agent-level structured output."""
from typing import Any, Literal
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import BaseModel, ConfigDict, Field
from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.agent._structured_output_tool import _GenerateStructuredOutput
from agentscope.model import ChatResponse, ChatUsage
from agentscope.state import AgentState
from agentscope.tool import ToolBase, Toolkit, ToolChunk
from agentscope.permission import (
    PermissionDecision,
    PermissionBehavior,
    PermissionContext,
)
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.event import UserConfirmResultEvent, ConfirmResult


class WeatherReport(BaseModel):
    """The structured output schema used in the tests."""

    city: str
    temperature: float


class WeatherReportWithUnit(BaseModel):
    """The structured output schema with a defaulted field."""

    city: str
    unit: str = "celsius"


class FileResult(BaseModel):
    """A file result with a branch-specific default."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["file"]
    content: str = "empty"


class LinkResult(BaseModel):
    """A link result with a different branch-specific default."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["link"]
    url: str = "https://example.com"


class SearchResult(BaseModel):
    """A result whose alternatives must not share inserted defaults."""

    result: FileResult | LinkResult


class DiscriminatedSearchResult(BaseModel):
    """A result selected by its kind field."""

    result: FileResult | LinkResult = Field(discriminator="kind")


class MockConfirmTool(ToolBase):
    """A mock tool that requires user confirmation."""

    name: str = "mock_confirm_tool"
    description: str = "A mock tool requiring user confirmation"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Input string"},
        },
        "required": ["input"],
    }
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Mock tool requires user confirmation",
            message="Mock tool requires user confirmation",
        )

    # pylint: disable=redefined-builtin
    async def __call__(self, input: str, **kwargs: Any) -> ToolChunk:
        """Execute the tool."""
        return ToolChunk(
            content=[TextBlock(text=f"Confirm result: {input}")],
        )


class AgentStructuredOutputTest(IsolatedAsyncioTestCase):
    """Test the agent-level structured output."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.model = MockModel()
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(),
            # The runtime state injection is covered by agent_injection_test,
            # turn it off here to keep the assertions focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        self.structured_tool_call = ChatResponse(
            content=[
                ToolCallBlock(
                    id="structured_call_1",
                    name="GenerateStructuredOutput",
                    input='{"city": "Hangzhou", "temperature": 25.0}',
                ),
            ],
            is_last=True,
        )
        self.text_response = ChatResponse(
            content=[TextBlock(text="Let me think more.")],
            is_last=True,
        )

    async def test_structured_reply(self) -> None:
        """A structured reply calls the builtin tool and carries the
        validated output on the final message."""
        self.model.set_responses([self.structured_tool_call])

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )

        self.assertDictEqual(
            res.model_dump(),
            {
                "id": self.agent.state.reply_id,
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": "completed",
                "structured_output": {"city": "Hangzhou", "temperature": 25.0},
                "error": None,
                "metadata": {},
                "name": "Friday",
                "role": "assistant",
                "usage": None,
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": "The required structured output is generated.",
                    },
                ],
            },
        )
        self.assertDictEqual(
            self.agent.state.reply_context.model_dump(),
            {
                "reply_id": self.agent.state.reply_id,
                "cur_iter": 1,
                "structured_schema": WeatherReport.model_json_schema(),
                "structured_output": {"city": "Hangzhou", "temperature": 25.0},
            },
        )

    async def test_structured_reply_preserves_accumulated_usage(self) -> None:
        """A structured reply exposes usage from every model call."""
        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Let me think more.")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=80,
                        output_tokens=40,
                        time=0.1,
                        cache_input_tokens=5,
                        cache_creation_input_tokens=2,
                    ),
                ),
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_1",
                            name="GenerateStructuredOutput",
                            input=(
                                '{"city": "Hangzhou", ' '"temperature": 25.0}'
                            ),
                        ),
                    ],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=100,
                        output_tokens=50,
                        time=0.2,
                        cache_input_tokens=7,
                        cache_creation_input_tokens=3,
                    ),
                ),
            ],
        )

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )

        self.assertDictEqual(
            res.model_dump(),
            {
                "id": self.agent.state.reply_id,
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": "completed",
                "structured_output": {
                    "city": "Hangzhou",
                    "temperature": 25.0,
                },
                "error": None,
                "metadata": {},
                "name": "Friday",
                "role": "assistant",
                "usage": {
                    "input_tokens": 180,
                    "output_tokens": 90,
                    "cache_input_tokens": 12,
                    "cache_creation_input_tokens": 5,
                },
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": (
                            "The required structured output is generated."
                        ),
                    },
                ],
            },
        )
        self.assertEqual(self.model.cnt, 2)

    async def test_defaults_and_extra_fields(self) -> None:
        """In process the model class validates the output directly, so
        defaults are filled and extra fields are dropped."""
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_1",
                            name="GenerateStructuredOutput",
                            input='{"city": "Hangzhou", "note": "sunny"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReportWithUnit,
        )

        self.assertDictEqual(
            res.structured_output,
            {"city": "Hangzhou", "unit": "celsius"},
        )

    async def test_validation_error_retry(self) -> None:
        """An invalid structured output produces an error tool result, and
        the model retries in the next reasoning round."""
        valid_structured_tool_call = ChatResponse(
            content=[
                ToolCallBlock(
                    id="structured_call_1",
                    name="GenerateStructuredOutput",
                    input='{"city": "Hangzhou", "temperature": 25.0}',
                ),
            ],
            is_last=True,
            usage=ChatUsage(
                input_tokens=100,
                output_tokens=50,
                time=0.2,
                cache_input_tokens=7,
                cache_creation_input_tokens=3,
            ),
        )
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_0",
                            name="GenerateStructuredOutput",
                            input='{"city": "Hangzhou", "temperature": "hot"}',
                        ),
                    ],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=80,
                        output_tokens=40,
                        time=0.1,
                        cache_input_tokens=5,
                        cache_creation_input_tokens=2,
                    ),
                ),
                valid_structured_tool_call,
            ],
        )

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )

        self.assertDictEqual(
            res.structured_output,
            {"city": "Hangzhou", "temperature": 25.0},
        )
        assistant_msg = self.agent.state.context[-1]
        tool_results = [
            block.model_dump()
            for block in assistant_msg.get_content_blocks("tool_result")
        ]
        self.assertListEqual(
            tool_results,
            [
                {
                    "type": "tool_result",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "id": "structured_call_0",
                    "name": "GenerateStructuredOutput",
                    "output": "Input validation failed for tool "
                    "'GenerateStructuredOutput': 'hot' is not of type "
                    "'number' (at $.temperature)",
                    "state": "error",
                    "metadata": {},
                },
                {
                    "type": "tool_result",
                    "created_at": AnyString(),
                    "finished_at": None,
                    "id": "structured_call_1",
                    "name": "GenerateStructuredOutput",
                    "output": [
                        {
                            "type": "text",
                            "created_at": AnyString(),
                            "finished_at": None,
                            "id": AnyString(),
                            "text": "Structured output generated "
                            "successfully.",
                        },
                    ],
                    "state": "success",
                    "metadata": {},
                },
            ],
        )
        self.assertDictEqual(
            res.model_dump(),
            {
                "id": self.agent.state.reply_id,
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": "completed",
                "structured_output": {
                    "city": "Hangzhou",
                    "temperature": 25.0,
                },
                "error": None,
                "metadata": {},
                "name": "Friday",
                "role": "assistant",
                "usage": {
                    "input_tokens": 180,
                    "output_tokens": 90,
                    "cache_input_tokens": 12,
                    "cache_creation_input_tokens": 5,
                },
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": (
                            "The required structured output is generated."
                        ),
                    },
                ],
            },
        )

    async def test_forced_generation_at_max_iters(self) -> None:
        """Once ``max_iters`` is reached, the agent forces the structured
        output tool call within the grace iterations."""
        self.agent.react_config = ReActConfig(max_iters=1)
        self.model.set_responses(
            [self.text_response, self.structured_tool_call],
        )

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )

        self.assertDictEqual(
            res.structured_output,
            {"city": "Hangzhou", "temperature": 25.0},
        )
        self.assertEqual(res.finished_reason, "completed")
        self.assertEqual(self.model.cnt, 2)

    async def test_grace_iters_exhausted(self) -> None:
        """The reply exits with EXCEED_MAX_ITERS and no structured output
        when the grace iterations are exhausted."""
        self.agent.react_config = ReActConfig(
            max_iters=1,
            structured_output_grace_iters=1,
        )
        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="First attempt")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=80,
                        output_tokens=40,
                        time=0.1,
                        cache_input_tokens=5,
                        cache_creation_input_tokens=2,
                    ),
                ),
                ChatResponse(
                    content=[TextBlock(text="Second attempt")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=100,
                        output_tokens=50,
                        time=0.2,
                        cache_input_tokens=7,
                        cache_creation_input_tokens=3,
                    ),
                ),
            ],
        )

        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )

        self.assertDictEqual(
            res.model_dump(),
            {
                "id": self.agent.state.reply_id,
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": "exceed_max_iters",
                "structured_output": None,
                "error": None,
                "metadata": {},
                "name": "Friday",
                "role": "assistant",
                "usage": {
                    "input_tokens": 180,
                    "output_tokens": 90,
                    "cache_input_tokens": 12,
                    "cache_creation_input_tokens": 5,
                },
                "content": [
                    {
                        "type": "text",
                        "created_at": AnyString(),
                        "finished_at": None,
                        "id": AnyString(),
                        "text": "The maximum reasoning-acting iterations "
                        "are exceeded.",
                    },
                ],
            },
        )

    async def test_tool_mount_and_unmount(self) -> None:
        """The builtin tool is mounted for structured replies (without
        duplicate warnings) and unmounted for plain replies."""
        # The agent mutates the response blocks in place (e.g. the tool
        # call state), so each reply needs a freshly built response
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_1",
                            name="GenerateStructuredOutput",
                            input='{"city": "Hangzhou", "temperature": 25.0}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        with self.assertNoLogs("as", "WARNING"):
            await self.agent.reply(
                UserMsg(name="user", content="Weather in Hangzhou?"),
                structured_schema=WeatherReport,
            )
        self.assertIsNotNone(
            await self.agent.toolkit.get_tool("GenerateStructuredOutput"),
        )

        # A second structured reply re-mounts the tool without warnings
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_2",
                            name="GenerateStructuredOutput",
                            input='{"city": "Shanghai", "temperature": 28.0}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        with self.assertNoLogs("as", "WARNING"):
            await self.agent.reply(
                UserMsg(name="user", content="Weather in Shanghai?"),
                structured_schema=WeatherReport,
            )

        # A plain reply unmounts the tool
        self.model.set_responses(
            [ChatResponse(content=[TextBlock(text="Hello!")], is_last=True)],
        )
        await self.agent.reply(UserMsg(name="user", content="Hi"))
        self.assertIsNone(
            await self.agent.toolkit.get_tool("GenerateStructuredOutput"),
        )

    async def test_schema_survives_state_serialization(self) -> None:
        """The schema persists across HITL park, state dump/load and resume
        as a JSON schema dict, which fills defaults and keeps extras."""
        self.agent.toolkit = Toolkit(tools=[MockConfirmTool()])
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="confirm_call_1",
                            name="mock_confirm_tool",
                            input='{"input": "test"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )

        # The reply parks on the user confirmation
        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReportWithUnit,
        )
        self.assertIsNone(res.finished_reason)

        # Reload the state from its JSON dump into a new agent
        restored_state = AgentState.model_validate_json(
            self.agent.state.model_dump_json(),
        )
        self.assertDictEqual(
            restored_state.reply_context.structured_schema,
            WeatherReportWithUnit.model_json_schema(),
        )

        model = MockModel()
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_call_1",
                            name="GenerateStructuredOutput",
                            input='{"city": "Hangzhou", "note": "sunny"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[MockConfirmTool()]),
            state=restored_state,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

        # Resume WITHOUT re-providing the schema
        res = await agent.reply(
            UserConfirmResultEvent(
                reply_id=restored_state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=ToolCallBlock(
                            id="confirm_call_1",
                            name="mock_confirm_tool",
                            input='{"input": "test"}',
                        ),
                    ),
                ],
            ),
        )
        self.assertEqual(res.finished_reason, "completed")
        self.assertDictEqual(
            res.structured_output,
            {"city": "Hangzhou", "note": "sunny", "unit": "celsius"},
        )

    async def test_union_defaults_survive_state_serialization(self) -> None:
        """A resumed reply fills only the selected union branch's defaults."""
        for model in (SearchResult, DiscriminatedSearchResult):
            for kind in ("file", "link"):
                with self.subTest(model=model.__name__, kind=kind):
                    self.agent.state.reply_context.structured_schema = model
                    restored_state = AgentState.model_validate_json(
                        self.agent.state.model_dump_json(),
                    )
                    tool = _GenerateStructuredOutput(
                        schema=restored_state.reply_context.structured_schema,
                    )
                    chunk = await tool(
                        _agent_state=restored_state,
                        result={"kind": kind},
                    )
                    self.assertEqual(chunk.state, "success", chunk.content)
                    self.assertDictEqual(
                        restored_state.reply_context.structured_output,
                        model.model_validate(
                            {"result": {"kind": kind}},
                        ).model_dump(mode="json"),
                    )

    async def test_invalid_union_does_not_record_output(self) -> None:
        """An invalid alternative is still rejected after a state reload."""
        for model in (SearchResult, DiscriminatedSearchResult):
            with self.subTest(model=model.__name__):
                self.agent.state.reply_context.structured_schema = model
                restored_state = AgentState.model_validate_json(
                    self.agent.state.model_dump_json(),
                )
                tool = _GenerateStructuredOutput(
                    schema=restored_state.reply_context.structured_schema,
                )
                chunk = await tool(
                    _agent_state=restored_state,
                    result={"kind": "unknown"},
                )
                self.assertEqual(chunk.state, "error")
                self.assertIsNone(
                    restored_state.reply_context.structured_output,
                )

    async def test_ambiguous_one_of_is_rejected(self) -> None:
        """Defaults must not hide an ambiguous match between alternatives."""
        schema = {
            "type": "object",
            "properties": {
                "result": {
                    "oneOf": [
                        {
                            "type": "object",
                            "properties": {name: {"default": value}},
                            "additionalProperties": False,
                        }
                        for name, value in (("content", "empty"), ("url", ""))
                    ],
                },
            },
        }
        self.agent.state.reply_context.structured_schema = schema
        tool = _GenerateStructuredOutput(schema=schema)
        chunk = await tool(_agent_state=self.agent.state, result={})
        self.assertEqual(chunk.state, "error")
        self.assertIsNone(self.agent.state.reply_context.structured_output)

    async def test_union_reply_after_confirmation_reload(self) -> None:
        """A parked union reply completes after confirmation and reload."""
        self.agent.toolkit = Toolkit(tools=[MockConfirmTool()])
        confirm_call = ToolCallBlock(
            id="confirm_union",
            name="mock_confirm_tool",
            input='{"input": "search"}',
        )
        self.model.set_responses(
            [ChatResponse(content=[confirm_call], is_last=True)],
        )
        parked = await self.agent.reply(
            UserMsg(name="user", content="Find a link"),
            structured_schema=SearchResult,
        )
        self.assertIsNone(parked.finished_reason)
        restored_state = AgentState.model_validate_json(
            self.agent.state.model_dump_json(),
        )
        model = MockModel()
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured_union",
                            name="GenerateStructuredOutput",
                            input='{"result": {"kind": "link"}}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[MockConfirmTool()]),
            state=restored_state,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        result = await agent.reply(
            UserConfirmResultEvent(
                reply_id=restored_state.reply_id,
                confirm_results=[
                    ConfirmResult(confirmed=True, tool_call=confirm_call),
                ],
            ),
        )
        self.assertEqual(result.finished_reason, "completed")
        self.assertDictEqual(
            result.structured_output,
            {"result": {"kind": "link", "url": "https://example.com"}},
        )

    async def test_schema_ignored_on_resume(self) -> None:
        """A schema given with a HITL resume event only warns, and the
        reply continues with the parked schema."""
        self.agent.toolkit = Toolkit(tools=[MockConfirmTool()])
        self.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="confirm_call_1",
                            name="mock_confirm_tool",
                            input='{"input": "test"}',
                        ),
                    ],
                    is_last=True,
                ),
                self.structured_tool_call,
            ],
        )
        res = await self.agent.reply(
            UserMsg(name="user", content="Weather in Hangzhou?"),
            structured_schema=WeatherReport,
        )
        self.assertIsNone(res.finished_reason)

        with self.assertLogs("as", "WARNING"):
            res = await self.agent.reply(
                UserConfirmResultEvent(
                    reply_id=self.agent.state.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            confirmed=True,
                            tool_call=ToolCallBlock(
                                id="confirm_call_1",
                                name="mock_confirm_tool",
                                input='{"input": "test"}',
                            ),
                        ),
                    ],
                ),
                structured_schema=WeatherReportWithUnit,
            )
        self.assertEqual(res.finished_reason, "completed")
        self.assertDictEqual(
            res.structured_output,
            {"city": "Hangzhou", "temperature": 25.0},
        )
