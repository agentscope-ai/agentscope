# -*- coding: utf-8 -*-
"""Refusal regression tests through the public model and real OpenAI SDK.

The local transport exercises JSON/SSE decoding without provider requests.
"""
import asyncio
import json
from itertools import cycle
from typing import Any, AsyncIterator

import httpx
import pytest
from openai.types.responses import Response, ResponseStreamEvent
from pydantic import TypeAdapter

from utils import AnyString, AnyValue

from agentscope.credential import OpenAICredential
from agentscope.message import UserMsg
from agentscope.model import ChatResponse, OpenAIResponseModel


_REFUSAL = "I cannot help with that request."
_INPUT = "Local deterministic test."


def _text(value: str) -> dict:
    """Build a wire-format ordinary text part."""
    return {"type": "output_text", "text": value, "annotations": []}


def _refusal() -> dict:
    """Build a standard wire-format refusal part."""
    return {"type": "refusal", "refusal": _REFUSAL}


def _response_body(parts: list[dict]) -> dict:
    """Build a complete Responses JSON fixture with cached-token usage."""
    return {
        "id": "resp_local",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": "gpt-4.1-mini",
        "output": [
            {
                "id": "msg_local",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": parts,
            },
        ],
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "usage": {
            "input_tokens": 11,
            "input_tokens_details": {
                "cached_tokens": 3,
                "cache_write_tokens": 0,
            },
            "output_tokens": 7,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 18,
        },
    }


def _fragments(value: str, fragmented: bool) -> list[str]:
    """Split semantic deltas independently of SSE byte boundaries."""
    values = [value[:2], value[2:9], value[9:]] if fragmented else [value]
    return [fragment for fragment in values if fragment]


def _stream_events(parts: list[dict], fragmented: bool) -> list[dict]:
    """Include deltas and normal full-content done/completed snapshots."""
    events: list[dict] = []

    def append(event_type: str, **values: Any) -> None:
        """Append a sequenced event."""
        events.append(
            {"type": event_type, "sequence_number": len(events), **values},
        )

    body = _response_body(parts)
    item = body["output"][0]
    append(
        "response.output_item.added",
        output_index=0,
        item={**item, "status": "in_progress", "content": []},
    )
    for index, part in enumerate(parts):
        kind = part["type"]
        field = "text" if kind == "output_text" else "refusal"
        value = part[field]
        indices: dict[str, Any] = {
            "item_id": item["id"],
            "output_index": 0,
            "content_index": index,
        }
        logprobs: dict[str, Any] = (
            {"logprobs": []} if kind == "output_text" else {}
        )
        append(
            "response.content_part.added",
            **indices,
            part={**part, field: ""},
        )
        for delta in _fragments(value, fragmented):
            append(
                f"response.{kind}.delta",
                **indices,
                delta=delta,
                **logprobs,
            )
        append(
            f"response.{kind}.done",
            **indices,
            **{field: value},
            **logprobs,
        )
        append("response.content_part.done", **indices, part=part)
    append("response.output_item.done", output_index=0, item=item)
    append("response.completed", response=body)
    return events


class _FragmentedSSE(httpx.AsyncByteStream):
    """Deliver SSE bytes in small chunks without network sockets."""

    def __init__(self, events: list[dict]) -> None:
        """Encode the events and the SDK's stream terminator."""
        self.data = (
            "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            )
            + "data: [DONE]\n\n"
        ).encode()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield bytes split independently of event and delta boundaries."""
        cursor = 0
        for size in cycle((1, 3, 5, 11, 29)):
            if cursor >= len(self.data):
                return
            yield self.data[cursor : cursor + size]
            cursor += size


def _dump(response: ChatResponse) -> dict:
    """Serialize the entire response, including content and usage fields."""
    result = dict(response)
    result["content"] = [block.model_dump() for block in response.content]
    result["usage"] = dict(response.usage) if response.usage else None
    return result


def _expected_response(
    values: list[str],
    stream: bool,
    is_last: bool,
) -> dict:
    """Build a full response expectation with dynamic IDs and timestamps."""
    return {
        "content": [
            {
                "type": "text",
                "text": value,
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
            }
            for value in values
        ],
        "is_last": is_last,
        "id": AnyString() if stream else "resp_local",
        "created_at": AnyString(),
        "type": "chat_response",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "time": AnyValue(),
            "cache_creation_input_tokens": 0,
            "cache_input_tokens": 3,
            "type": "chat",
            "metadata": None,
        }
        if is_last
        else None,
        "finished_reason": "completed",
        "metadata": {},
    }


async def _call_model(
    stream: bool,
    parts: list[dict],
    fragmented: bool,
) -> tuple[list[ChatResponse], list[dict]]:
    """Exercise public model calls, SDK decoding, and stream accumulation."""
    requests: list[dict] = []
    body = _response_body(parts)
    Response.model_validate(body, strict=True)
    events = _stream_events(parts, fragmented) if stream else []
    event_adapter = TypeAdapter(ResponseStreamEvent)
    for event in events:
        event_adapter.validate_python(event, strict=True)

    async def handle(request: httpx.Request) -> httpx.Response:
        """Respond locally and record the complete formatted request."""
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(request.content),
            },
        )
        if stream:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_FragmentedSSE(events),
            )
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
    ) as client:
        model = OpenAIResponseModel(
            credential=OpenAICredential(
                api_key="local-transport-only",
                base_url="https://responses.test.invalid/v1",
            ),
            model="gpt-4.1-mini",
            stream=stream,
            max_retries=0,
            client_kwargs={"http_client": client},
        )
        result = await model([UserMsg(name="tester", content=_INPUT)])
        chunks = [chunk async for chunk in result] if stream else [result]
    return chunks, requests


@pytest.mark.parametrize(
    "stream,parts,fragmented",
    [
        pytest.param(False, [_text("Hello.")], False, id="text-nonstream"),
        pytest.param(False, [_refusal()], False, id="refusal-nonstream"),
        pytest.param(
            False,
            [_text("Before. "), _refusal(), _text(" After.")],
            False,
            id="mixed-nonstream",
        ),
        pytest.param(True, [_text("Hello.")], True, id="text-stream"),
        pytest.param(True, [_refusal()], False, id="refusal-stream"),
        pytest.param(True, [_refusal()], True, id="fragmented-refusal-stream"),
        pytest.param(
            True,
            [_text("Before. "), _refusal(), _text(" After.")],
            True,
            id="mixed-stream",
        ),
    ],
)
def test_public_model_preserves_refusal(
    stream: bool,
    parts: list[dict],
    fragmented: bool,
) -> None:
    """Keep refusal text, order, usage and completion without duplication."""
    chunks, requests = asyncio.run(_call_model(stream, parts, fragmented))
    values = [part.get("text", part.get("refusal")) for part in parts]
    expected = []
    if stream:
        for value in values:
            expected.extend(
                _expected_response([delta], stream=True, is_last=False)
                for delta in _fragments(value, fragmented)
            )
        values = ["".join(values)]
    expected.append(_expected_response(values, stream, is_last=True))

    assert [_dump(chunk) for chunk in chunks] == expected
    assert len({chunk.id for chunk in chunks}) == 1
    assert requests == [
        {
            "method": "POST",
            "url": "https://responses.test.invalid/v1/responses",
            "body": {
                "model": "gpt-4.1-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": _INPUT}],
                    },
                ],
                "stream": stream,
            },
        },
    ]
