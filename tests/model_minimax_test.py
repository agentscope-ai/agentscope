# -*- coding: utf-8 -*-
"""Unit tests for the MiniMax chat model and formatter."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.credential import MiniMaxCredential
from agentscope.formatter import MiniMaxChatFormatter
from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    URLSource,
)
from agentscope.model import MiniMaxChatModel


class MiniMaxTest(IsolatedAsyncioTestCase):
    """Tests for the MiniMax-specific behavior."""

    async def test_request_kwargs(self) -> None:
        """Thinking is sent as adaptive without budget or output config."""
        model = MiniMaxChatModel(
            credential=MiniMaxCredential(api_key="test"),
            model="MiniMax-M3",
            stream=False,
        )
        response = MagicMock(id="msg-1", content=[])
        response.usage = MagicMock(input_tokens=1, output_tokens=1)
        model.client = MagicMock()
        model.client.messages.create = AsyncMock(return_value=response)
        msgs = [Msg(name="user", content=[TextBlock(text="Hi")], role="user")]

        await model(msgs)
        model.parameters.thinking_enable = True
        await model(msgs)

        self.assertListEqual(
            [_.kwargs for _ in model.client.messages.create.await_args_list],
            [
                {
                    "model": "MiniMax-M3",
                    "max_tokens": 8192,
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Hi"}],
                        },
                    ],
                },
                {
                    "model": "MiniMax-M3",
                    "max_tokens": 8192,
                    "stream": False,
                    "thinking": {"type": "adaptive"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "Hi"}],
                        },
                    ],
                },
            ],
        )

    async def test_format_video(self) -> None:
        """Supported videos become video blocks, unsupported ones skipped."""
        formatter = MiniMaxChatFormatter(
            input_types=["text/plain", "image/*", "video/mp4"],
        )
        msgs = [
            Msg(
                name="user",
                content=[
                    DataBlock(
                        source=Base64Source(
                            data="AAAA",
                            media_type="video/mp4",
                        ),
                    ),
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/a.mkv",
                            media_type="video/x-matroska",
                        ),
                    ),
                    DataBlock(
                        source=Base64Source(
                            data="BBBB",
                            media_type="image/png",
                        ),
                    ),
                ],
                role="user",
            ),
        ]

        self.assertListEqual(
            await formatter.format(msgs),
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video",
                            "source": {
                                "type": "base64",
                                "media_type": "video/mp4",
                                "data": "AAAA",
                            },
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "BBBB",
                            },
                        },
                    ],
                },
            ],
        )

    def test_model_cards(self) -> None:
        """The MiniMax model cards carry the documented limits."""
        cards = sorted(MiniMaxChatModel.list_models(), key=lambda _: _.name)

        self.assertListEqual(
            [
                {
                    "name": _.name,
                    "context_size": _.context_size,
                    "output_size": _.output_size,
                    "input_types": _.input_types,
                }
                for _ in cards
            ],
            [
                {
                    "name": "MiniMax-M2.7",
                    "context_size": 204800,
                    "output_size": 65536,
                    "input_types": ["text/plain", "application/x-thinking"],
                },
                {
                    "name": "MiniMax-M2.7-highspeed",
                    "context_size": 204800,
                    "output_size": 65536,
                    "input_types": ["text/plain", "application/x-thinking"],
                },
                {
                    "name": "MiniMax-M3",
                    "context_size": 1000000,
                    "output_size": 131072,
                    "input_types": [
                        "text/plain",
                        "application/x-thinking",
                        "image/jpeg",
                        "image/png",
                        "image/gif",
                        "image/webp",
                        "video/mp4",
                        "video/x-msvideo",
                        "video/x-matroska",
                    ],
                },
            ],
        )

    def test_default_base_url(self) -> None:
        """The credential defaults to the Anthropic-compatible endpoint."""
        self.assertEqual(
            MiniMaxCredential(api_key="test").base_url,
            "https://api.minimax.io/anthropic",
        )
