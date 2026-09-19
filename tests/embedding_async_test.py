# -*- coding: utf-8 -*-
"""Exercise embedding SDK calls without contacting provider services."""
import asyncio
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentscope.credential import DashScopeCredential, GeminiCredential
from agentscope.embedding import DashScopeEmbeddingModel, GeminiEmbeddingModel
from agentscope.message import Base64Source, DataBlock, TextBlock


@pytest.fixture(
    name="provider",
    params=[
        ("dashscope", "text-embedding-v4", 10),
        ("dashscope", "qwen3-vl-embedding", 20),
        ("gemini", "gemini-embedding-001", 100),
        ("gemini", "gemini-embedding-2", 20),
    ],
    ids=[
        "dashscope-text",
        "dashscope-multimodal",
        "gemini-text",
        "gemini-multimodal",
    ],
)
def _provider(request: pytest.FixtureRequest) -> Iterator[SimpleNamespace]:
    """Patch SDK endpoints for both the baseline and the fixed code."""
    name, model_name, batch_size = request.param
    sync_api = MagicMock()
    async_api = AsyncMock()
    if name == "gemini":
        pytest.importorskip("google.genai")
        client = MagicMock()
        client.models.embed_content = sync_api
        client.aio.models.embed_content = async_api
        with patch("google.genai.Client", return_value=client):
            model = GeminiEmbeddingModel(
                credential=GeminiCredential(api_key="test-key"),
                model=model_name,
                dimensions=1,
                retry_delay=0,
            )
    else:
        model = DashScopeEmbeddingModel(
            credential=DashScopeCredential(api_key="test-key"),
            model=model_name,
            dimensions=1,
            retry_delay=0,
        )

    with (
        patch("dashscope.embeddings.TextEmbedding.call", sync_api),
        patch("dashscope.MultiModalEmbedding.call", sync_api),
    ):
        yield SimpleNamespace(
            name=name,
            model=model,
            batch_size=batch_size,
            multimodal=batch_size == 20,
            sync_api=sync_api,
            async_api=async_api,
        )


def _response(vectors: list[list[float]]) -> SimpleNamespace:
    """Return the fields consumed by both providers, with known token usage."""
    return SimpleNamespace(
        status_code=200,
        embeddings=[SimpleNamespace(values=vector) for vector in vectors],
        output={"embeddings": [{"embedding": vector} for vector in vectors]},
        usage={"total_tokens": 7, "input_tokens": 5, "image_tokens": 2},
    )


def test_embedding_batches_do_not_block(provider: SimpleNamespace) -> None:
    """SDK waits yield the loop and batches can finish out of order."""

    async def run() -> None:
        loop = asyncio.get_running_loop()
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]
        released = [threading.Event(), threading.Event()]
        completed = []

        def call(**kwargs: Any) -> SimpleNamespace:
            batch = kwargs.get("input", kwargs.get("contents"))
            index = 0 if len(batch) > 1 else 1
            loop.call_soon_threadsafe(started[index].set)
            # A timeout only prevents a broken implementation from hanging.
            # The assertion is about event ordering, not elapsed performance.
            assert released[index].wait(5), "SDK call blocked the event loop"
            offset = index * provider.batch_size
            response = _response(
                [[float(offset + i)] for i in range(len(batch))],
            )
            completed.append(index)
            loop.call_soon_threadsafe(finished[index].set)
            return response

        async def async_call(**kwargs: Any) -> SimpleNamespace:
            return await asyncio.to_thread(call, **kwargs)

        provider.sync_api.side_effect = call
        provider.async_api.side_effect = async_call
        inputs: list[TextBlock | DataBlock] = [
            TextBlock(text=str(i)) for i in range(provider.batch_size)
        ]
        inputs.append(
            DataBlock(
                source=Base64Source(data="aW1hZ2U=", media_type="image/png"),
            )
            if provider.multimodal
            else TextBlock(text=str(provider.batch_size)),
        )
        task = asyncio.create_task(provider.model(inputs))
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started)),
                timeout=10,
            )
            assert not task.done()
            # Only this coroutine releases the SDK waits, after both started.
            released[1].set()
            await asyncio.wait_for(finished[1].wait(), timeout=5)
            released[0].set()
            result = await task
        finally:
            for event in released:
                event.set()
            await asyncio.gather(task, return_exceptions=True)

        assert completed == [1, 0]
        assert result.embeddings == [
            [float(i)] for i in range(provider.batch_size + 1)
        ]
        assert result.usage.tokens == (
            14 if provider.name == "dashscope" else 0
        )
        assert result.usage.time >= 0
        assert result.source == "api"

    asyncio.run(run())


def test_embedding_request_and_usage(provider: SimpleNamespace) -> None:
    """SDK arguments and single-response usage retain their existing shape."""
    provider.sync_api.return_value = _response([[0.25]])
    provider.async_api.return_value = _response([[0.25]])
    kwargs = {"task_type": "RETRIEVAL_DOCUMENT"}
    result = asyncio.run(provider.model([TextBlock(text="hello")], **kwargs))
    calls = (
        provider.sync_api.call_args_list + provider.async_api.call_args_list
    )
    assert len(calls) == 1
    actual = calls[0].kwargs
    assert actual["model"] == provider.model.model
    if provider.name == "dashscope":
        expected_input: list[str | dict[str, str]] = (
            [{"text": "hello"}] if provider.multimodal else ["hello"]
        )
        assert actual == {
            "model": provider.model.model,
            "api_key": "test-key",
            "input": expected_input,
            **({} if provider.multimodal else {"dimension": 1}),
            **kwargs,
        }
    else:
        assert actual["config"].output_dimensionality == 1
        assert actual["config"].task_type == "RETRIEVAL_DOCUMENT"
        if provider.multimodal:
            assert len(actual["contents"]) == 1
            assert actual["contents"][0].parts[0].text == "hello"
        else:
            assert actual["contents"] == ["hello"]
    assert result.embeddings == [[0.25]]
    assert result.usage.tokens == (7 if provider.name == "dashscope" else None)
    assert result.usage.time >= 0
    assert result.source == "api"


def test_embedding_sdk_error_preserves_retries(
    provider: SimpleNamespace,
) -> None:
    """DashScope retries RuntimeError; Gemini propagates it immediately."""
    error = RuntimeError("provider unavailable")
    provider.sync_api.side_effect = error
    provider.async_api.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(provider.model(["hello"]))
    assert caught.value is error
    attempts = (
        provider.model.max_retries + 1 if provider.name == "dashscope" else 1
    )
    assert (
        provider.sync_api.call_count + provider.async_api.call_count
        == attempts
    )


def test_dashscope_status_error(provider: SimpleNamespace) -> None:
    """Non-200 responses still produce the provider-specific RuntimeError."""
    if provider.name != "dashscope":
        pytest.skip("Only DashScope returns status codes in the response")
    response = _response([])
    response.status_code = 400
    provider.sync_api.return_value = response
    provider.async_api.return_value = response
    mode = "multimodal" if provider.multimodal else "text"
    with pytest.raises(
        RuntimeError,
        match=f"DashScope {mode} embedding API error",
    ):
        asyncio.run(provider.model(["hello"]))
    assert (
        provider.sync_api.call_count + provider.async_api.call_count
        == provider.model.max_retries + 1
    )
