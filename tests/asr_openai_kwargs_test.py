# -*- coding: utf-8 -*-
"""Unit tests for per-call transcription options on the OpenAI ASR model.

``ASRModelBase.transcribe`` documents ``**kwargs`` as provider-specific
transcription options, and ``file``, ``model`` and ``response_format`` are
parameters of the OpenAI Audio Transcriptions endpoint. They must therefore be
overridable per call like ``language`` and ``prompt`` already are, instead of
colliding with the values ``transcribe`` supplies itself.
"""

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.asr import OpenAIASRModel
from agentscope.credential import OpenAICredential


class OpenAIASRTranscribeKwargTest(IsolatedAsyncioTestCase):
    """Test that every documented per-call option reaches the SDK."""

    def make_model(
        self,
        parameters: OpenAIASRModel.Parameters | None = None,
    ) -> OpenAIASRModel:
        """Build a model whose transcription call is stubbed out."""
        model = OpenAIASRModel(
            credential=OpenAICredential(api_key="test"),
            model="gpt-4o-mini-transcribe",
            parameters=parameters,
        )
        model.client = MagicMock()
        model.client.audio.transcriptions.create = AsyncMock(
            return_value=MagicMock(text="Hello world"),
        )
        return model

    def sent_kwargs(self, model: OpenAIASRModel) -> dict[str, Any]:
        """Return the whole keyword set handed to the transcription call."""
        create = model.client.audio.transcriptions.create
        create.assert_awaited_once()
        return dict(create.await_args.kwargs)

    async def test_response_format_override_reaches_sdk(self) -> None:
        """Ask for segments and timestamps without binding the key twice."""
        model = self.make_model()
        await model.transcribe(b"RIFFfake", response_format="verbose_json")

        self.assertEqual(
            self.sent_kwargs(model),
            {
                "file": ("audio.wav", b"RIFFfake"),
                "model": "gpt-4o-mini-transcribe",
                "response_format": "verbose_json",
            },
        )

    async def test_model_override_reaches_sdk(self) -> None:
        """Transcribe one call with a different model name."""
        model = self.make_model()
        await model.transcribe(b"RIFFfake", model="whisper-1")

        self.assertEqual(
            self.sent_kwargs(model),
            {
                "file": ("audio.wav", b"RIFFfake"),
                "model": "whisper-1",
                "response_format": "json",
            },
        )

    async def test_file_override_reaches_sdk(self) -> None:
        """Replace the multipart audio tuple built from ``filename``."""
        model = self.make_model()
        await model.transcribe(b"RIFFfake", file=("sample.mp3", b"mp3 bytes"))

        self.assertEqual(
            self.sent_kwargs(model),
            {
                "file": ("sample.mp3", b"mp3 bytes"),
                "model": "gpt-4o-mini-transcribe",
                "response_format": "json",
            },
        )

    async def test_reserved_override_keeps_configured_parameters(
        self,
    ) -> None:
        """Combine a per-call override with the configured parameters."""
        model = self.make_model(
            OpenAIASRModel.Parameters(language="en", prompt="AgentScope"),
        )
        await model.transcribe(b"RIFFfake", "sample.mp3", language="fr")

        self.assertEqual(
            self.sent_kwargs(model),
            {
                "file": ("sample.mp3", b"RIFFfake"),
                "model": "gpt-4o-mini-transcribe",
                "response_format": "json",
                "language": "fr",
                "prompt": "AgentScope",
            },
        )
