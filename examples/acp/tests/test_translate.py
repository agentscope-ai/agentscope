# -*- coding: utf-8 -*-
"""Unit tests for the AgentEvent → session/update translator (§19.2)."""
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallProgress,
    ToolCallStart,
)

from acp_example.bridge import OpRegistry
from acp_example.translate import Translator

from agentscope.event import (
    HintBlockEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import ToolResultState

REPLY = "reply-1"


def make() -> tuple[Translator, OpRegistry]:
    """Build a fresh translator + registry pair."""
    ops = OpRegistry()
    return Translator(ops), ops


def test_text_deltas_map_to_message_chunks_with_block_id() -> None:
    """Text deltas become agent_message_chunks keyed by block id."""
    tr, _ = make()
    assert not tr.translate(
        TextBlockStartEvent(reply_id=REPLY, block_id="b1"),
    )
    updates = tr.translate(
        TextBlockDeltaEvent(reply_id=REPLY, block_id="b1", delta="Hello"),
    )
    assert len(updates) == 1
    upd = updates[0]
    assert isinstance(upd, AgentMessageChunk)
    assert upd.content.text == "Hello"
    assert upd.message_id == "b1"
    assert not tr.translate(
        TextBlockEndEvent(reply_id=REPLY, block_id="b1"),
    )


def test_thinking_deltas_map_to_thought_chunks() -> None:
    """Thinking deltas become agent_thought_chunks."""
    tr, _ = make()
    updates = tr.translate(
        ThinkingBlockDeltaEvent(reply_id=REPLY, block_id="t1", delta="hmm"),
    )
    assert isinstance(updates[0], AgentThoughtChunk)
    assert updates[0].message_id == "t1"


def test_tool_call_lifecycle_and_pending_registration() -> None:
    """The full tool lifecycle maps to tool_call/_update events."""
    tr, ops = make()
    start = tr.translate(
        ToolCallStartEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            tool_call_name="Read",
        ),
    )
    assert isinstance(start[0], ToolCallStart)
    assert start[0].tool_call_id == "tc1"
    assert start[0].kind == "read"
    assert start[0].status == "pending"

    assert not tr.translate(
        ToolCallDeltaEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            delta='{"file_path": ',
        ),
    )
    end = tr.translate(
        ToolCallDeltaEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            delta='"/tmp/x"}',
        ),
    ) + tr.translate(ToolCallEndEvent(reply_id=REPLY, tool_call_id="tc1"))
    assert len(end) == 1
    assert end[0].raw_input == {"file_path": "/tmp/x"}
    # Args complete → the op is registered for middleware claiming.
    assert ops.pending == {"tc1": ("Read", '{"file_path": "/tmp/x"}')}

    running = tr.translate(
        ToolResultStartEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            tool_call_name="Read",
        ),
    )
    assert running[0].status == "in_progress"

    delta = tr.translate(
        ToolResultTextDeltaEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            delta="line 1\n",
        ),
    )
    assert delta[0].content[0].content.text == "line 1\n"

    done = tr.translate(
        ToolResultEndEvent(
            reply_id=REPLY,
            tool_call_id="tc1",
            state=ToolResultState.SUCCESS,
        ),
    )
    assert isinstance(done[0], ToolCallProgress)
    assert done[0].status == "completed"
    assert done[0].raw_output == "line 1\n"
    assert done[0].field_meta == {"agentscope": {"result_state": "completed"}}
    # Executed or not, the pending entry must be cleaned up.
    assert not ops.pending


def test_result_state_taxonomy() -> None:
    """ToolResultState maps to ACP status plus a _meta taxonomy."""
    for state, status, taxonomy in [
        (ToolResultState.ERROR, "failed", "failed"),
        (ToolResultState.INTERRUPTED, "failed", "interrupted"),
        (ToolResultState.DENIED, "failed", "denied"),
    ]:
        tr, _ = make()
        tr.translate(
            ToolResultStartEvent(
                reply_id=REPLY,
                tool_call_id="tc",
                tool_call_name="Write",
            ),
        )
        done = tr.translate(
            ToolResultEndEvent(
                reply_id=REPLY,
                tool_call_id="tc",
                state=state,
            ),
        )
        assert done[0].status == status
        assert done[0].field_meta == {
            "agentscope": {"result_state": taxonomy},
        }


def test_noise_events_translate_to_nothing() -> None:
    """Hints, model-call boundaries and reply ends emit nothing."""
    tr, _ = make()
    # Hint blocks are runtime-state injection noise — never rendered.
    assert not tr.translate(
        HintBlockEvent(
            reply_id=REPLY,
            block_id="h1",
            source="tasks",
            hint="<system-reminder>internal</system-reminder>",
        ),
    )
    # No faithful usage_update is possible (no context-window size), so
    # model-call boundaries emit nothing.
    assert not tr.translate(
        ModelCallStartEvent(reply_id=REPLY, model_name="m"),
    )
    assert not tr.translate(
        ModelCallEndEvent(
            reply_id=REPLY,
            input_tokens=3,
            output_tokens=5,
        ),
    )
    assert not tr.translate(
        ReplyEndEvent(reply_id=REPLY, session_id="s1"),
    )
