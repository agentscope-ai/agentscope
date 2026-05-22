# -*- coding: utf-8 -*-
"""Unit tests for Msg.append_event – event-stream-to-Msg accumulation.

The test drives a single Msg object through a full, realistic streaming
sequence and asserts the complete model_dump() after every individual event.

Coverage
--------
* TextBlock  : start / delta (×2) / end
* ThinkingBlock : start / delta (×2) / end
* DataBlock (base-64) : start / delta (×2) / end
* ToolCallBlock streaming : start / delta (×2) / end
* RequireUserConfirmEvent  → ASKING state
* UserConfirmResultEvent (confirmed=True)  → ALLOWED state
* UserConfirmResultEvent (confirmed=False) → FINISHED state
* ToolResultBlock text output : start / text-delta (×2) / end (SUCCESS)
* RequireExternalExecutionEvent → SUBMITTED state
* ExternalExecutionResultEvent  → ToolResultBlock appended directly
* ToolResultBlock data output   : base-64 delta + URL delta / end (ERROR)
* ModelCallEndEvent (×2) → usage initialized then accumulated
* ReplyEndEvent → finished_at stamped
* Wrong reply_id → event silently skipped
* Missing block  → warning, no crash
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.event import (
    ConfirmResult,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ExternalExecutionResultEvent,
    ModelCallEndEvent,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
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
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    Msg,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)

# ---------------------------------------------------------------------------
# Fixed IDs used throughout – hard-coded so ground-truth dicts are readable.
# ---------------------------------------------------------------------------
_REPLY_ID = "reply_001"
_SESSION_ID = "session_001"

_B_TEXT = "b_text_001"  # TextBlock id
_B_THINK = "b_think_001"  # ThinkingBlock id
_B_DATA = "b_data_001"  # DataBlock id

_TC_ALLOW = "tc_allow_001"  # tool call that gets confirmed → allowed
_TC_DENY = "tc_deny_001"  # tool call that gets denied  → finished
_TC_EXT = "tc_ext_001"  # tool call for external execution
_TC_IMG = "tc_img_001"  # tool call whose result has data blocks

_RES_DATA_B = "res_data_001"  # DataBlock inside tool-result (base-64)
_RES_URL_B = "res_url_001"  # DataBlock inside tool-result (URL)

_FIXED_END_TS = "2026-01-01T12:00:00"  # deterministic finished_at


# ---------------------------------------------------------------------------
# Block-dict helpers – each call returns a fresh dict to avoid aliasing.
# ---------------------------------------------------------------------------


def _tb(block_id: str, text: str) -> dict:
    """Text block dict."""
    return {"type": "text", "id": block_id, "text": text}


def _thb(block_id: str, thinking: str) -> dict:
    """Thinking block dict."""
    return {"type": "thinking", "id": block_id, "thinking": thinking}


def _db_b64(block_id: str, data: str, media_type: str) -> dict:
    """DataBlock (base-64 source) dict."""
    return {
        "type": "data",
        "id": block_id,
        "source": {"type": "base64", "data": data, "media_type": media_type},
        "name": None,
    }


def _db_url(block_id: str, url: str, media_type: str) -> dict:
    """DataBlock (URL source) dict."""
    return {
        "type": "data",
        "id": block_id,
        "source": {"type": "url", "url": url, "media_type": media_type},
        "name": None,
    }


def _tcb(tc_id: str, name: str, inp: str, state: str) -> dict:
    """ToolCallBlock dict."""
    return {
        "type": "tool_call",
        "id": tc_id,
        "name": name,
        "input": inp,
        "state": state,
        "suggested_rules": [],
    }


def _trb(tc_id: str, name: str, output: Any, state: str) -> dict:
    """ToolResultBlock dict."""
    return {
        "type": "tool_result",
        "id": tc_id,
        "name": name,
        "output": output,
        "state": state,
    }


class EventToMessageTest(IsolatedAsyncioTestCase):
    """Test Msg.append_event across a full streaming event sequence."""

    # ------------------------------------------------------------------
    # asyncSetUp: build self.events and self.ground_truths in lock-step.
    # ------------------------------------------------------------------

    async def asyncSetUp(self) -> None:
        """Build the Msg, the event list, and the ground-truth list."""
        self.msg = Msg(
            id=_REPLY_ID,
            name="TestAgent",
            role="assistant",
            content=[],
        )
        _created_at = self.msg.created_at

        def _base(
            content: list,
            finished_at: str | None = None,
            usage: dict | None = None,
        ) -> dict:
            """Return the expected model_dump() of self.msg."""
            return {
                "name": "TestAgent",
                "role": "assistant",
                "id": _REPLY_ID,
                "metadata": {},
                "created_at": _created_at,
                "finished_at": finished_at,
                "content": content,
                "usage": usage,
            }

        # ================================================================
        # Stage 1 – Text block streaming
        # ================================================================
        ev_text_start = TextBlockStartEvent(
            reply_id=_REPLY_ID,
            block_id=_B_TEXT,
        )
        gt_text_start = _base([_tb(_B_TEXT, "")])

        ev_text_delta1 = TextBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_TEXT,
            delta="Hello",
        )
        gt_text_delta1 = _base([_tb(_B_TEXT, "Hello")])

        ev_text_delta2 = TextBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_TEXT,
            delta=" World",
        )
        gt_text_delta2 = _base([_tb(_B_TEXT, "Hello World")])

        ev_text_end = TextBlockEndEvent(reply_id=_REPLY_ID, block_id=_B_TEXT)
        gt_text_end = _base([_tb(_B_TEXT, "Hello World")])  # unchanged

        # ================================================================
        # Stage 2 – Thinking block streaming
        # ================================================================
        ev_think_start = ThinkingBlockStartEvent(
            reply_id=_REPLY_ID,
            block_id=_B_THINK,
        )
        gt_think_start = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, ""),
            ],
        )

        ev_think_delta1 = ThinkingBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_THINK,
            delta="Let me",
        )
        gt_think_delta1 = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me"),
            ],
        )

        ev_think_delta2 = ThinkingBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_THINK,
            delta=" think",
        )
        gt_think_delta2 = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
            ],
        )

        ev_think_end = ThinkingBlockEndEvent(
            reply_id=_REPLY_ID,
            block_id=_B_THINK,
        )
        gt_think_end = _base(
            [  # unchanged
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
            ],
        )

        # ================================================================
        # Stage 3 – Data block streaming (base-64)
        # ================================================================
        ev_data_start = DataBlockStartEvent(
            reply_id=_REPLY_ID,
            block_id=_B_DATA,
            media_type="image/png",
        )
        gt_data_start = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
                _db_b64(_B_DATA, "", "image/png"),
            ],
        )

        ev_data_delta1 = DataBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_DATA,
            data="abc",
            media_type="image/png",
        )
        gt_data_delta1 = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
                _db_b64(_B_DATA, "abc", "image/png"),
            ],
        )

        ev_data_delta2 = DataBlockDeltaEvent(
            reply_id=_REPLY_ID,
            block_id=_B_DATA,
            data="def",
            media_type="image/png",
        )
        gt_data_delta2 = _base(
            [
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
                _db_b64(_B_DATA, "abcdef", "image/png"),
            ],
        )

        ev_data_end = DataBlockEndEvent(reply_id=_REPLY_ID, block_id=_B_DATA)
        gt_data_end = _base(
            [  # unchanged
                _tb(_B_TEXT, "Hello World"),
                _thb(_B_THINK, "Let me think"),
                _db_b64(_B_DATA, "abcdef", "image/png"),
            ],
        )

        # ================================================================
        # Stage 4 – ToolCall (TC_ALLOW): stream → confirm → allowed
        #           + text tool-result (SUCCESS)
        # ================================================================
        ev_tc_allow_start = ToolCallStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            tool_call_name="search",
        )
        _s4_prefix = [
            _tb(_B_TEXT, "Hello World"),
            _thb(_B_THINK, "Let me think"),
            _db_b64(_B_DATA, "abcdef", "image/png"),
        ]
        gt_tc_allow_start = _base(
            _s4_prefix + [_tcb(_TC_ALLOW, "search", "", "pending")],
        )

        ev_tc_allow_delta1 = ToolCallDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            delta='{"q"',
        )
        gt_tc_allow_delta1 = _base(
            _s4_prefix + [_tcb(_TC_ALLOW, "search", '{"q"', "pending")],
        )

        ev_tc_allow_delta2 = ToolCallDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            delta=': "hi"}',
        )
        gt_tc_allow_delta2 = _base(
            _s4_prefix + [_tcb(_TC_ALLOW, "search", '{"q": "hi"}', "pending")],
        )

        ev_tc_allow_end = ToolCallEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
        )
        gt_tc_allow_end = _base(  # unchanged
            _s4_prefix + [_tcb(_TC_ALLOW, "search", '{"q": "hi"}', "pending")],
        )

        # RequireUserConfirmEvent  → state: pending → asking
        _tc_allow_block = ToolCallBlock(
            id=_TC_ALLOW,
            name="search",
            input='{"q": "hi"}',
        )
        ev_require_confirm = RequireUserConfirmEvent(
            reply_id=_REPLY_ID,
            tool_calls=[_tc_allow_block],
        )
        gt_require_confirm = _base(
            _s4_prefix + [_tcb(_TC_ALLOW, "search", '{"q": "hi"}', "asking")],
        )

        # UserConfirmResultEvent (confirmed=True)  → state: asking → allowed
        ev_user_confirmed = UserConfirmResultEvent(
            reply_id=_REPLY_ID,
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=_tc_allow_block),
            ],
        )
        gt_user_confirmed = _base(
            _s4_prefix + [_tcb(_TC_ALLOW, "search", '{"q": "hi"}', "allowed")],
        )

        # ToolResult for _TC_ALLOW – text output
        ev_result_start = ToolResultStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            tool_call_name="search",
        )
        _s4b_prefix = _s4_prefix + [
            _tcb(_TC_ALLOW, "search", '{"q": "hi"}', "allowed"),
        ]
        gt_result_start = _base(
            _s4b_prefix + [_trb(_TC_ALLOW, "search", [], "running")],
        )

        # First text-delta creates a new TextBlock with auto-generated ID.
        ev_result_text1 = ToolResultTextDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            delta="Found:",
        )
        gt_result_text1 = _base(
            _s4b_prefix
            + [
                _trb(
                    _TC_ALLOW,
                    "search",
                    [{"type": "text", "id": AnyString(), "text": "Found:"}],
                    "running",
                ),
            ],
        )

        # Second text-delta appends to the SAME TextBlock.
        ev_result_text2 = ToolResultTextDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            delta=" 3 items",
        )
        gt_result_text2 = _base(
            _s4b_prefix
            + [
                _trb(
                    _TC_ALLOW,
                    "search",
                    [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Found: 3 items",
                        },
                    ],
                    "running",
                ),
            ],
        )

        ev_result_end_ok = ToolResultEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_ALLOW,
            state=ToolResultState.SUCCESS,
        )
        gt_result_end_ok = _base(
            _s4b_prefix
            + [
                _trb(
                    _TC_ALLOW,
                    "search",
                    [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Found: 3 items",
                        },
                    ],
                    "success",
                ),
            ],
        )

        # ================================================================
        # Stage 5 – ToolCall (TC_DENY): stream → confirm → denied (finished)
        # ================================================================
        _s5_prefix = _s4b_prefix + [
            _trb(
                _TC_ALLOW,
                "search",
                [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Found: 3 items",
                    },
                ],
                "success",
            ),
        ]

        ev_tc_deny_start = ToolCallStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_DENY,
            tool_call_name="delete",
        )
        gt_tc_deny_start = _base(
            _s5_prefix + [_tcb(_TC_DENY, "delete", "", "pending")],
        )

        ev_tc_deny_end = ToolCallEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_DENY,
        )
        gt_tc_deny_end = _base(  # unchanged
            _s5_prefix + [_tcb(_TC_DENY, "delete", "", "pending")],
        )

        _tc_deny_block = ToolCallBlock(id=_TC_DENY, name="delete", input="")
        ev_require_confirm_deny = RequireUserConfirmEvent(
            reply_id=_REPLY_ID,
            tool_calls=[_tc_deny_block],
        )
        gt_require_confirm_deny = _base(
            _s5_prefix + [_tcb(_TC_DENY, "delete", "", "asking")],
        )

        # UserConfirmResultEvent (confirmed=False)  → state: asking → finished
        ev_user_denied = UserConfirmResultEvent(
            reply_id=_REPLY_ID,
            confirm_results=[
                ConfirmResult(confirmed=False, tool_call=_tc_deny_block),
            ],
        )
        gt_user_denied = _base(
            _s5_prefix + [_tcb(_TC_DENY, "delete", "", "finished")],
        )

        # ================================================================
        # Stage 6 – ToolCall (TC_EXT): external execution flow
        # ================================================================
        _s6_prefix = _s5_prefix + [_tcb(_TC_DENY, "delete", "", "finished")]

        ev_tc_ext_start = ToolCallStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_EXT,
            tool_call_name="run_code",
        )
        gt_tc_ext_start = _base(
            _s6_prefix + [_tcb(_TC_EXT, "run_code", "", "pending")],
        )

        ev_tc_ext_end = ToolCallEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_EXT,
        )
        gt_tc_ext_end = _base(  # unchanged
            _s6_prefix + [_tcb(_TC_EXT, "run_code", "", "pending")],
        )

        _tc_ext_block = ToolCallBlock(id=_TC_EXT, name="run_code", input="")
        ev_require_ext = RequireExternalExecutionEvent(
            reply_id=_REPLY_ID,
            tool_calls=[_tc_ext_block],
        )
        gt_require_ext = _base(
            _s6_prefix + [_tcb(_TC_EXT, "run_code", "", "submitted")],
        )

        # ExternalExecutionResultEvent – appends a ToolResultBlock directly.
        _ext_result_block = ToolResultBlock(
            id=_TC_EXT,
            name="run_code",
            output="output: hello",
            state=ToolResultState.SUCCESS,
        )
        ev_ext_result = ExternalExecutionResultEvent(
            reply_id=_REPLY_ID,
            execution_results=[_ext_result_block],
        )
        _s6b_prefix = _s6_prefix + [_tcb(_TC_EXT, "run_code", "", "submitted")]
        gt_ext_result = _base(
            _s6b_prefix
            + [_trb(_TC_EXT, "run_code", "output: hello", "success")],
        )

        # ================================================================
        # Stage 7 – ToolResult with data output: base-64 + URL blocks
        # ================================================================
        _s7_prefix = _s6b_prefix + [
            _trb(_TC_EXT, "run_code", "output: hello", "success"),
        ]

        ev_tc_img_start = ToolCallStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
            tool_call_name="screenshot",
        )
        gt_tc_img_start = _base(
            _s7_prefix + [_tcb(_TC_IMG, "screenshot", "", "pending")],
        )

        ev_tc_img_end = ToolCallEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
        )
        gt_tc_img_end = _base(  # unchanged
            _s7_prefix + [_tcb(_TC_IMG, "screenshot", "", "pending")],
        )

        ev_res_img_start = ToolResultStartEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
            tool_call_name="screenshot",
        )
        _s7b_prefix = _s7_prefix + [_tcb(_TC_IMG, "screenshot", "", "pending")]
        gt_res_img_start = _base(
            _s7b_prefix + [_trb(_TC_IMG, "screenshot", [], "running")],
        )

        # Base-64 data delta → DataBlock(base64) appended to output
        ev_res_img_b64 = ToolResultDataDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
            block_id=_RES_DATA_B,
            media_type="image/png",
            data="iVBOR==",
        )
        gt_res_img_b64 = _base(
            _s7b_prefix
            + [
                _trb(
                    _TC_IMG,
                    "screenshot",
                    [_db_b64(_RES_DATA_B, "iVBOR==", "image/png")],
                    "running",
                ),
            ],
        )

        # URL data delta → DataBlock(url) appended to output
        ev_res_img_url = ToolResultDataDeltaEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
            block_id=_RES_URL_B,
            media_type="image/jpeg",
            url="https://example.com/img.jpg",
        )
        gt_res_img_url = _base(
            _s7b_prefix
            + [
                _trb(
                    _TC_IMG,
                    "screenshot",
                    [
                        _db_b64(_RES_DATA_B, "iVBOR==", "image/png"),
                        _db_url(
                            _RES_URL_B,
                            "https://example.com/img.jpg",
                            "image/jpeg",
                        ),
                    ],
                    "running",
                ),
            ],
        )

        ev_res_img_end = ToolResultEndEvent(
            reply_id=_REPLY_ID,
            tool_call_id=_TC_IMG,
            state=ToolResultState.ERROR,
        )
        gt_res_img_end = _base(
            _s7b_prefix
            + [
                _trb(
                    _TC_IMG,
                    "screenshot",
                    [
                        _db_b64(_RES_DATA_B, "iVBOR==", "image/png"),
                        _db_url(
                            _RES_URL_B,
                            "https://example.com/img.jpg",
                            "image/jpeg",
                        ),
                    ],
                    "error",
                ),
            ],
        )

        # ================================================================
        # Stage 8 – ModelCallEndEvent (first call: usage initialized;
        #          second call: usage accumulated)
        # ================================================================
        _final_content = _s7b_prefix + [
            _trb(
                _TC_IMG,
                "screenshot",
                [
                    _db_b64(_RES_DATA_B, "iVBOR==", "image/png"),
                    _db_url(
                        _RES_URL_B,
                        "https://example.com/img.jpg",
                        "image/jpeg",
                    ),
                ],
                "error",
            ),
        ]
        ev_model_call_end_1 = ModelCallEndEvent(
            reply_id=_REPLY_ID,
            input_tokens=10,
            output_tokens=20,
        )
        gt_model_call_end_1 = _base(
            _final_content,
            usage={"input_tokens": 10, "output_tokens": 20},
        )

        ev_model_call_end_2 = ModelCallEndEvent(
            reply_id=_REPLY_ID,
            input_tokens=5,
            output_tokens=8,
        )
        gt_model_call_end_2 = _base(
            _final_content,
            usage={"input_tokens": 15, "output_tokens": 28},
        )

        # ================================================================
        # Stage 9 – ReplyEndEvent
        # ================================================================
        ev_reply_end = ReplyEndEvent(
            reply_id=_REPLY_ID,
            session_id=_SESSION_ID,
            created_at=_FIXED_END_TS,
        )
        gt_reply_end = _base(
            _final_content,
            finished_at=_FIXED_END_TS,
            usage={"input_tokens": 15, "output_tokens": 28},
        )

        # ================================================================
        # Assemble the two parallel lists
        # ================================================================
        self.events = [
            # Stage 1: Text
            ev_text_start,
            ev_text_delta1,
            ev_text_delta2,
            ev_text_end,
            # Stage 2: Thinking
            ev_think_start,
            ev_think_delta1,
            ev_think_delta2,
            ev_think_end,
            # Stage 3: Data (base-64)
            ev_data_start,
            ev_data_delta1,
            ev_data_delta2,
            ev_data_end,
            # Stage 4: ToolCall → confirm (allowed) + text result (success)
            ev_tc_allow_start,
            ev_tc_allow_delta1,
            ev_tc_allow_delta2,
            ev_tc_allow_end,
            ev_require_confirm,
            ev_user_confirmed,
            ev_result_start,
            ev_result_text1,
            ev_result_text2,
            ev_result_end_ok,
            # Stage 5: ToolCall → confirm (denied)
            ev_tc_deny_start,
            ev_tc_deny_end,
            ev_require_confirm_deny,
            ev_user_denied,
            # Stage 6: ToolCall → external execution
            ev_tc_ext_start,
            ev_tc_ext_end,
            ev_require_ext,
            ev_ext_result,
            # Stage 7: ToolResult with data output (base-64 + URL)
            ev_tc_img_start,
            ev_tc_img_end,
            ev_res_img_start,
            ev_res_img_b64,
            ev_res_img_url,
            ev_res_img_end,
            # Stage 8: MODEL_CALL_END (init + accumulate)
            ev_model_call_end_1,
            ev_model_call_end_2,
            # Stage 9: REPLY_END
            ev_reply_end,
        ]
        self.ground_truths = [
            # Stage 1
            gt_text_start,
            gt_text_delta1,
            gt_text_delta2,
            gt_text_end,
            # Stage 2
            gt_think_start,
            gt_think_delta1,
            gt_think_delta2,
            gt_think_end,
            # Stage 3
            gt_data_start,
            gt_data_delta1,
            gt_data_delta2,
            gt_data_end,
            # Stage 4
            gt_tc_allow_start,
            gt_tc_allow_delta1,
            gt_tc_allow_delta2,
            gt_tc_allow_end,
            gt_require_confirm,
            gt_user_confirmed,
            gt_result_start,
            gt_result_text1,
            gt_result_text2,
            gt_result_end_ok,
            # Stage 5
            gt_tc_deny_start,
            gt_tc_deny_end,
            gt_require_confirm_deny,
            gt_user_denied,
            # Stage 6
            gt_tc_ext_start,
            gt_tc_ext_end,
            gt_require_ext,
            gt_ext_result,
            # Stage 7
            gt_tc_img_start,
            gt_tc_img_end,
            gt_res_img_start,
            gt_res_img_b64,
            gt_res_img_url,
            gt_res_img_end,
            # Stage 8
            gt_model_call_end_1,
            gt_model_call_end_2,
            # Stage 9
            gt_reply_end,
        ]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    async def test_append_event_stream(self) -> None:
        """Apply every event in order and assert the full Msg state after each.

        Uses a zip loop over self.events / self.ground_truths so that the
        event index is clear from the assertion failure message.
        """
        self.assertEqual(
            len(self.events),
            len(self.ground_truths),
            "events and ground_truths must have equal length",
        )
        for idx, (event, expected) in enumerate(
            zip(self.events, self.ground_truths),
        ):
            self.msg.append_event(event)
            self.assertDictEqual(
                self.msg.model_dump(),
                expected,
                msg=f"Mismatch after event[{idx}] ({event.type})",
            )

    async def test_wrong_reply_id_is_skipped(self) -> None:
        """An event whose reply_id does not match msg.id must be ignored."""
        original_dump = self.msg.model_dump()
        wrong_event = TextBlockStartEvent(
            reply_id="totally_wrong_id",
            block_id="should_not_appear",
        )
        self.msg.append_event(wrong_event)
        self.assertDictEqual(
            self.msg.model_dump(),
            original_dump,
            msg="Msg must not change when event.reply_id does not match",
        )

    async def test_missing_block_does_not_crash(self) -> None:
        """Sending a delta for a non-existent block must log a warning only."""
        original_dump = self.msg.model_dump()

        # Delta events for blocks that were never started
        ghost_events = [
            TextBlockDeltaEvent(
                reply_id=_REPLY_ID,
                block_id="ghost_text",
                delta="x",
            ),
            ThinkingBlockDeltaEvent(
                reply_id=_REPLY_ID,
                block_id="ghost_think",
                delta="x",
            ),
            DataBlockDeltaEvent(
                reply_id=_REPLY_ID,
                block_id="ghost_data",
                data="x",
                media_type="image/png",
            ),
            ToolCallDeltaEvent(
                reply_id=_REPLY_ID,
                tool_call_id="ghost_tc",
                delta="x",
            ),
            ToolResultTextDeltaEvent(
                reply_id=_REPLY_ID,
                tool_call_id="ghost_tr",
                delta="x",
            ),
            ToolResultDataDeltaEvent(
                reply_id=_REPLY_ID,
                tool_call_id="ghost_tr",
                media_type="image/png",
                data="x",
            ),
            ToolResultEndEvent(
                reply_id=_REPLY_ID,
                tool_call_id="ghost_tr",
                state=ToolResultState.SUCCESS,
            ),
        ]
        for ev in ghost_events:
            self.msg.append_event(ev)  # must not raise

        self.assertDictEqual(
            self.msg.model_dump(),
            original_dump,
            msg="Msg must not change when delta targets a missing block",
        )

    async def asyncTearDown(self) -> None:
        """No teardown needed."""
