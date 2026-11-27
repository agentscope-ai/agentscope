# -*- coding: utf-8 -*-
"""ASR model resources are released after use."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from agentscope.app._router._asr import transcribe_audio
from agentscope.asr import ASRResponse, OpenAIASRModel
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock


class ASRClientCleanupTest(IsolatedAsyncioTestCase):
    """Exercise model and route cleanup without provider requests."""

    async def test_model_context_closes_client(self) -> None:
        """A batch model context releases the owned client."""
        client = SimpleNamespace(close=AsyncMock())
        with patch("openai.AsyncClient", return_value=client):
            model = OpenAIASRModel(OpenAICredential(api_key="test"))
        async with model:
            pass
        client.close.assert_awaited_once()

    async def test_route_closes_on_success_failure_and_cancellation(
        self,
    ) -> None:
        """Every transcription exit path releases the request's model."""
        for error in (None, ValueError("bad audio"), asyncio.CancelledError()):
            with self.subTest(error=error):
                response = ASRResponse(content=TextBlock(text="hello"))
                transcribe = AsyncMock(
                    return_value=response, side_effect=error
                )
                model = SimpleNamespace(
                    transcribe=transcribe, close=AsyncMock()
                )
                upload = SimpleNamespace(
                    size=5,
                    read=AsyncMock(return_value=b"audio"),
                    filename="test.wav",
                )
                with patch(
                    "agentscope.app._router._asr.get_asr_model",
                    new=AsyncMock(return_value=model),
                ):
                    request = transcribe_audio(
                        file=upload,
                        credential_id="id",
                        model="model",
                        parameters="{}",
                        user_id="user",
                        access=None,
                    )
                    if error is None:
                        self.assertEqual((await request).text, "hello")
                    else:
                        expected = (
                            HTTPException
                            if isinstance(error, ValueError)
                            else asyncio.CancelledError
                        )
                        with self.assertRaises(expected):
                            await request
                model.close.assert_awaited_once()
