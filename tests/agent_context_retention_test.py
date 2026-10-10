# -*- coding: utf-8 -*-
"""Transactional context retention in the Agent compression pipeline."""
# pylint: disable=protected-access
import asyncio
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils import MockModel

from agentscope.agent import Agent, ContextConfig, PinnedContextRetentionPolicy
from agentscope.message import (
    AssistantMsg,
    DataBlock,
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    URLSource,
    UserMsg,
)
from agentscope.model import StructuredResponse
from agentscope.state import AgentState


def _agent(max_pinned_tokens: int = 20, summary: str = "summary") -> Agent:
    """Make compression deterministic without a network request."""
    model = MockModel(context_size=100)

    async def count(messages: list[Msg], tools: list[dict]) -> int:
        """Use characters as synthetic tokens, plus schema overhead."""
        return sum(len(m.get_text_content() or "") for m in messages) + len(
            tools,
        )

    model.count_tokens = AsyncMock(side_effect=count)
    model.generate_structured_output = AsyncMock(
        return_value=StructuredResponse(content={"summary": summary}),
    )
    return Agent(
        "assistant",
        "sys",
        model,
        state=AgentState(
            context=[
                UserMsg(
                    "user",
                    "key fact",
                    id="pinned",
                    metadata={"context_retention": True},
                ),
                AssistantMsg("assistant", "x" * 120, id="old"),
                UserMsg("user", "recent", id="recent"),
            ],
        ),
        context_config=ContextConfig(
            trigger_ratio=0.7,
            reserve_ratio=0.4,
            compression_prompt="Summarize",
            summary_template="{summary}",
            summary_schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        ),
        context_retention_policy=PinnedContextRetentionPolicy(
            max_pinned_tokens=max_pinned_tokens,
        ),
    )


