# -*- coding: utf-8 -*-
"""Unit tests for the batch ASR reference implementation."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.asr import ASRModelBase, ASRResponse, OpenAIASRModel
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock


class TestOpenAIASRModel(IsolatedAsyncioTestCase):
    """Test transcription without making network requests."""

    def test_response_uses_timestamp_factory(self) -> None:
        """Use the configurable timestamp factory for response creation."""
        content = TextBlock(
            text="Hello world",
            id="text-id",
            created_at="text-created-at",
        )
        with patch(
            "agentscope._utils._common._timestamp_factory",
            return_value="FROZEN-TS",
        ):
            response = ASRResponse(
                content=content,
                id="response-id",
            )

        self.assertEqual(
            dict(response),
            {
                "content": content,
                "id": "response-id",
                "created_at": "FROZEN-TS",
                "type": "asr",
                "metadata": None,
            },
        )

    def make_model(
        self,
        parameters: OpenAIASRModel.Parameters | None = None,
    ) -> OpenAIASRModel:
        """Build a model whose transcription call is stubbed out."""
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
        """Transcribe audio and send the default model and format."""
        model = self.make_model()
        with (
            patch(
                "agentscope._utils._common._id_factory",
                return_value="text-id",
            ),
            patch(
                "agentscope._utils._common._timestamp_factory",
                return_value="FROZEN-TS",
            ),
            patch(
                "agentscope.asr._response._get_timestamp",
                return_value="response-id",
            ),
        ):
            response = await model.transcribe(
                b"audio bytes",
                "sample.mp3",
            )

        self.assertIsInstance(model, ASRModelBase)
        self.assertIsInstance(response, ASRResponse)
        self.assertEqual(
            dict(response),
            {
                "content": TextBlock(
                    text="Hello world",
                    id="text-id",
                    created_at="FROZEN-TS",
                ),
                "id": "response-id",
                "created_at": "FROZEN-TS",
                "type": "asr",
                "metadata": None,
            },
        )
        model.client.audio.transcriptions.create.assert_awaited_once_with(
            file=("sample.mp3", b"audio bytes"),
            model="gpt-4o-mini-transcribe",
            response_format="json",
        )

    async def test_forwards_parameters(self) -> None:
        """Forward the optional language and prompt parameters."""
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
        """Reject empty audio before reaching the API."""
        model = self.make_model()
        with self.assertRaisesRegex(ValueError, "audio must not be empty"):
            await model.transcribe(b"")
        model.client.audio.transcriptions.create.assert_not_awaited()

    async def test_rejects_missing_filename(self) -> None:
        """Reject a missing filename before reaching the API."""
        model = self.make_model()
        with self.assertRaisesRegex(ValueError, "filename must not be empty"):
            await model.transcribe(b"audio", "")
        model.client.audio.transcriptions.create.assert_not_awaited()

    async def test_credential_discovers_models(self) -> None:
        """Expose the ASR model class and its cards via the credential."""
        self.assertEqual(
            OpenAICredential.get_asr_model_classes(),
            [OpenAIASRModel],
        )
        cards = OpenAICredential.list_asr_models()
        parameter_schema = OpenAIASRModel.Parameters.model_json_schema()
        self.assertEqual(
            [card.model_dump(mode="json") for card in cards],
            [
                {
                    "type": "asr_model",
                    "name": "gpt-4o-mini-transcribe",
                    "label": "GPT-4o Mini Transcribe",
                    "input_types": [
                        "audio/flac",
                        "audio/mpeg",
                        "audio/mp4",
                        "audio/ogg",
                        "audio/wav",
                        "audio/webm",
                    ],
                    "output_types": ["text/plain"],
                    "parameter_schema": parameter_schema,
                },
                {
                    "type": "asr_model",
                    "name": "gpt-4o-transcribe",
                    "label": "GPT-4o Transcribe",
                    "input_types": [
                        "audio/flac",
                        "audio/mpeg",
                        "audio/mp4",
                        "audio/ogg",
                        "audio/wav",
                        "audio/webm",
                    ],
                    "output_types": ["text/plain"],
                    "parameter_schema": parameter_schema,
                },
            ],
        )
