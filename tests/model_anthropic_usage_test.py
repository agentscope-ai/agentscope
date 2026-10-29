# -*- coding: utf-8 -*-
"""Exercise compatible-endpoint usage through the real SDK, adapter and agent.

Missing/null primary counters are outside Anthropic's documented schema, but
the SDK's default non-strict HTTP parsing accepts them. Refs #3444 / #3453.
"""
import asyncio
import json
from typing import Any

import httpx2 as httpx
import pytest

from agentscope.agent import Agent, InjectionConfig
from agentscope.credential import AnthropicCredential
from agentscope.message import UserMsg
from agentscope.model import AnthropicChatModel


_VALID_USAGE = {
    "input_tokens": 12,
    "output_tokens": 7,
    # Keep cache traffic at zero: cache billing semantics are a separate issue.
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}
_COUNTERS = tuple(_VALID_USAGE)


def _response(stream: bool, usage: dict) -> httpx.Response:
    """Build raw HTTP payloads; let the SDK construct all response objects."""
    message = {
        "id": "msg-usage",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-4-5",
        "content": [{"type": "text", "text": "Hello"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }
    if not stream:
        return httpx.Response(200, json=message)

    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                **message,
                "content": [],
                "stop_reason": None,
                "usage": {**usage, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {
                key: value
                for key, value in usage.items()
                if key == "output_tokens"
            },
        },
        {"type": "message_stop"},
    ]
    body = "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    )
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=body,
    )


async def _check_usage(stream: bool, usage: dict, expected: tuple) -> None:
    """Check adapter output and the two downstream integer-only consumers."""
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        """Return an offline completion for each real SDK request."""
        requests.append(request)
        assert request.url.path == "/v1/messages"
        assert json.loads(request.content)["stream"] == stream
        return _response(stream, usage)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
    ) as client:
        model = AnthropicChatModel(
            credential=AnthropicCredential(
                api_key="test",
                base_url="https://anthropic.test",
            ),
            model="claude-opus-4-5",
            stream=stream,
            max_retries=0,
            client_kwargs={"http_client": client, "max_retries": 0},
        )
        response = await model([UserMsg("user", "Hi")])
        if stream:
            responses = [chunk async for chunk in response]
            response = responses[-1]
        assert response.usage is not None
        assert (
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.usage.cache_input_tokens,
            response.usage.cache_creation_input_tokens,
        ) == expected

        agent = Agent(
            name="assistant",
            system_prompt="Be brief.",
            model=model,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        events = [
            event async for event in agent.reply_stream(UserMsg("user", "Hi"))
        ]
        end_events = [e for e in events if e.type == "MODEL_CALL_END"]
        assert len(end_events) == 1
        end = end_events[0]
        assert (
            end.input_tokens,
            end.output_tokens,
            end.cache_input_tokens,
            end.cache_creation_input_tokens,
        ) == expected
        assert events[-1].type == "REPLY_END"
        assert events[-1].finished_reason == "completed"
        saved = agent.state.context[-1]
        assert saved.role == "assistant"
        assert saved.get_text_content() == "Hello"
        assert saved.usage is not None
        assert saved.usage.model_dump() == dict(
            zip(
                (
                    "input_tokens",
                    "output_tokens",
                    "cache_input_tokens",
                    "cache_creation_input_tokens",
                ),
                expected,
            ),
        )
        assert len(requests) == 2


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("field", _COUNTERS)
@pytest.mark.parametrize("missing", [False, True], ids=["null", "missing"])
def test_incomplete_usage(stream: bool, field: str, missing: bool) -> None:
    """An absent counter must not discard valid counters or the answer."""
    usage = {**_VALID_USAGE, field: None}
    if missing:
        del usage[field]
    expected = tuple(
        0 if key == field else value for key, value in _VALID_USAGE.items()
    )
    asyncio.run(_check_usage(stream, usage, expected))


@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize(
    "value",
    [None, 0, 9],
    ids=["null", "zero", "positive"],
)
def test_all_usage_counters(stream: bool, value: int | None) -> None:
    """Normalize all-null usage and preserve zero and positive controls."""
    usage = {
        "input_tokens": value,
        "output_tokens": value,
        "cache_read_input_tokens": None if value is None else 0,
        "cache_creation_input_tokens": None if value is None else 0,
    }
    expected = (0, 0, 0, 0) if value is None else (value, value, 0, 0)
    asyncio.run(_check_usage(stream, usage, expected))
