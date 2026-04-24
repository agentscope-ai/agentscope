# -*- coding: utf-8 -*-
"""Integration tests for GLM (Zhipu AI) via OpenAIChatModel.

These tests hit the real GLM API and require the environment variable
``ZAI_API_KEY`` to be set.  They are designed to capture raw API responses
as JSON fixtures (written to ``scripts/smoke_test/fixtures/``) so that
follow-up mock tests can replay them without network access.

Ref: AgentScope issue #664
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio

from agentscope.model import OpenAIChatModel, ChatResponse
from agentscope.message import TextBlock, ThinkingBlock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# A publicly accessible test image URL
TEST_IMAGE_URL = (
    "https://img1.baidu.com/it/"
    "u=1966616150,2146512490&fm=253&fmt=auto&app=138&f=JPEG?w=751&h=500"
)


# ---------------------------------------------------------------------------
# Skip guard – the whole module is skipped when the key is absent
# ---------------------------------------------------------------------------
_api_key = os.environ.get("ZAI_API_KEY", "").strip() or None
pytestmark = pytest.mark.skipif(
    _api_key is None,
    reason="ZAI_API_KEY environment variable not set – skipping GLM tests",
)


# ---------------------------------------------------------------------------
# Helper factory
# ---------------------------------------------------------------------------
def create_glm_model(
    model_name: str,
    stream: bool = False,
    **extra_kwargs,
) -> OpenAIChatModel:
    """Create an OpenAIChatModel configured for GLM.

    Args:
        model_name: GLM model identifier (e.g. ``"glm-4-flash"``).
        stream: Whether to enable streaming.
        **extra_kwargs: Forwarded to ``OpenAIChatModel.__init__`` — useful
            for ``generate_kwargs`` or any other init-time parameter.
    """
    return OpenAIChatModel(
        model_name=model_name,
        api_key=_api_key,
        stream=stream,
        client_kwargs={"base_url": GLM_BASE_URL},
        **extra_kwargs,
    )


def _save_fixture(name: str, data: dict) -> None:
    """Persist *data* as a pretty-printed JSON file under ``fixtures/``."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _chat_response_to_dict(resp: ChatResponse) -> dict:
    """Convert a ChatResponse into a JSON-serialisable dict.

    ``ChatUsage.metadata`` may carry SDK objects that are not directly
    serialisable, so we convert them defensively.
    """
    usage_dict = None
    if resp.usage is not None:
        usage_dict = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "time": resp.usage.time,
        }

    return {
        "id": resp.id,
        "type": resp.type,
        "created_at": resp.created_at,
        "content": list(resp.content),
        "usage": usage_dict,
        "metadata": resp.metadata,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_glm_non_streaming():
    """Non-streaming call to glm-4-flash.

    Verification points:
    - Returns a ``ChatResponse`` (not a generator).
    - ``content`` contains at least one ``TextBlock`` with non-empty text.
    - ``usage`` is present with positive ``input_tokens`` / ``output_tokens``.
    """
    model = create_glm_model("glm-4-flash", stream=False)

    messages = [{"role": "user", "content": "用一句话介绍你自己"}]
    resp = await model(messages)

    assert isinstance(resp, ChatResponse)

    # At least one TextBlock with content
    text_blocks = [b for b in resp.content if b.get("type") == "text"]
    assert len(text_blocks) > 0, "Expected at least one TextBlock"
    assert text_blocks[0]["text"], "TextBlock text should not be empty"

    # Usage present
    assert resp.usage is not None
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0

    _save_fixture("glm_non_streaming_response", _chat_response_to_dict(resp))


@pytest.mark.asyncio
async def test_glm_streaming():
    """Streaming call to glm-4-flash.

    Verification points:
    - Returns an ``AsyncGenerator``.
    - Iterating yields ``ChatResponse`` chunks.
    - The final accumulated text is non-empty.
    - The last chunk carries ``usage`` with token counts.

    Side-effect:
    - Saves every raw OpenAI SDK ``ChatCompletionChunk`` to
      ``fixtures/glm_streaming_chunks.json`` so that the mock tests can
      replay the exact stream the API produced.
    """
    model = create_glm_model("glm-4-flash", stream=True)

    # Wrap the client's create method to capture raw SDK chunks
    raw_chunks: list[dict] = []
    _original_create = model.client.chat.completions.create

    async def _capturing_create(**kwargs):
        """Proxy that records every chunk yielded by the real stream."""
        real_stream = await _original_create(**kwargs)

        class _RecordingStream:
            def __init__(self, inner):
                self._inner = inner

            async def __aenter__(self):
                await self._inner.__aenter__()
                return self

            async def __aexit__(self, *args):
                await self._inner.__aexit__(*args)

            def __aiter__(self):
                return self

            async def __anext__(self):
                chunk = await self._inner.__anext__()
                # OpenAI SDK objects expose .model_dump()
                raw_chunks.append(chunk.model_dump())
                return chunk

        return _RecordingStream(real_stream)

    model.client.chat.completions.create = _capturing_create

    messages = [{"role": "user", "content": "用一句话介绍你自己"}]
    resp = await model(messages)

    assert isinstance(resp, AsyncGenerator)

    last_chunk: ChatResponse | None = None
    async for chunk in resp:
        assert isinstance(chunk, ChatResponse)
        last_chunk = chunk

    assert last_chunk is not None, "Stream should yield at least one chunk"

    # Final text should be non-empty
    text_blocks = [b for b in last_chunk.content if b.get("type") == "text"]
    assert len(text_blocks) > 0
    full_text = text_blocks[0]["text"]
    assert full_text, "Accumulated stream text should not be empty"

    # Usage should be present in the final chunk
    assert last_chunk.usage is not None, (
        "Final streaming chunk should contain usage information"
    )
    assert last_chunk.usage.input_tokens > 0
    assert last_chunk.usage.output_tokens > 0

    _save_fixture(
        "glm_streaming_response",
        _chat_response_to_dict(last_chunk),
    )

    # Save raw SDK chunks for mock test replay
    assert len(raw_chunks) > 0, "Should have captured at least one raw chunk"
    _save_fixture("glm_streaming_chunks", raw_chunks)


@pytest.mark.asyncio
async def test_glm_reasoning():
    """Reasoning / thinking mode with glm-4.7-flash.

    glm-4.7-flash is a free model that supports ``enable_thinking=True``
    via the ``extra_body`` parameter in the OpenAI-compatible API.

    The framework passes ``extra_body`` through because
    ``OpenAIChatModel.__call__`` merges all ``**kwargs`` into the dict
    sent to ``client.chat.completions.create`` (line 244 of
    ``_openai_model.py``), and the OpenAI Python SDK forwards
    ``extra_body`` to the HTTP request body.

    The response's ``reasoning_content`` attribute on
    ``choice.message`` / ``choice.delta`` is already handled by the
    framework's parser (lines 558-570 for non-streaming,
    386-394 for streaming), so ``ThinkingBlock`` should appear
    automatically.

    Verification points:
    - ``extra_body`` with ``enable_thinking`` is accepted without error.
    - Response ``content`` contains a ``ThinkingBlock`` with non-empty
      ``thinking`` text.
    - Response ``content`` also contains a ``TextBlock`` with the final
      answer.
    """
    model = create_glm_model("glm-4.7-flash", stream=False)

    messages = [{"role": "user", "content": "计算 12345 * 67890 的结果"}]

    # extra_body is forwarded by OpenAI SDK to the HTTP request body
    resp = await model(messages, extra_body={"enable_thinking": True})

    assert isinstance(resp, ChatResponse)

    thinking_blocks = [
        b for b in resp.content if b.get("type") == "thinking"
    ]
    text_blocks = [b for b in resp.content if b.get("type") == "text"]

    # ISSUE: If GLM does not return `reasoning_content` on
    # `choice.message`, the framework will not produce a ThinkingBlock.
    # In that case this assertion will fail and the test should be
    # updated with pytest.mark.xfail or an alternative approach.
    assert len(thinking_blocks) > 0, (
        "Expected at least one ThinkingBlock when enable_thinking=True"
    )
    assert thinking_blocks[0]["thinking"], (
        "ThinkingBlock thinking text should not be empty"
    )

    assert len(text_blocks) > 0, "Expected a TextBlock with the final answer"
    assert text_blocks[0]["text"], "TextBlock text should not be empty"

    assert resp.usage is not None

    _save_fixture(
        "glm_reasoning_response",
        _chat_response_to_dict(resp),
    )


@pytest.mark.asyncio
async def test_glm_multimodal_input():
    """Vision input with a GLM model that accepts images.

    Uses ``glm-4v-flash`` (free, vision-capable). The message contains
    an image URL alongside a text prompt.

    Verification points:
    - The model accepts a multimodal message without error.
    - Response contains a ``TextBlock`` describing the image.
    """
    model = create_glm_model("glm-4v-flash", stream=False)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": TEST_IMAGE_URL},
                },
                {
                    "type": "text",
                    "text": "请用中文简要描述这张图片的内容",
                },
            ],
        },
    ]

    resp = await model(messages)

    assert isinstance(resp, ChatResponse)

    text_blocks = [b for b in resp.content if b.get("type") == "text"]
    assert len(text_blocks) > 0, "Expected at least one TextBlock"
    assert text_blocks[0]["text"], (
        "Model should return a non-empty description of the image"
    )

    assert resp.usage is not None
    assert resp.usage.input_tokens > 0

    _save_fixture(
        "glm_multimodal_input_response",
        _chat_response_to_dict(resp),
    )


@pytest.mark.skip(reason="GLM does not support multimodal output (image/audio generation)")
@pytest.mark.asyncio
async def test_glm_multimodal_output():
    """Multimodal output test — skipped.

    GLM currently does not support generating images or audio in the
    chat completion response. This test is a placeholder for future
    capability.
    """
    pass
