# -*- coding: utf-8 -*-
"""Tests for explicit context pins and protocol-safe recent history."""
import asyncio
from collections import Counter
from copy import deepcopy
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import PinnedContextRetentionPolicy
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
    Usage,
)
from agentscope.state import AgentState


async def _count_blocks(messages: list[Msg]) -> int:
    """Count a fixed two-token prefix and one token per content block."""
    return 2 + sum(len(msg.content) for msg in messages)


def _block_keys(messages: list[Msg]) -> list[tuple[str, str, str]]:
    """Identify blocks including the distinct tool call/result types."""
    return [
        (msg.id, block.type, block.id)
        for msg in messages
        for block in msg.content
    ]


class ContextRetentionTest(IsolatedAsyncioTestCase):
    """Exercise pin budgets, recent history and tool protocol constraints."""

    async def test_pins_and_recent_context(self) -> None:
        """Keep an old constraint and the newest message within the budget."""
        context = [
            UserMsg(
                "user",
                "Never change the public API.",
                id="constraint",
                metadata={"context_retention": True},
            ),
            UserMsg("user", "An older detail.", id="old"),
            UserMsg("user", "Continue the task.", id="recent"),
        ]
        original = deepcopy(context)
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=1).split(
            context,
            token_budget=4,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(
            [msg.id for msg in result.msgs_to_reserve],
            ["constraint", "recent"],
        )
        self.assertEqual([msg.id for msg in result.msgs_to_compress], ["old"])
        self.assertEqual(result.reserved_tokens, 4)
        self.assertFalse(result.budget_exceeded)
        result.msgs_to_reserve[0].metadata["context_retention"] = False
        result.msgs_to_compress[0].content[0].text = "changed"
        self.assertEqual(context, original)

    async def test_pin_cost_excludes_fixed_prefix(self) -> None:
        """An exact pin cap excludes the counter's fixed input prefix."""
        context = [
            UserMsg("user", "pinned", metadata={"context_retention": True}),
        ]
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=1).split(
            context,
            token_budget=3,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(result.msgs_to_reserve, context)
        self.assertEqual(result.reserved_tokens, 3)

    async def test_pin_overflow_preserves_original(self) -> None:
        """Both pin caps fail without changing the input."""
        context = [
            UserMsg("user", "pinned", metadata={"context_retention": True}),
        ]
        original = deepcopy(context)
        for cap, budget, error in [
            (0, 100, "max_pinned_tokens"),
            (10, 2, "token_budget"),
        ]:
            with self.subTest(cap=cap, budget=budget):
                with self.assertRaisesRegex(ValueError, error):
                    await PinnedContextRetentionPolicy(
                        max_pinned_tokens=cap,
                    ).split(
                        context,
                        token_budget=budget,
                        count_tokens=_count_blocks,
                        unfinished_tool_call_ids=frozenset(),
                    )
                self.assertEqual(context, original)

    async def test_empty_context_reports_prefix_overflow(self) -> None:
        """A prefix exceeding the soft target is allowed without pins."""
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=0).split(
            [],
            token_budget=0,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(result.msgs_to_compress, [])
        self.assertEqual(result.msgs_to_reserve, [])
        self.assertEqual(result.reserved_tokens, 2)
        self.assertTrue(result.budget_exceeded)

    async def test_unfinished_call_survives_soft_budget(self) -> None:
        """A pending compression call is preserved even with no spare space."""
        context = [
            AssistantMsg(
                "agent",
                [
                    TextBlock(text="Older reasoning", id="text"),
                    ToolCallBlock(
                        id="pending",
                        name="CompressContext",
                        input="{}",
                    ),
                ],
                id="reply",
                metadata={"source": "test"},
            ),
        ]
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=0).split(
            context,
            token_budget=1,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset({"pending"}),
        )
        self.assertEqual(
            _block_keys(result.msgs_to_reserve),
            [("reply", "tool_call", "pending")],
        )
        self.assertEqual(
            _block_keys(result.msgs_to_compress),
            [("reply", "text", "text")],
        )
        self.assertEqual(
            result.msgs_to_reserve[0].metadata,
            context[0].metadata,
        )
        self.assertTrue(result.budget_exceeded)

    async def test_pins_then_unfinished_can_exceed_target(self) -> None:
        """Valid pins are not rejected for a later mandatory pending call."""
        context = [
            UserMsg("user", "pin", metadata={"context_retention": True}),
            AssistantMsg(
                "agent",
                [ToolCallBlock(id="pending", name="Read", input="{}")],
            ),
        ]
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=1).split(
            context,
            token_budget=3,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset({"pending"}),
        )
        self.assertEqual(result.msgs_to_reserve, context)
        self.assertEqual(result.reserved_tokens, 4)
        self.assertTrue(result.budget_exceeded)

    async def test_interleaved_tool_pairs_are_atomic(self) -> None:
        """Crossing pairs stay complete in each ordered partition."""
        context = [
            AssistantMsg(
                "agent",
                [
                    ToolCallBlock(id="a", name="Read", input="{}"),
                    ToolCallBlock(id="b", name="Write", input="{}"),
                    ToolResultBlock(id="a", name="Read", output="read"),
                    ToolResultBlock(id="b", name="Write", output="written"),
                    TextBlock(text="Continue", id="text"),
                ],
                id="reply",
                metadata={"details": {"value": 1}},
            ),
        ]
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=0).split(
            context,
            token_budget=5,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(
            _block_keys(result.msgs_to_reserve),
            [
                ("reply", "tool_call", "b"),
                ("reply", "tool_result", "b"),
                ("reply", "text", "text"),
            ],
        )
        self.assertEqual(
            _block_keys(result.msgs_to_compress),
            [("reply", "tool_call", "a"), ("reply", "tool_result", "a")],
        )
        self.assertEqual(
            Counter(_block_keys(context)),
            Counter(_block_keys(result.msgs_to_compress))
            + Counter(_block_keys(result.msgs_to_reserve)),
        )
        result.msgs_to_reserve[0].metadata["details"]["value"] = 2
        self.assertEqual(context[0].metadata["details"]["value"], 1)
        self.assertEqual(
            result.msgs_to_compress[0].metadata["details"]["value"],
            1,
        )

    async def test_pin_closure_crosses_messages_and_counts_its_cost(
        self,
    ) -> None:
        """Pinning a result also retains its older call and charges both."""
        context = [
            AssistantMsg(
                "agent",
                [
                    TextBlock(text="Older thought", id="text"),
                    ToolCallBlock(id="a", name="Read", input="{}"),
                ],
                id="call",
            ),
            AssistantMsg(
                "agent",
                [ToolResultBlock(id="a", name="Read", output="evidence")],
                id="result",
                metadata={"context_retention": True},
            ),
        ]
        with self.assertRaisesRegex(ValueError, "max_pinned_tokens"):
            await PinnedContextRetentionPolicy(max_pinned_tokens=1).split(
                context,
                token_budget=4,
                count_tokens=_count_blocks,
                unfinished_tool_call_ids=frozenset(),
            )
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=2).split(
            context,
            token_budget=4,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(
            _block_keys(result.msgs_to_reserve),
            [("call", "tool_call", "a"), ("result", "tool_result", "a")],
        )
        self.assertEqual(
            _block_keys(result.msgs_to_compress),
            [("call", "text", "text")],
        )

    async def test_message_pin_is_not_a_partial_pin(self) -> None:
        """A marked message must fit in full, including its older blocks."""
        context = [
            AssistantMsg(
                "agent",
                [TextBlock(text="first"), TextBlock(text="second")],
                metadata={"context_retention": True},
            ),
        ]
        with self.assertRaisesRegex(ValueError, "max_pinned_tokens"):
            await PinnedContextRetentionPolicy(max_pinned_tokens=1).split(
                context,
                token_budget=100,
                count_tokens=_count_blocks,
                unfinished_tool_call_ids=frozenset(),
            )

    async def test_custom_marker_requires_boolean_true(self) -> None:
        """Only explicit True pins a message under the configured key."""
        context = [
            UserMsg("user", "truthy", id="old", metadata={"keep": 1}),
            UserMsg("user", "explicit", id="pin", metadata={"keep": True}),
        ]
        result = await PinnedContextRetentionPolicy(
            max_pinned_tokens=1,
            metadata_key="keep",
        ).split(
            context,
            token_budget=3,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual([msg.id for msg in result.msgs_to_reserve], ["pin"])

    async def test_pins_survive_repeated_partition_and_state_round_trip(
        self,
    ) -> None:
        """Existing state serialization preserves pins across compression."""
        policy = PinnedContextRetentionPolicy(max_pinned_tokens=1)
        state = AgentState(
            context=[
                UserMsg(
                    "user",
                    "constraint",
                    id="pin",
                    metadata={"context_retention": True},
                ),
                UserMsg("user", "previous result", id="old"),
            ],
        )
        for index in range(3):
            state.context.append(UserMsg("user", "next", id=f"new-{index}"))
            result = await policy.split(
                state.context,
                token_budget=4,
                count_tokens=_count_blocks,
                unfinished_tool_call_ids=frozenset(),
            )
            self.assertEqual(
                [msg.id for msg in result.msgs_to_reserve],
                ["pin", f"new-{index}"],
            )
            state.context = result.msgs_to_reserve
            state = AgentState.model_validate_json(state.model_dump_json())

    async def test_counter_failure_and_cancellation_preserve_input(
        self,
    ) -> None:
        """Counter-side mutation or failure cannot corrupt the caller state."""
        context = [
            UserMsg("user", "pin", metadata={"context_retention": True}),
        ]
        original = deepcopy(context)
        for error in [
            RuntimeError("counter failed"),
            asyncio.CancelledError(),
        ]:

            async def fail(
                messages: list[Msg],
                failure: BaseException = error,
            ) -> int:
                if messages:
                    messages[0].content.clear()
                    messages[0].metadata.clear()
                    raise failure
                return 2

            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    await PinnedContextRetentionPolicy(
                        max_pinned_tokens=1,
                    ).split(
                        context,
                        token_budget=3,
                        count_tokens=fail,
                        unfinished_tool_call_ids=frozenset(),
                    )
                self.assertEqual(context, original)

    async def test_empty_message_usage_is_preserved_once(self) -> None:
        """An empty usage carrier belongs to exactly one partition."""
        context = [
            AssistantMsg(
                "agent",
                [],
                id="usage",
                usage=Usage(input_tokens=10, output_tokens=2),
            ),
        ]
        result = await PinnedContextRetentionPolicy(max_pinned_tokens=0).split(
            context,
            token_budget=2,
            count_tokens=_count_blocks,
            unfinished_tool_call_ids=frozenset(),
        )
        self.assertEqual(result.msgs_to_reserve, context)
        self.assertEqual(result.msgs_to_compress, [])

    async def test_invalid_configuration_and_budget(self) -> None:
        """Reject invalid budgets before doing any partition work."""
        for cap in [-1, True, 1.5]:
            with self.subTest(cap=cap):
                with self.assertRaises(ValueError):
                    PinnedContextRetentionPolicy(
                        max_pinned_tokens=cap,  # type: ignore[arg-type]
                    )
        with self.assertRaises(ValueError):
            PinnedContextRetentionPolicy(max_pinned_tokens=0, metadata_key="")
        for budget in [-1, float("nan"), float("inf"), True]:
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):
                    await PinnedContextRetentionPolicy(
                        max_pinned_tokens=0,
                    ).split(
                        [],
                        token_budget=budget,
                        count_tokens=_count_blocks,
                        unfinished_tool_call_ids=frozenset(),
                    )