class AgentContextRetentionTest(IsolatedAsyncioTestCase):
    """Check state preservation across failure and repeated compression."""

    async def test_pin_survives_repeated_compression_and_restore(self) -> None:
        """Original evidence remains available after multiple summaries."""
        agent = _agent()
        pinned = deepcopy(agent.state.context[0])
        for _ in range(3):
            await agent.compress_context()
            self.assertEqual(agent.state.summary, "summary")
            self.assertEqual(agent.state.context[0], pinned)
            self.assertEqual(agent.state.context[-1].id, "recent")
            agent.state.context.insert(
                1,
                AssistantMsg("assistant", "x" * 120),
            )
        restored = AgentState.model_validate_json(
            agent.state.model_dump_json(),
        )
        agent.state = restored
        await agent.compress_context()
        self.assertEqual(agent.state.context[0], pinned)

    async def test_pin_overflow_keeps_images_summary_and_state(self) -> None:
        """Image limiting on a prepared copy cannot leak through an error."""
        agent = _agent(max_pinned_tokens=1)
        agent.state.context[1].content.append(
            DataBlock(
                source=URLSource(
                    url="https://example.org/image.png",
                    media_type="image/png",
                ),
            ),
        )
        agent.context_config.max_image_num = 0
        original = agent.state.model_dump_json()
        with self.assertRaises(ValueError):
            await agent.compress_context()
        self.assertEqual(agent.state.model_dump_json(), original)
        agent.model.generate_structured_output.assert_not_called()

    async def test_new_summary_overflow_keeps_state(self) -> None:
        """A summary larger than the old budget cannot partially commit."""
        agent = _agent(summary="s" * 80)
        original = agent.state.model_dump_json()
        with self.assertRaisesRegex(ValueError, "Summary and retained"):
            await agent.compress_context()
        self.assertEqual(agent.state.model_dump_json(), original)

    async def test_summary_error_and_cancellation_keep_state(self) -> None:
        """Both model errors and interruption preserve the source context."""
        for error in (RuntimeError("model failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                agent = _agent()
                agent.context_config.compression_fallback_to_truncation = False
                agent.model.generate_structured_output.side_effect = error
                original = agent.state.model_dump_json()
                with self.assertRaises(type(error)):
                    await agent.compress_context()
                self.assertEqual(agent.state.model_dump_json(), original)

    async def test_concurrent_input_is_not_overwritten(self) -> None:
        """A stale partition raises while keeping new input in live state."""
        agent = _agent()
        original = deepcopy(agent.state.context)
        extra = UserMsg("user", "new input")

        async def summarize(**_kwargs: object) -> StructuredResponse:
            agent.state.context.append(extra)
            return StructuredResponse(content={"summary": "summary"})

        agent.model.generate_structured_output.side_effect = summarize
        with self.assertRaisesRegex(RuntimeError, "Context changed"):
            await agent.compress_context()
        self.assertEqual(agent.state.context, original + [extra])

    async def test_unfinished_call_is_not_forced_into_summary(self) -> None:
        """No compressible content never retries with a zero pin budget."""
        agent = _agent()
        agent.state.context = [
            AssistantMsg(
                "assistant",
                [ToolCallBlock(id="pending", name="work", input="{}")],
                id=agent.state.reply_id,
            ),
        ]

        async def count(messages: list[Msg], tools: list[dict]) -> int:
            return (
                80
                if any(m.has_content_blocks("tool_call") for m in messages)
                else 3 + len(tools)
            )

        agent.model.count_tokens.side_effect = count
        original = agent.state.model_dump_json()
        with self.assertRaisesRegex(ValueError, "no compressible"):
            await agent.compress_context()
        self.assertEqual(agent.state.model_dump_json(), original)

    async def test_short_context_skips_summary(self) -> None:
        """Opting in still avoids unnecessary summary requests."""
        agent = _agent()
        agent.state.context = [UserMsg("user", "short")]
        await agent.compress_context()
        self.assertEqual(agent.state.context[0].get_text_content(), "short")
        agent.model.generate_structured_output.assert_not_called()

    async def test_compression_reuses_one_system_prompt_snapshot(self) -> None:
        """Partitioning, summary input and validation use the same prefix."""
        agent = _agent()
        agent._get_system_prompt = AsyncMock(
            side_effect=["sys", "z" * 80],
        )
        await agent.compress_context()
        agent._get_system_prompt.assert_awaited_once()
        messages = agent.model.generate_structured_output.call_args.kwargs[
            "messages"
        ]
        self.assertEqual(messages[0].get_text_content(), "sys")

    async def test_reasoning_rechecks_changed_system_prompt(self) -> None:
        """Retention guards the actual model input after compression."""
        agent = _agent()
        await agent.compress_context()
        agent._get_system_prompt = AsyncMock(return_value="z" * 80)
        agent.model._call_api = AsyncMock()
        original = agent.state.model_dump_json()
        with self.assertRaises(ValueError):
            async for _ in agent._reasoning_impl():
                pass
        agent.model._call_api.assert_not_called()
        self.assertEqual(agent.state.model_dump_json(), original)

    async def test_overflow_retry_does_not_split_cross_message_pairs(
        self,
    ) -> None:
        """Dropping old messages for retry preserves whole tool pairs."""
        agent = _agent()
        agent.state.context = [
            AssistantMsg(
                "assistant",
                [ToolCallBlock(id="call", name="work", input="x" * 120)],
            ),
            AssistantMsg(
                "assistant",
                [
                    ToolResultBlock(
                        id="call",
                        name="work",
                        output="result",
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
            UserMsg("user", "recent"),
        ]

        async def count(messages: list[Msg], tools: list[dict]) -> int:
            return sum(
                len(msg.get_text_content() or "")
                + sum(
                    len(call.input)
                    for call in msg.get_content_blocks("tool_call")
                )
                for msg in messages
            ) + len(tools)

        agent.model.count_tokens.side_effect = count
        agent.model.generate_structured_output.side_effect = [
            RuntimeError("input too long"),
            StructuredResponse(content={"summary": "summary"}),
        ]
        await agent.compress_context()
        self.assertEqual(agent.model.generate_structured_output.call_count, 2)
        for call in agent.model.generate_structured_output.call_args_list:
            messages = call.kwargs["messages"]
            calls = {
                block.id
                for msg in messages
                for block in msg.get_content_blocks("tool_call")
            }
            results = {
                block.id
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            }
            self.assertEqual(calls, results)

    async def test_summary_failure_fallback_preserves_pins(self) -> None:
        """Existing truncation fallback never weakens explicit pins."""
        agent = _agent()
        agent.state.summary = "previous"
        agent.model.generate_structured_output.side_effect = RuntimeError(
            "model failed",
        )
        agent.context_config.compression_fallback_to_truncation = True
        pinned = deepcopy(agent.state.context[0])
        await agent.compress_context()
        self.assertEqual(agent.state.summary, "previous")
        self.assertEqual(agent.state.context[0], pinned)
