# -*- coding: utf-8 -*-
"""Exercise MiniMax requests through the real SDK with an offline transport."""
import inspect
import json
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

import httpx2
from pydantic import ValidationError

from agentscope.credential import MiniMaxCredential
from agentscope.message import Base64Source, DataBlock, Msg, TextBlock
from agentscope.model import MiniMaxChatModel


class MiniMaxTransportTest(IsolatedAsyncioTestCase):
    """Check serialized requests rather than replacing SDK methods."""

    async def _request(
        self,
        model_name: str | None = None,
        region: str = "io",
        parameters: dict | None = None,
    ) -> dict:
        """Return the JSON body captured at the HTTP transport boundary."""
        captured = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            self.assertEqual(
                str(request.url),
                f"https://api.minimax.{region}/anthropic/v1/messages",
            )
            captured.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json={
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "model": captured[-1]["model"],
                    "content": [{"type": "text", "text": "OK"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
        ) as client:
            kwargs = {} if model_name is None else {"model": model_name}
            model = MiniMaxChatModel(
                credential=MiniMaxCredential(
                    api_key="test",
                    base_url=f"https://api.minimax.{region}/anthropic",
                ),
                parameters=MiniMaxChatModel.Parameters(**(parameters or {})),
                stream=False,
                client_kwargs={"http_client": client},
                **kwargs,
            )
            await model(
                [
                    Msg(
                        name="user",
                        role="user",
                        content=[
                            TextBlock(text="Describe these."),
                            DataBlock(
                                source=Base64Source(
                                    data="AAAA",
                                    media_type="image/png",
                                ),
                            ),
                            DataBlock(
                                source=Base64Source(
                                    data="BBBB",
                                    media_type="video/mp4",
                                ),
                            ),
                        ],
                    ),
                ],
            )
        self.assertEqual(len(captured), 1)
        body = captured[0]
        self.assertEqual(
            [block["type"] for block in body["messages"][0]["content"]],
            ["text", "image", "video"],
        )
        self.assertNotIn("service_tier", body)
        self.assertNotIn("reasoning_effort", body)
        return body

    async def test_default_and_explicit_models_in_both_regions(self) -> None:
        """Default Flash works while explicit names retain their identity."""
        for region in ("io", "cn"):
            for name in (
                None,
                "MiniMax-M3.1-Flash",
                "MiniMax-M3",
                "MiniMax-M2.7",
                "MiniMax-M2.7-highspeed",
                "custom-model",
            ):
                with self.subTest(region=region, model=name):
                    body = await self._request(name, region)
                    self.assertEqual(
                        body["model"],
                        name or "MiniMax-M3.1-Flash",
                    )
                    if name in (None, "MiniMax-M3.1-Flash"):
                        self.assertEqual(
                            body["thinking"],
                            {"type": "adaptive"},
                        )
                        self.assertEqual(
                            body["output_config"],
                            {"effort": "max"},
                        )
                    else:
                        self.assertNotIn("thinking", body)
                        self.assertNotIn("output_config", body)

    async def test_flash_efforts_and_m3_thinking(self) -> None:
        """Flash accepts all effort levels; M3 keeps its adaptive toggle."""
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                body = await self._request(
                    parameters={"reasoning_effort": effort},
                )
                self.assertEqual(body["output_config"], {"effort": effort})
        body = await self._request(
            "MiniMax-M3",
            parameters={"thinking_enable": True, "reasoning_effort": "low"},
        )
        self.assertEqual(body["thinking"], {"type": "adaptive"})
        self.assertNotIn("output_config", body)
        with self.assertRaises(ValidationError):
            MiniMaxChatModel.Parameters(reasoning_effort="none")

    async def test_default_streaming_request(self) -> None:
        """The default Flash SDK stream retains adaptive thinking."""
        captured = []
        events: list[dict[str, Any]] = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-stream",
                    "type": "message",
                    "role": "assistant",
                    "model": "MiniMax-M3.1-Flash",
                    "content": [],
                    "usage": {"input_tokens": 1, "output_tokens": 0},
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
                "delta": {"type": "text_delta", "text": "OK"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]

        def respond(request: httpx2.Request) -> httpx2.Response:
            captured.append(json.loads(request.content))
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode(),
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
        ) as client:
            model = MiniMaxChatModel(
                credential=MiniMaxCredential(api_key="test"),
                client_kwargs={"http_client": client},
            )
            stream = await model(
                [
                    Msg(
                        name="user",
                        role="user",
                        content=[TextBlock(text="Answer OK.")],
                    ),
                ],
            )
            chunks = [chunk async for chunk in stream]
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0]["stream"])
        self.assertEqual(captured[0]["model"], "MiniMax-M3.1-Flash")
        self.assertEqual(captured[0]["thinking"], {"type": "adaptive"})
        self.assertEqual(captured[0]["output_config"], {"effort": "max"})
        self.assertTrue(chunks[-1].is_last)
        self.assertEqual(chunks[-1].content[0].text, "OK")

    async def test_structured_output_keeps_flash_adaptive(self) -> None:
        """SDK retries skip the disabled-thinking fallback for Flash."""
        captured = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            captured.append(body)
            self.assertEqual(body["model"], "MiniMax-M3.1-Flash")
            self.assertEqual(body["thinking"], {"type": "adaptive"})
            self.assertEqual(body["output_config"], {"effort": "max"})
            if len(captured) < 3:
                return httpx2.Response(
                    400,
                    json={
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": "Try another tool-choice strategy.",
                        },
                    },
                )
            return httpx2.Response(
                200,
                json={
                    "id": "msg-structured",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-result",
                            "name": "generate_structured_output",
                            "input": {"answer": "OK"},
                        },
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
        ) as client:
            model = MiniMaxChatModel(
                credential=MiniMaxCredential(api_key="test"),
                stream=False,
                client_kwargs={"http_client": client, "max_retries": 0},
            )
            result = await model.generate_structured_output(
                [
                    Msg(
                        name="user",
                        role="user",
                        content=[TextBlock(text="Answer OK.")],
                    ),
                ],
                {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
            )
        self.assertEqual(result.content, {"answer": "OK"})
        self.assertEqual(len(captured), 3)
        self.assertEqual(
            captured[0]["tool_choice"],
            {"type": "tool", "name": "generate_structured_output"},
        )
        self.assertEqual(captured[1]["tool_choice"], {"type": "auto"})
        self.assertNotIn("tool_choice", captured[2])

    async def test_candidates_and_saved_models(self) -> None:
        """Candidate filtering does not rewrite saved legacy/custom models."""
        from agentscope.app._router._model import list_models
        from agentscope.app._router._schema._model import ListModelsRequest
        from agentscope.app._service._model import get_model
        from agentscope.app.storage import ChatModelConfig

        result = await list_models(
            ListModelsRequest(provider="minimax_credential"),
        )
        self.assertEqual(
            [card.name for card in result.models],
            ["MiniMax-M3.1-Flash", "MiniMax-M3"],
        )
        self.assertEqual(result.total, 2)
        self.assertEqual(
            [card.name for card in MiniMaxChatModel.list_models()],
            ["MiniMax-M3.1-Flash", "MiniMax-M3"],
        )
        custom_dir = str(
            Path(inspect.getfile(MiniMaxChatModel)).parent / "_models",
        )
        self.assertIn(
            "MiniMax-M2.7",
            [card.name for card in MiniMaxChatModel.list_models(custom_dir)],
        )
        flash, m3 = result.models
        self.assertNotIn(
            "thinking_enable",
            flash.parameter_schema["properties"],
        )
        self.assertNotIn("reasoning_effort", m3.parameter_schema["properties"])
        access = MagicMock()
        access.resolve_credential = AsyncMock(
            return_value=MagicMock(
                data={"type": "minimax_credential", "api_key": "test"},
            ),
        )
        for name in ("MiniMax-M2.7", "MiniMax-M3", "custom-model"):
            model = await get_model(
                "user",
                ChatModelConfig(
                    type="minimax_chat",
                    credential_id="credential",
                    model=name,
                    parameters={},
                ),
                access,
            )
            self.assertEqual(model.model, name)
            if name == "MiniMax-M2.7":
                self.assertEqual(model.context_size, 204800)
            await model.client.close()
