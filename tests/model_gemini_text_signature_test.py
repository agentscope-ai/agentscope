# -*- coding: utf-8 -*-
"""Round-trip signed Gemini text parts through SDK parsing and persistence."""
import base64
import json
from typing import Any
from unittest import IsolatedAsyncioTestCase

import httpx2

from agentscope.credential import GeminiCredential
from agentscope.formatter import GeminiChatFormatter
from agentscope.message import AssistantMsg, Msg, TextBlock, UserMsg
from agentscope.model import ChatResponse, GeminiChatModel


def _signature(value: str) -> str:
    """Encode an opaque test signature exactly as the wire represents it."""
    return base64.b64encode(value.encode()).decode()


def _response(parts: list[dict]) -> dict:
    """Build a real SDK GenerateContentResponse JSON payload."""
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "index": 0},
        ],
        "usageMetadata": {
            "promptTokenCount": 1,
            "candidatesTokenCount": 1,
            "totalTokenCount": 2,
        },
    }


class GeminiTextSignatureTest(IsolatedAsyncioTestCase):
    """Signed text remains on its corresponding part across model turns."""

    async def _round_trip(self, streaming: bool) -> None:
        """Exercise SDK responses, public accumulation and JSON restoration."""
        try:
            from google.genai import types
        except ImportError:
            self.skipTest("Gemini extra is not installed")
        expected: list[dict[str, Any]] = [
            {
                "text": "Let me think.",
                "thought": True,
                "thoughtSignature": _signature("thought"),
            },
            {"text": "Answer", "thoughtSignature": _signature("answer")},
            {"text": "Another.", "thoughtSignature": _signature("another")},
        ]
        deltas: list[list[dict[str, Any]]] = [
            [{"text": "Let ", "thought": True}],
            [{"text": "me think.", "thought": True}],
            [
                {
                    "text": "",
                    "thought": True,
                    "thoughtSignature": _signature("thought"),
                },
            ],
            [{"text": "Answer"}],
            [{"text": "", "thoughtSignature": _signature("answer")}],
            [{"text": "Another.", "thoughtSignature": _signature("another")}],
        ]
        captured = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            captured.append(json.loads(request.content))
            if streaming:
                data = "".join(
                    "data: " + json.dumps(_response(parts)) + "\n\n"
                    for parts in deltas
                )
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=data,
                )
            return httpx2.Response(200, json=_response(expected))

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle),
        ) as client:
            model = GeminiChatModel(
                credential=GeminiCredential(api_key="offline-test"),
                model="gemini-3-flash-preview",
                stream=streaming,
                client_kwargs={
                    "http_options": types.HttpOptions(
                        base_url="http://offline.test",
                        httpx_async_client=client,
                    ),
                },
            )
            history = [UserMsg(name="user", content="Explain.")]
            result = await model(history)
            if streaming:
                chunks = [chunk async for chunk in result]
                result = chunks[-1]
                self.assertTrue(result.is_last)
            saved = AssistantMsg(name="assistant", content=result.content)
            restored = Msg.model_validate_json(saved.model_dump_json())
            history.extend(
                [restored, UserMsg(name="user", content="Continue.")],
            )
            next_result = await model(history)
            if streaming:
                async for _ in next_result:
                    pass
        self.assertEqual(captured[1]["contents"][1]["parts"], expected)
        self.assertEqual(len(restored.content), 3)

    async def test_nonstream_signed_parts_survive_two_turns(self) -> None:
        """Adjacent non-streaming parts retain their own signatures."""
        await self._round_trip(False)

    async def test_stream_late_signatures_survive_two_turns(self) -> None:
        """Late signatures survive accumulation and persistence."""
        await self._round_trip(True)

    async def test_unsigned_history_keeps_its_existing_shape(self) -> None:
        """Old unsigned messages keep their shape without a migration."""
        message = AssistantMsg(name="assistant", content="old answer")
        dumped = message.model_dump_json()
        restored = Msg.model_validate_json(dumped)
        formatted = await GeminiChatFormatter().format([restored])
        self.assertEqual(
            formatted,
            [{"role": "model", "parts": [{"text": "old answer"}]}],
        )
        self.assertNotIn("gemini_thought_signature", dumped)

    async def test_legacy_accumulation_keeps_late_metadata(self) -> None:
        """The public response accumulator also retains closing metadata."""
        response = ChatResponse(
            content=[TextBlock(id="part-1", text="answer")],
            is_last=False,
        )
        for signature in (_signature("answer"), None):
            response.append_chat_response(
                ChatResponse(
                    content=[
                        TextBlock(
                            id="part-1",
                            text="",
                            gemini_thought_signature=signature,
                        ),
                    ],
                    is_last=False,
                ),
            )
        self.assertEqual(response.content[0].text, "answer")
        self.assertEqual(
            response.content[0].model_extra,
            {"gemini_thought_signature": _signature("answer")},
        )
