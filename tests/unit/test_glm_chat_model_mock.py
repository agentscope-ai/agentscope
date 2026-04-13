# -*- coding: utf-8 -*-
"""Mock unit tests for GLM (Zhipu AI) via OpenAIChatModel.

These tests replay real API responses captured by the integration tests
(stored as JSON fixtures under ``scripts/smoke_test/fixtures/``) so they
can run in CI without a real API key.

The mock boundary is ``model.client.chat.completions.create`` — the same
call site used by the existing ``model_openai_test.py`` tests.  This
ensures we are testing OpenAIChatModel's **parsing logic**, not the
openai SDK itself.

Ref: AgentScope issue #664
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock

from agentscope.model import OpenAIChatModel, ChatResponse
from agentscope.message import TextBlock, ThinkingBlock

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "smoke_test" / "fixtures"
)


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture by stem name (without ``.json``)."""
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers – mock construction (following model_openai_test.py conventions)
# ---------------------------------------------------------------------------
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"


def _create_glm_model(
    model_name: str,
    stream: bool = False,
) -> OpenAIChatModel:
    """Create an OpenAIChatModel targeting GLM with a fake key.

    The ``openai.AsyncClient`` is patched at the call-site so no real
    HTTP connection is ever attempted.
    """
    return OpenAIChatModel(
        model_name=model_name,
        api_key="fake-key",
        stream=stream,
        client_kwargs={"base_url": GLM_BASE_URL},
    )


def _make_mock_completion(fixture: dict) -> Mock:
    """Build a ``Mock`` that mimics ``openai.types.chat.ChatCompletion``.

    Mirrors ``_create_mock_response`` / ``_create_mock_response_with_reasoning``
    from ``model_openai_test.py``, but sources data from a fixture dict
    captured by the integration tests.
    """
    content_blocks = fixture["content"]
    text = ""
    reasoning_content = None

    for block in content_blocks:
        if block["type"] == "text":
            text = block["text"]
        elif block["type"] == "thinking":
            reasoning_content = block["thinking"]

    message = Mock()
    message.content = text or None
    message.reasoning_content = reasoning_content
    message.tool_calls = []
    message.audio = None
    message.parsed = None

    choice = Mock()
    choice.message = message

    response = Mock()
    response.choices = [choice]
    response.id = fixture["id"]

    if fixture.get("usage"):
        usage = Mock()
        usage.prompt_tokens = fixture["usage"]["input_tokens"]
        usage.completion_tokens = fixture["usage"]["output_tokens"]
        response.usage = usage
    else:
        response.usage = None

    return response


def _make_stream_mock_from_chunks(raw_chunks: list[dict]) -> Any:
    """Build an async-iterable stream mock from real captured SDK chunks.

    Each element of *raw_chunks* is the ``model_dump()`` output of an
    ``openai.types.chat.ChatCompletionChunk`` captured by the integration
    test.  We convert each dict back into a ``Mock`` whose attribute
    access matches exactly what the framework's
    ``_parse_openai_stream_response`` expects:

    - ``chunk.id``
    - ``chunk.usage``  (with ``.prompt_tokens``, ``.completion_tokens``)
    - ``chunk.choices`` (list)
    - ``choice.delta.content``
    - ``choice.delta.reasoning_content``
    - ``choice.delta.tool_calls``
    - ``choice.delta.audio``
    """

    def _dict_to_chunk_mock(d: dict) -> Mock:
        chunk = Mock()
        chunk.id = d.get("id")

        # usage – may be None on non-final chunks
        raw_usage = d.get("usage")
        if raw_usage:
            u = Mock()
            u.prompt_tokens = raw_usage["prompt_tokens"]
            u.completion_tokens = raw_usage["completion_tokens"]
            chunk.usage = u
        else:
            chunk.usage = None

        # choices
        choices = []
        for raw_choice in d.get("choices", []):
            choice = Mock()
            raw_delta = raw_choice.get("delta", {})

            delta = Mock()
            delta.content = raw_delta.get("content")
            delta.reasoning_content = raw_delta.get("reasoning_content")
            delta.tool_calls = raw_delta.get("tool_calls") or []

            # audio – framework checks `"data" in choice.delta.audio`
            audio_mock = Mock()
            audio_mock.__contains__ = lambda self, key: False
            delta.audio = audio_mock

            choice.delta = delta
            choices.append(choice)

        chunk.choices = choices
        return chunk

    items = [_dict_to_chunk_mock(d) for d in raw_chunks]

    class MockStream:
        """Async-iterable replay of real captured OpenAI stream chunks."""

        def __init__(self) -> None:
            self._items = items
            self._index = 0

        async def __aenter__(self) -> "MockStream":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        def __aiter__(self) -> "MockStream":
            return self

        async def __anext__(self) -> Any:
            if self._index >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._index]
            self._index += 1
            return item

    return MockStream()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
