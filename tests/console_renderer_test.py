# -*- coding: utf-8 -*-
"""Console renderer test cases."""
from io import StringIO
from unittest import TestCase

from rich.console import Console

from agentscope.console import ConsoleRenderer
from agentscope.event import (
    HintBlockEvent,
    ModelCallEndEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import ToolCallBlock, ToolCallState, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionRule
from agentscope.types import ReplyFinishedReason

REPLY_ID = "reply-test"


class ConsoleRendererTest(TestCase):
    """Test cases for the console renderer."""

    def setUp(self) -> None:
        """Create a renderer that prints into a string buffer."""
        self.buffer = StringIO()
        self.renderer = ConsoleRenderer(
            console=Console(file=self.buffer, width=100, highlight=False),
        )

    def output(self) -> str:
        """The rendered terminal output so far."""
        return self.buffer.getvalue()

    def render_all(self, events: list) -> None:
        """Render the given events in order."""
        for event in events:
            self.renderer.render(event)

    @staticmethod
    def text_events(text: str, block_id: str = "t1") -> list:
        """Build start/delta/end events for a streamed text block."""
        return [
            TextBlockStartEvent(reply_id=REPLY_ID, block_id=block_id),
            *[
                TextBlockDeltaEvent(
                    reply_id=REPLY_ID,
                    block_id=block_id,
                    delta=chunk,
                )
                for chunk in (text[:3], text[3:])
            ],
            TextBlockEndEvent(reply_id=REPLY_ID, block_id=block_id),
        ]

    def test_text_streaming(self) -> None:
        """Text deltas are printed as they arrive."""
        self.render_all(
            [
                ReplyStartEvent(
                    session_id="s",
                    reply_id=REPLY_ID,
                    name="Friday",
                ),
                *self.text_events("hello world"),
            ],
        )
        self.assertIn("Friday", self.output())
        self.assertIn("hello world", self.output())

    def test_quiet_hides_everything_but_text(self) -> None:
        """Quiet verbosity only prints the streamed reply text."""
        self.renderer.verbosity = "quiet"
        self.render_all(
            [
                ReplyStartEvent(
                    session_id="s",
                    reply_id=REPLY_ID,
                    name="Friday",
                ),
                ThinkingBlockStartEvent(reply_id=REPLY_ID, block_id="th1"),
                ThinkingBlockDeltaEvent(
                    reply_id=REPLY_ID,
                    block_id="th1",
                    delta="pondering...",
                ),
                ThinkingBlockEndEvent(reply_id=REPLY_ID, block_id="th1"),
                *self.text_events("the answer"),
                ModelCallEndEvent(
                    reply_id=REPLY_ID,
                    input_tokens=10,
                    output_tokens=5,
                ),
            ],
        )
        self.assertIn("the answer", self.output())
        self.assertNotIn("Friday", self.output())
        self.assertNotIn("pondering", self.output())
        self.assertNotIn("tokens", self.output())

    def test_thinking_and_usage_in_default(self) -> None:
        """Default verbosity prints thinking and token usage."""
        self.render_all(
            [
                ThinkingBlockStartEvent(reply_id=REPLY_ID, block_id="th1"),
                ThinkingBlockDeltaEvent(
                    reply_id=REPLY_ID,
                    block_id="th1",
                    delta="pondering...",
                ),
                ThinkingBlockEndEvent(reply_id=REPLY_ID, block_id="th1"),
                ModelCallEndEvent(
                    reply_id=REPLY_ID,
                    input_tokens=10,
                    output_tokens=5,
                ),
            ],
        )
        self.assertIn("Thinking", self.output())
        self.assertIn("pondering...", self.output())
        self.assertIn("tokens: 10 in / 5 out", self.output())

    def test_tool_call_printed_whole_on_end(self) -> None:
        """Tool call arguments are buffered and pretty-printed on end."""
        self.render_all(
            [
                ToolCallStartEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    tool_call_name="get_weather",
                ),
                ToolCallDeltaEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    delta='{"city": "Hang',
                ),
                ToolCallDeltaEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    delta='zhou"}',
                ),
            ],
        )
        # Nothing printed while the arguments are still streaming
        self.assertNotIn("get_weather", self.output())

        self.renderer.render(
            ToolCallEndEvent(reply_id=REPLY_ID, tool_call_id="c1"),
        )
        self.assertIn('get_weather {"city": "Hangzhou"}', self.output())

    def test_concurrent_tool_results_do_not_interleave(self) -> None:
        """Interleaved result deltas are printed as whole blocks."""

        def result_delta(call_id: str, delta: str) -> object:
            return ToolResultTextDeltaEvent(
                reply_id=REPLY_ID,
                tool_call_id=call_id,
                delta=delta,
            )

        self.render_all(
            [
                ToolResultStartEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    tool_call_name="tool_a",
                ),
                ToolResultStartEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c2",
                    tool_call_name="tool_b",
                ),
                # Deltas of the two results interleave
                result_delta("c1", "AAA-"),
                result_delta("c2", "BBB-"),
                result_delta("c1", "aaa"),
                result_delta("c2", "bbb"),
                ToolResultEndEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    state=ToolResultState.SUCCESS,
                ),
                ToolResultEndEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c2",
                    state=ToolResultState.ERROR,
                ),
            ],
        )
        self.assertIn("AAA-aaa", self.output())
        self.assertIn("BBB-bbb", self.output())
        self.assertIn("✓ tool_a", self.output())
        self.assertIn("✗ tool_b", self.output())

    def test_tool_result_truncation(self) -> None:
        """Long tool results are truncated with a hint line."""
        self.renderer.max_tool_result_lines = 3
        self.render_all(
            [
                ToolResultStartEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    tool_call_name="Bash",
                ),
                ToolResultTextDeltaEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    delta="\n".join(f"line-{i}" for i in range(10)),
                ),
                ToolResultEndEvent(
                    reply_id=REPLY_ID,
                    tool_call_id="c1",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        self.assertIn("line-2", self.output())
        self.assertNotIn("line-3", self.output())
        self.assertIn("(+7 more lines)", self.output())

    def test_hint_block_hidden_only_in_quiet(self) -> None:
        """Hint blocks are shown by default and hidden under quiet."""
        hint = HintBlockEvent(
            reply_id=REPLY_ID,
            block_id="h1",
            source="runtime_state_injection",
            hint="<current-time>2026-08-12</current-time>",
        )
        self.renderer.verbosity = "quiet"
        self.renderer.render(hint)
        self.assertNotIn("current-time", self.output())

        self.renderer.verbosity = "default"
        self.renderer.render(hint)
        self.assertIn("hint from runtime_state_injection", self.output())
        self.assertIn("<current-time>2026-08-12</current-time>", self.output())

    def test_confirm_request_shows_suggested_rules(self) -> None:
        """The confirmation notice lists tool calls and suggested rules."""
        self.renderer.render(
            RequireUserConfirmEvent(
                reply_id=REPLY_ID,
                tool_calls=[
                    ToolCallBlock(
                        id="c1",
                        name="Bash",
                        input='{"command": "pip install requests"}',
                        state=ToolCallState.ASKING,
                        suggested_rules=[
                            PermissionRule(
                                tool_name="Bash",
                                rule_content="pip install",
                                behavior=PermissionBehavior.ALLOW,
                                source="session",
                            ),
                        ],
                    ),
                ],
            ),
        )
        self.assertIn("awaiting user confirmation", self.output())
        self.assertIn(
            'Bash {"command": "pip install requests"}',
            self.output(),
        )
        self.assertIn("suggested rule: allow Bash(pip install)", self.output())

    def test_reply_end_interrupted_and_state_reuse(self) -> None:
        """Interruption notice is printed and last_msg accumulates."""
        self.render_all(
            [
                ReplyStartEvent(
                    session_id="s",
                    reply_id=REPLY_ID,
                    name="Friday",
                ),
                *self.text_events("partial answer"),
                ReplyEndEvent(
                    session_id="s",
                    reply_id=REPLY_ID,
                    finished_reason=ReplyFinishedReason.INTERRUPTED,
                ),
            ],
        )
        self.assertIn("interrupted by the user", self.output())

        msg = self.renderer.last_msg
        assert msg is not None
        self.assertEqual(msg.id, REPLY_ID)
        self.assertEqual(msg.get_text_content(), "partial answer")
        self.assertEqual(
            msg.finished_reason,
            ReplyFinishedReason.INTERRUPTED,
        )
