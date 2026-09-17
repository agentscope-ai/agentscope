# -*- coding: utf-8 -*-
"""Unit tests for the batch ASR reference implementation."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.asr import ASRModelBase, ASRResponse, OpenAIASRModel
from agentscope.credential import OpenAICredential


class TestOpenAIASRModel(IsolatedAsyncioTestCase):
    """Test transcription without making network requests."""

    def make_model(
        self,
        parameters: OpenAIASRModel.Parameters | None = None,
    ) -> OpenAIASRModel:
        model = OpenAIASRModel(
            credential=OpenAICredential(api_key="test"),
            parameters=parameters,
        )
        model.client = MagicMock()
        model.client.audio.transcriptions.create = AsyncMock(
            return_value=MagicMock(text="Hello world"),
        )
        return model

    async def test_transcribes_audio(self) -> None:
        model = self.make_model()
        response = await model.transcribe(b"audio bytes", "sample.mp3")

        self.assertIsInstance(model, ASRModelBase)
        self.assertIsInstance(response, ASRResponse)
        self.assertEqual(response.content.text, "Hello world")
        self.assertEqual(response.type, "asr")
        model.client.audio.transcriptions.create.assert_awaited_once_with(
            file=("sample.mp3", b"audio bytes"),
            model="gpt-4o-mini-transcribe",
            response_format="json",
        )

    async def test_forwards_parameters(self) -> None:
        model = self.make_model(
            OpenAIASRModel.Parameters(language="en", prompt="AgentScope"),
        )
        await model.transcribe(b"audio")

        model.client.audio.transcriptions.create.assert_awaited_once_with(
            file=("audio.wav", b"audio"),
            model="gpt-4o-mini-transcribe",
            response_format="json",
            language="en",
            prompt="AgentScope",
        )

    async def test_rejects_empty_audio(self) -> None:
        model = self.make_model()
        with self.assertRaisesRegex(ValueError, "audio must not be empty"):
            await model.transcribe(b"")
        model.client.audio.transcriptions.create.assert_not_awaited()

    async def test_rejects_missing_filename(self) -> None:
        model = self.make_model()
        with self.assertRaisesRegex(ValueError, "filename must not be empty"):
            await model.transcribe(b"audio", "")
        model.client.audio.transcriptions.create.assert_not_awaited()

    async def test_credential_discovers_models(self) -> None:
        self.assertEqual(
            OpenAICredential.get_asr_model_classes(),
            [OpenAIASRModel],
        )
        cards = OpenAICredential.list_asr_models()
        self.assertEqual(
            {card.name for card in cards},
            {"gpt-4o-mini-transcribe", "gpt-4o-transcribe"},
        )
        self.assertTrue(all(card.type == "asr_model" for card in cards))
        self.assertTrue(all("audio/wav" in card.input_types for card in cards))
        self.assertTrue(
            all(
                "language" in card.parameter_schema["properties"]
                for card in cards
            ),
        )
