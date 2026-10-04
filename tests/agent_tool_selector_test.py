# -*- coding: utf-8 -*-
"""Tool selection in the Agent input and reasoning paths."""
# pylint: disable=protected-access
import asyncio
from typing import AsyncGenerator, Awaitable, Callable, Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils import MockModel
from pydantic import BaseModel

from agentscope.agent import Agent, ContextConfig
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    Toolkit,
    ToolChoice,
    ToolChunk,
    ToolGroup,
    ToolSelection,
    ToolSelectorBase,
)


def _tool(name: str) -> FunctionTool:
    """Build a harmless tool with a deterministic schema."""

    def run() -> ToolChunk:
        """Return a sample value."""
        return ToolChunk(content=[TextBlock(text="ok")])

    return FunctionTool(run, name=name)


class RecordingSelector(ToolSelectorBase):
    """Select the first candidate plus every required tool."""

    def __init__(self) -> None:
        """Record calls without modifying candidate schemas."""
        self.queries: list[str | None] = []
        self.required: list[set[str]] = []

    async def select(
        self,
        query: str | None,
        tools: list[dict],
        *,
        count_tokens: Callable[[list[dict]], Awaitable[int]],
        max_tokens: int | None = None,
        required_tools: Sequence[str] = (),
        tool_choice: ToolChoice | None = None,
    ) -> ToolSelection:
        """Follow explicit selection constraints and record the query."""
        self.queries.append(query)
        names = set(required_tools)
        if tool_choice:
            names.update(tool_choice.tools or [])
            if tool_choice.mode not in ("auto", "none", "required"):
                names.add(tool_choice.mode)
        self.required.append(names)
        if tools:
            names.add(tools[0]["function"]["name"])
        selected = [t for t in tools if t["function"]["name"] in names]
        tokens = await count_tokens(selected)
        if max_tokens is not None and tokens > max_tokens:
            raise ValueError("Required schema budget exceeded.")
        return ToolSelection(tools=selected, schema_tokens=tokens)