class TestGLMChatModelMock(IsolatedAsyncioTestCase):
    """Mock unit tests for GLM provider via OpenAIChatModel."""

    # -- non-streaming -------------------------------------------------------
    async def test_glm_non_streaming_mock(self) -> None:
        """Replay a real non-streaming GLM response through the parser.

        Verification:
        - ``ChatResponse.content`` contains a ``TextBlock`` whose text
          matches the fixture.
        - ``ChatResponse.usage`` token counts match the fixture.
        """
        fixture = _load_fixture("glm_non_streaming_response")

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = _create_glm_model("glm-4-flash", stream=False)
            model.client = mock_client

            mock_client.chat.completions.create = AsyncMock(
                return_value=_make_mock_completion(fixture),
            )

            messages = [{"role": "user", "content": "用一句话介绍你自己"}]
            result = await model(messages)

        self.assertIsInstance(result, ChatResponse)

        # Text matches fixture
        expected_text = fixture["content"][0]["text"]
        text_blocks = [b for b in result.content if b.get("type") == "text"]
        self.assertTrue(len(text_blocks) > 0)
        self.assertEqual(text_blocks[0]["text"], expected_text)

        # Usage matches fixture
        self.assertIsNotNone(result.usage)
        self.assertEqual(
            result.usage.input_tokens,
            fixture["usage"]["input_tokens"],
        )
        self.assertEqual(
            result.usage.output_tokens,
            fixture["usage"]["output_tokens"],
        )

    # -- streaming -----------------------------------------------------------
    async def test_glm_streaming_mock(self) -> None:
        """Replay real captured GLM stream chunks through the parser.

        Uses ``glm_streaming_chunks.json`` — an array of raw
        ``ChatCompletionChunk.model_dump()`` dicts recorded by the
        integration test — to construct the mock stream.  This gives
        byte-for-byte fidelity with the real GLM SSE stream, including
        correct chunk boundaries, ``finish_reason``, and usage placement.

        Verification:
        - ``await model(messages)`` returns an ``AsyncGenerator``.
        - Iterating yields ``ChatResponse`` chunks.
        - The final accumulated text matches the companion
          ``glm_streaming_response.json`` fixture (end-to-end consistency).
        - The last chunk carries correct ``usage`` information.
        """
        raw_chunks = _load_fixture("glm_streaming_chunks")
        expected = _load_fixture("glm_streaming_response")

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = _create_glm_model("glm-4-flash", stream=True)
            model.client = mock_client

            mock_client.chat.completions.create = AsyncMock(
                return_value=_make_stream_mock_from_chunks(raw_chunks),
            )

            messages = [{"role": "user", "content": "用一句话介绍你自己"}]
            result = await model(messages)

        self.assertIsInstance(result, AsyncGenerator)

        last_chunk: ChatResponse | None = None
        chunk_count = 0
        async for chunk in result:
            self.assertIsInstance(chunk, ChatResponse)
            last_chunk = chunk
            chunk_count += 1

        self.assertIsNotNone(last_chunk)
        # Should produce multiple parsed ChatResponse chunks
        self.assertGreater(chunk_count, 1)

        # Final accumulated text matches the integration-test result
        expected_text = expected["content"][0]["text"]
        text_blocks = [
            b for b in last_chunk.content if b.get("type") == "text"
        ]
        self.assertTrue(len(text_blocks) > 0)
        self.assertEqual(text_blocks[0]["text"], expected_text)

        # Usage from the real final chunk
        self.assertIsNotNone(last_chunk.usage)
        self.assertEqual(
            last_chunk.usage.input_tokens,
            expected["usage"]["input_tokens"],
        )
        self.assertEqual(
            last_chunk.usage.output_tokens,
            expected["usage"]["output_tokens"],
        )

    # -- reasoning / thinking ------------------------------------------------
    async def test_glm_reasoning_mock(self) -> None:
        """Replay a real reasoning GLM response through the parser.

        The fixture contains both ``ThinkingBlock`` (from
        ``reasoning_content``) and ``TextBlock``.  The mock sets
        ``choice.message.reasoning_content`` so that the framework's
        ``_parse_openai_completion_response`` produces the correct
        ``ThinkingBlock``.

        Verification:
        - ``content`` contains a ``ThinkingBlock`` with non-empty thinking.
        - ``content`` contains a ``TextBlock`` with the final answer.
        - Both texts match the fixture data exactly.
        """
        fixture = _load_fixture("glm_reasoning_response")

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = _create_glm_model("glm-4.7-flash", stream=False)
            model.client = mock_client

            mock_client.chat.completions.create = AsyncMock(
                return_value=_make_mock_completion(fixture),
            )

            messages = [
                {"role": "user", "content": "计算 12345 * 67890 的结果"},
            ]
            result = await model(
                messages,
                extra_body={"enable_thinking": True},
            )

        self.assertIsInstance(result, ChatResponse)

        # ThinkingBlock
        thinking_blocks = [
            b for b in result.content if b.get("type") == "thinking"
        ]
        self.assertTrue(len(thinking_blocks) > 0)

        expected_thinking = None
        expected_text = None
        for block in fixture["content"]:
            if block["type"] == "thinking":
                expected_thinking = block["thinking"]
            elif block["type"] == "text":
                expected_text = block["text"]

        self.assertIsNotNone(expected_thinking)
        self.assertEqual(thinking_blocks[0]["thinking"], expected_thinking)

        # TextBlock
        text_blocks = [b for b in result.content if b.get("type") == "text"]
        self.assertTrue(len(text_blocks) > 0)
        self.assertEqual(text_blocks[0]["text"], expected_text)

        # extra_body was forwarded to the create call
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(
            call_kwargs.get("extra_body"),
            {"enable_thinking": True},
        )

    # -- multimodal input ----------------------------------------------------
    async def test_glm_multimodal_input_mock(self) -> None:
        """Replay a real multimodal-input GLM response through the parser.

        The messages list contains an ``image_url`` content block.  The
        framework should pass it through unchanged and parse the text
        response normally.

        Verification:
        - The model accepts messages with ``image_url`` content blocks.
        - ``ChatResponse.content`` contains a ``TextBlock`` matching the
          fixture's image-description text.
        - ``usage`` token counts match.
        """
        fixture = _load_fixture("glm_multimodal_input_response")

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = _create_glm_model("glm-4v-flash", stream=False)
            model.client = mock_client

            mock_client.chat.completions.create = AsyncMock(
                return_value=_make_mock_completion(fixture),
            )

            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/test.jpg",
                            },
                        },
                        {
                            "type": "text",
                            "text": "请用中文简要描述这张图片的内容",
                        },
                    ],
                },
            ]
            result = await model(messages)

        self.assertIsInstance(result, ChatResponse)

        expected_text = fixture["content"][0]["text"]
        text_blocks = [b for b in result.content if b.get("type") == "text"]
        self.assertTrue(len(text_blocks) > 0)
        self.assertEqual(text_blocks[0]["text"], expected_text)

        self.assertIsNotNone(result.usage)
        self.assertEqual(
            result.usage.input_tokens,
            fixture["usage"]["input_tokens"],
        )
        self.assertEqual(
            result.usage.output_tokens,
            fixture["usage"]["output_tokens"],
        )

        # Verify the multimodal messages were forwarded as-is
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        sent_content = call_kwargs["messages"][0]["content"]
        self.assertEqual(sent_content[0]["type"], "image_url")
