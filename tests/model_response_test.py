# -*- coding: utf-8 -*-
"""Unit tests for :class:`agentscope.model.ChatResponse` and its
``append_*`` helpers."""
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.message import TextBlock
from agentscope.model import ChatResponse, FinishedReason, ChatUsage


def _dump(chat_response: ChatResponse) -> dict:
    """Convert a ``ChatResponse`` into a plain-dict form suitable for
    ``assertDictEqual`` / ``assertListEqual`` comparison.

    ``ChatResponse`` inherits from ``DictMixin`` (a ``dict`` subclass) but
    keeps its content blocks as pydantic models, so we normalize them via
    ``model_dump(mode="json")`` here.
    """
    d = dict(chat_response)
    d["content"] = [b.model_dump(mode="json") for b in d["content"]]
    if d["usage"] is not None:
        d["usage"] = dict(d["usage"])
    return d


def _expected(
    content: list,
    usage: dict | None = None,
    finished_reason: FinishedReason = FinishedReason.COMPLETED,
    is_last: bool = True,
) -> dict:
    """Build the expected serialized-``ChatResponse`` dict, with
    ``AnyString`` placeholders for auto-generated fields."""
    return {
        "content": content,
        "is_last": is_last,
        "id": AnyString(),
        "created_at": AnyString(),
        "type": "chat_response",
        "usage": usage,
        "finished_reason": finished_reason,
        "metadata": {},
    }