class AgentToolSelectorTest(IsolatedAsyncioTestCase):
    """Verify selection does not change registration or execution policy."""

    async def test_selection_precedes_compression_and_model_call(self) -> None:
        """Fifty registered tools do not trigger unnecessary compression."""
        model = MockModel(context_size=100)
        model.set_responses(
            [ChatResponse(content=[TextBlock(text="done")], is_last=True)],
        )

        async def count(messages: list[Msg], tools: list[dict]) -> int:
            return len(messages) + 10 * len(tools)

        model.count_tokens = AsyncMock(side_effect=count)
        model.generate_structured_output = AsyncMock()
        original_call = model._call_api
        model._call_api = AsyncMock(side_effect=original_call)
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "Helpful assistant",
            model,
            toolkit=Toolkit(tools=[_tool(f"tool_{i}") for i in range(50)]),
            tool_selector=selector,
            max_tool_schema_tokens=20,
        )
        await agent.reply(UserMsg("user", "Find weather"))
        self.assertEqual(len(selector.queries), 1)
        self.assertEqual(selector.queries[0], "Find weather")
        model.generate_structured_output.assert_not_called()
        self.assertEqual(len(model._call_api.call_args.kwargs["tools"]), 1)
        self.assertEqual(len(await agent.toolkit.get_tool_schemas()), 50)
        self.assertEqual(agent.last_tool_selection.schema_tokens, 10)
        self.assertIsNone(agent._selection_context)

    async def test_default_keeps_all_schemas(self) -> None:
        """Existing callers keep the complete eligible set."""
        agent = Agent(
            "assistant",
            "",
            MockModel(),
            toolkit=Toolkit(tools=[_tool("first"), _tool("second")]),
        )
        prepared = await agent._prepare_model_input()
        self.assertEqual(len(prepared["tools"]), 2)
        self.assertIsNone(agent.last_tool_selection)

    async def test_scope_reuses_selection_and_refreshes_candidates(
        self,
    ) -> None:
        """Compression cannot lose the query or resurrect removed tools."""
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            MockModel(),
            toolkit=Toolkit(tools=[_tool("first"), _tool("second")]),
            state=AgentState(context=[UserMsg("user", "original task")]),
            tool_selector=selector,
        )
        with agent._tool_selection_step(None):
            first = await agent._prepare_model_input()
            agent.state.context = []
            second = await agent._prepare_model_input()
            self.assertEqual(first["tools"], second["tools"])
            self.assertEqual(len(selector.queries), 1)
            await agent.toolkit.remove_tool("first")
            third = await agent._prepare_model_input()
            self.assertEqual(third["tools"][0]["function"]["name"], "second")
            self.assertEqual(selector.queries, ["original task"] * 2)
        self.assertIsNone(agent._selection_context)

    async def test_control_and_explicit_tools_are_required(self) -> None:
        """Group control, compression and forced names survive selection."""
        selector = RecordingSelector()
        toolkit = Toolkit(
            tools=[_tool("first"), _tool("forced"), _tool("pinned")],
            tool_groups=[
                ToolGroup(
                    name="extra",
                    description="Additional tools",
                    tools=[_tool("inactive")],
                ),
            ],
        )
        agent = Agent(
            "assistant",
            "",
            MockModel(context_size=10000),
            toolkit=toolkit,
            tool_selector=selector,
            required_tools=["pinned"],
            context_config=ContextConfig(compression_tool_enabled=True),
        )
        with agent._tool_selection_step(ToolChoice(mode="forced")):
            prepared = await agent._prepare_model_input()
        names = {t["function"]["name"] for t in prepared["tools"]}
        self.assertTrue({"forced", "pinned", "CompressContext"} <= names)
        self.assertIn(toolkit.builtin_meta_tool.tool.name, names)
        self.assertNotIn("inactive", names)

    async def test_changed_reasoning_constraint_invalidates_snapshot(
        self,
    ) -> None:
        """A late forced tool cannot be hidden by an earlier selection."""
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            MockModel(context_size=10000),
            toolkit=Toolkit(tools=[_tool("first"), _tool("forced")]),
            tool_selector=selector,
        )
        with agent._tool_selection_step(None):
            await agent._prepare_model_input()
            prepared = await agent._prepare_model_input(
                tool_choice=ToolChoice(mode="forced"),
            )
        self.assertEqual(len(selector.queries), 2)
        self.assertIn("forced", selector.required[-1])
        self.assertEqual(len(prepared["tools"]), 2)

    async def test_bad_budget_is_rejected(self) -> None:
        """Invalid configuration fails before any model request."""
        with self.assertRaises(ValueError):
            Agent("assistant", "", MockModel(), max_tool_schema_tokens=-1)

    async def test_next_step_uses_summarized_task(self) -> None:
        """Keep the task query after the last user input is compressed."""
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            MockModel(),
            toolkit=Toolkit(tools=[_tool("first")]),
            state=AgentState(context=[UserMsg("user", "Find weather")]),
            tool_selector=selector,
        )
        with agent._tool_selection_step(None):
            await agent._prepare_model_input()
            agent.state.context = []
            agent.state.summary = "The task is to find weather."
        with agent._tool_selection_step(None):
            await agent._prepare_model_input()
        self.assertEqual(
            selector.queries,
            ["Find weather", "The task is to find weather."],
        )

    async def test_selection_scope_closes_on_error_and_cancel(self) -> None:
        """Failed selection cannot leave a cached constraint for next reply."""
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            MockModel(),
            tool_selector=selector,
        )
        for error in (ValueError("invalid"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                selector.select = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)):
                    with agent._tool_selection_step(None):
                        await agent._prepare_model_input()
                self.assertIsNone(agent._selection_context)

    async def test_reasoning_middleware_can_force_an_unselected_tool(
        self,
    ) -> None:
        """Account for a middleware's constraint before the API request."""

        class ForceTool(MiddlewareBase):
            """Require a tool after the initial compression pass."""

            async def on_reasoning(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                async for event in next_handler(
                    tool_choice=ToolChoice(mode="forced"),
                ):
                    yield event

        model = MockModel(context_size=10000)
        model.set_responses(
            [ChatResponse(content=[TextBlock(text="done")], is_last=True)],
        )
        model._call_api = AsyncMock(side_effect=model._call_api)
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            model,
            toolkit=Toolkit(tools=[_tool("first"), _tool("forced")]),
            tool_selector=selector,
            middlewares=[ForceTool()],
        )
        await agent.reply(UserMsg("user", "task"))
        self.assertEqual(len(selector.queries), 2)
        names = {
            t["function"]["name"]
            for t in model._call_api.call_args.kwargs["tools"]
        }
        self.assertIn("forced", names)

    async def test_structured_output_tool_is_automatically_required(
        self,
    ) -> None:
        """Selection preserves the final-output protocol's control tool."""

        class Answer(BaseModel):
            """The requested final output."""

            answer: str

        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="structured",
                            name="GenerateStructuredOutput",
                            input='{"answer": "done"}',
                        ),
                    ],
                    is_last=True,
                ),
            ],
        )
        selector = RecordingSelector()
        agent = Agent(
            "assistant",
            "",
            model,
            toolkit=Toolkit(tools=[_tool("first")]),
            tool_selector=selector,
        )
        reply = await agent.reply(UserMsg("user", "task"), Answer)
        self.assertEqual(reply.structured_output, {"answer": "done"})
        self.assertIn("GenerateStructuredOutput", selector.required[-1])
