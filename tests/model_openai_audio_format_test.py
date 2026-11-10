# -*- coding: utf-8 -*-
"""Resolve audio decoding from the format sent by the real OpenAI SDK."""
import base64
import copy
import io
import json
import wave
from functools import partial
from unittest import IsolatedAsyncioTestCase

import httpx2

from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel


def _wav() -> bytes:
    """Return a valid silent WAV instead of a header-shaped byte fixture."""
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24)
    return output.getvalue()


def _audio(format_name: str) -> dict:
    """Build a complete audio request accepted by the provider."""
    return {"voice": "alloy", "format": format_name}


class OpenAIAudioFormatTest(IsolatedAsyncioTestCase):
    """The parser and transport agree on the effective non-stream format."""

    async def test_effective_audio_format_and_override_precedence(
        self,
    ) -> None:
        """Instance and per-call extra_body overrides follow SDK precedence."""
        cases = [
            ("default_pcm", None, {}, "pcm16"),
            ("direct_wav", None, {"audio": _audio("wav")}, "wav"),
            ("instance_wav", {"audio": _audio("wav")}, {}, "wav"),
            ("instance_mp3", {"audio": _audio("mp3")}, {}, "mp3"),
            (
                "extra_beats_direct",
                {"audio": _audio("wav")},
                {"audio": _audio("pcm16")},
                "wav",
            ),
            (
                "per_call_extra",
                {"audio": _audio("mp3")},
                {"extra_body": {"audio": _audio("wav")}},
                "wav",
            ),
            (
                "unrelated_extra",
                {"offline_option": True},
                {"audio": _audio("wav")},
                "wav",
            ),
        ]
        original_wav = _wav()
        for name, extra_body, generate_kwargs, expected_format in cases:
            with self.subTest(case=name):
                before = copy.deepcopy((extra_body, generate_kwargs))
                captured = []

                def handle(
                    request: httpx2.Request,
                    recorded: list,
                ) -> httpx2.Response:
                    data = json.loads(request.content)
                    recorded.append(data)
                    wire_format = data["audio"]["format"]
                    payload = {
                        "wav": original_wav,
                        "pcm16": b"\x00\x00" * 24,
                        "mp3": b"ID3-offline-fixture",
                    }[wire_format]
                    return httpx2.Response(
                        200,
                        json={
                            "id": "chatcmpl-offline",
                            "object": "chat.completion",
                            "created": 1,
                            "model": "gpt-audio",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "audio": {
                                            "id": "audio_1",
                                            "data": base64.b64encode(
                                                payload,
                                            ).decode(),
                                            "transcript": "hello",
                                            "expires_at": 9999999999,
                                        },
                                    },
                                    "finish_reason": "stop",
                                },
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    )

                async with httpx2.AsyncClient(
                    transport=httpx2.MockTransport(
                        partial(handle, recorded=captured),
                    ),
                ) as client:
                    model = OpenAIChatModel(
                        credential=OpenAICredential(api_key="offline-test"),
                        model="gpt-audio",
                        stream=False,
                        parameters=OpenAIChatModel.Parameters(voice="alloy"),
                        extra_body=extra_body,
                        client_kwargs={"http_client": client},
                    )
                    response = await model([], **generate_kwargs)
                self.assertEqual(
                    captured[0]["audio"]["format"],
                    expected_format,
                )
                source = next(
                    block.source
                    for block in response.content
                    if block.type == "data"
                )
                payload = base64.b64decode(source.data)
                if expected_format == "mp3":
                    self.assertEqual(source.media_type, "audio/mp3")
                    self.assertEqual(payload, b"ID3-offline-fixture")
                else:
                    self.assertEqual(source.media_type, "audio/wav")
                    self.assertEqual(payload, original_wav)
                    with wave.open(io.BytesIO(payload), "rb") as audio:
                        self.assertEqual(
                            audio.readframes(24),
                            b"\x00\x00" * 24,
                        )
                self.assertEqual((extra_body, generate_kwargs), before)