class ChatResponseAppendTest(IsolatedAsyncioTestCase):
    """Test the ``append_*`` methods of ``ChatResponse``."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""

    # ------------------------------------------------------------------
    # append_text
    # ------------------------------------------------------------------
    async def test_append_text_new_block(self) -> None:
        """A new text block is created when the list is empty."""
        r = ChatResponse(content=[TextBlock(text="exists")], is_last=True)
        r.append_text("hello", block_id="t1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {"type": "text", "text": "exists", "id": AnyString()},
                    {"type": "text", "text": "hello", "id": "t1"},
                ],
            ),
        )

    async def test_append_text_merge_same_id(self) -> None:
        """Two ``append_text`` calls with the same ``block_id`` merge
        into a single text block."""
        r = ChatResponse(content=[], is_last=True)
        r.append_text("hello", block_id="t1")
        r.append_text(" world", block_id="t1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {"type": "text", "text": "hello world", "id": "t1"},
                ],
            ),
        )

    async def test_append_text_different_ids_are_separate_blocks(
        self,
    ) -> None:
        """``append_text`` with different ``block_id`` values creates
        separate text blocks in order."""
        r = ChatResponse(content=[], is_last=True)
        r.append_text("first", block_id="t1")
        r.append_text("second", block_id="t2")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {"type": "text", "text": "first", "id": "t1"},
                    {"type": "text", "text": "second", "id": "t2"},
                ],
            ),
        )

    # ------------------------------------------------------------------
    # append_thinking
    # ------------------------------------------------------------------
    async def test_append_thinking_no_signature(self) -> None:
        """A thinking block is created without ``signature`` field when
        the caller does not provide one."""
        r = ChatResponse(content=[], is_last=True)
        r.append_thinking("why", block_id="th1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {"type": "thinking", "thinking": "why", "id": "th1"},
                ],
            ),
        )

    async def test_append_thinking_with_signature_on_create(self) -> None:
        """Signature can be supplied at block-creation time."""
        r = ChatResponse(content=[], is_last=True)
        r.append_thinking("why", block_id="th1", signature="sig-1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "why",
                        "id": "th1",
                        "signature": "sig-1",
                    },
                ],
            ),
        )

    async def test_append_thinking_merge_multiple_deltas_no_signature(
        self,
    ) -> None:
        """Multiple consecutive thinking deltas without signature merge
        into one block. Regression test: the previous implementation
        raised ``AttributeError`` on the second delta because
        ``signature`` was accessed unconditionally."""
        r = ChatResponse(content=[], is_last=True)
        r.append_thinking("hello", block_id="th1")
        r.append_thinking(" world", block_id="th1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "hello world",
                        "id": "th1",
                    },
                ],
            ),
        )

    async def test_append_thinking_signature_after_thinking(self) -> None:
        """The Anthropic pattern: several thinking deltas followed by a
        signature-only delta, all sharing the same ``block_id``."""
        r = ChatResponse(content=[], is_last=True)
        r.append_thinking("hello", block_id="th1")
        r.append_thinking(" world", block_id="th1")
        r.append_thinking("", block_id="th1", signature="sig-1")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "hello world",
                        "id": "th1",
                        "signature": "sig-1",
                    },
                ],
            ),
        )

    # ------------------------------------------------------------------
    # append_tool_call
    # ------------------------------------------------------------------
    async def test_append_tool_call_new_block(self) -> None:
        """A new tool call block is created on first append."""
        r = ChatResponse(content=[], is_last=True)
        r.append_tool_call(block_id="tc1", name="fn", input='{"x":1}')

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "tool_call",
                        "id": "tc1",
                        "name": "fn",
                        "input": '{"x":1}',
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
            ),
        )

    async def test_append_tool_call_merge_input_by_id(self) -> None:
        """Multiple ``append_tool_call`` calls sharing the same
        ``block_id`` concatenate their ``input`` payload."""
        r = ChatResponse(content=[], is_last=True)
        r.append_tool_call(block_id="tc1", name="fn", input='{"x"')
        r.append_tool_call(block_id="tc1", name="fn", input=":1}")

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "tool_call",
                        "id": "tc1",
                        "name": "fn",
                        "input": '{"x":1}',
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
            ),
        )

    async def test_append_tool_call_multiple_calls(self) -> None:
        """Different ``block_id`` values produce separate tool-call
        blocks in the order they were appended."""
        r = ChatResponse(content=[], is_last=True)
        r.append_tool_call(block_id="tc1", name="fn_a", input='{"x":1}')
        r.append_tool_call(block_id="tc2", name="fn_b", input='{"y":2}')

        self.assertDictEqual(
            _dump(r),
            _expected(
                content=[
                    {
                        "type": "tool_call",
                        "id": "tc1",
                        "name": "fn_a",
                        "input": '{"x":1}',
                        "state": "pending",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_call",
                        "id": "tc2",
                        "name": "fn_b",
                        "input": '{"y":2}',
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
            ),
        )

    # ------------------------------------------------------------------
    # append_chat_response
    # ------------------------------------------------------------------
    async def test_append_chat_response_merges_matching_ids(self) -> None:
        """Blocks with matching ids across mixed types are merged, not
        duplicated. Verifies text / thinking / tool_call all merge in
        one call."""
        acc = ChatResponse(content=[], is_last=True)

        # First delta contains one of each block type.
        d1 = ChatResponse(content=[], is_last=False)
        d1.append_text("hello", block_id="txt")
        d1.append_thinking("why", block_id="th")
        d1.append_tool_call(block_id="tc", name="fn", input='{"a"')
        acc.append_chat_response(d1)

        # Second delta extends each block by matching ids.
        d2 = ChatResponse(content=[], is_last=False)
        d2.append_text(" world", block_id="txt")
        d2.append_thinking(" not", block_id="th")
        d2.append_tool_call(block_id="tc", name="fn", input=":1}")
        acc.append_chat_response(d2)

        self.assertDictEqual(
            _dump(acc),
            _expected(
                content=[
                    {
                        "type": "text",
                        "text": "hello world",
                        "id": "txt",
                    },
                    {
                        "type": "thinking",
                        "thinking": "why not",
                        "id": "th",
                    },
                    {
                        "type": "tool_call",
                        "id": "tc",
                        "name": "fn",
                        "input": '{"a":1}',
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
            ),
        )

    async def test_append_chat_response_extends_new_ids(self) -> None:
        """Delta blocks whose ids are not present in the accumulator
        are appended as new blocks."""
        acc = ChatResponse(content=[], is_last=True)
        d1 = ChatResponse(content=[], is_last=False)
        d1.append_text("hello", block_id="t1")
        acc.append_chat_response(d1)

        d2 = ChatResponse(content=[], is_last=False)
        d2.append_text("second", block_id="t2")
        d2.append_tool_call(block_id="tc1", name="fn", input="{}")
        acc.append_chat_response(d2)

        self.assertDictEqual(
            _dump(acc),
            _expected(
                content=[
                    {"type": "text", "text": "hello", "id": "t1"},
                    {"type": "text", "text": "second", "id": "t2"},
                    {
                        "type": "tool_call",
                        "id": "tc1",
                        "name": "fn",
                        "input": "{}",
                        "state": "pending",
                        "suggested_rules": [],
                    },
                ],
            ),
        )

    async def test_append_chat_response_thinking_then_signature(
        self,
    ) -> None:
        """A signature-only delta arriving after several thinking-only
        deltas via ``append_chat_response`` correctly attaches the
        signature to the accumulated thinking block."""
        acc = ChatResponse(content=[], is_last=True)

        d1 = ChatResponse(content=[], is_last=False)
        d1.append_thinking("hello", block_id="th1")
        acc.append_chat_response(d1)

        d2 = ChatResponse(content=[], is_last=False)
        d2.append_thinking(" world", block_id="th1")
        acc.append_chat_response(d2)

        d3 = ChatResponse(content=[], is_last=False)
        d3.append_thinking("", block_id="th1", signature="sig-1")
        acc.append_chat_response(d3)

        self.assertDictEqual(
            _dump(acc),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "hello world",
                        "id": "th1",
                        "signature": "sig-1",
                    },
                ],
            ),
        )

    async def test_append_chat_response_multiple_thinking_deltas_no_signature(
        self,
    ) -> None:
        """Regression test for the ``AttributeError`` that used to be
        raised when merging two consecutive thinking deltas that never
        carried a ``signature``."""
        acc = ChatResponse(content=[], is_last=True)

        d1 = ChatResponse(content=[], is_last=False)
        d1.append_thinking("foo", block_id="th1")
        acc.append_chat_response(d1)

        d2 = ChatResponse(content=[], is_last=False)
        d2.append_thinking("bar", block_id="th1")
        acc.append_chat_response(d2)

        self.assertDictEqual(
            _dump(acc),
            _expected(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "foobar",
                        "id": "th1",
                    },
                ],
            ),
        )

    async def test_append_chat_response_usage_override(self) -> None:
        """``usage`` from the delta overrides ``self.usage`` when
        non-``None``; a delta with no usage leaves it untouched."""
        acc = ChatResponse(content=[], is_last=True)

        # First delta carries an early usage snapshot.
        d1 = ChatResponse(
            content=[],
            is_last=False,
            usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.1),
        )
        d1.append_text("hi", block_id="t1")
        acc.append_chat_response(d1)

        # Second delta has no usage → previous should be preserved.
        d2 = ChatResponse(content=[], is_last=False)
        d2.append_text(" there", block_id="t1")
        acc.append_chat_response(d2)

        # Third delta carries the final usage → it wins.
        d3 = ChatResponse(
            content=[],
            is_last=False,
            usage=ChatUsage(input_tokens=5, output_tokens=10, time=0.5),
        )
        acc.append_chat_response(d3)

        self.assertDictEqual(
            _dump(acc),
            _expected(
                content=[
                    {"type": "text", "text": "hi there", "id": "t1"},
                ],
                usage={
                    "input_tokens": 5,
                    "output_tokens": 10,
                    "time": 0.5,
                    "cache_creation_input_tokens": 0,
                    "cache_input_tokens": 0,
                    "type": "chat",
                    "metadata": None,
                },
            ),
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
