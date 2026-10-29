# -*- coding: utf-8 -*-
"""Unit tests for batch and realtime ASR model behavior."""

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.asr import ASRModelBase, ASRResponse, OpenAIASRModel
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock


class _RealtimeASRModel(ASRModelBase):
    """Minimal realtime implementation used to verify the base lifecycle."""

    realtime: bool = True

    def __init__(self, credential: OpenAICredential) -> None:
        """Initialize the test model."""
        super().__init__(credential=credential, model="realtime-test")
        self.events: list[str] = []
        self.push_options: list[dict[str, Any]] = []
        self.response_index = 0

    async def connect(self) -> None:
        """Record connection establishment."""
        self.events.append("connect")

    async def close(self) -> None:
        """Record connection closure."""
        self.events.append("close")

    async def push(
        self,
        audio: bytes,
        **kwargs: Any,
    ) -> ASRResponse:
        """Send audio and return an available transcription event."""
        text = audio.decode("utf-8")
        self.events.append(f"push:{text}")
        self.push_options.append(kwargs)
        self.response_index += 1
        content = (
            TextBlock(
                text=text,
                id=f"text-id-{self.response_index}",
                created_at=f"text-created-at-{self.response_index}",
            )
            if text
            else None
        )
        return ASRResponse(
            content=content,
            id=f"response-id-{self.response_index}",
            created_at=f"response-created-at-{self.response_index}",
            is_last=False,
        )

    async def transcribe(  # pylint: disable=unused-argument
        self,
        audio: bytes | None = None,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Finalize realtime input and return the last event."""
        text = f"{audio.decode('utf-8')}." if audio is not None else "."
        self.events.append(
            f"transcribe:{audio.decode('utf-8')}"
            if audio is not None
            else "transcribe",
        )
        self.response_index += 1
        return ASRResponse(
            content=TextBlock(
                text=text,
                id=f"text-id-{self.response_index}",
                created_at=f"text-created-at-{self.response_index}",
            ),
            id=f"response-id-{self.response_index}",
            created_at=f"response-created-at-{self.response_index}",
        )


class _IncompleteASRModel(ASRModelBase):
    """ASR implementation missing the required transcription method."""


class TestASRModelBase(IsolatedAsyncioTestCase):
    """Test the shared ASR model lifecycle."""

    def test_requires_transcribe_implementation(self) -> None:
        """Reject concrete models without a transcription implementation."""
        model_class: Any = _IncompleteASRModel

        with self.assertRaisesRegex(
            TypeError,
            "Can't instantiate abstract class _IncompleteASRModel",
        ):
            type.__call__(
                model_class,
                credential=OpenAICredential(api_key="test"),
                model="incomplete-test",
            )

    async def test_realtime_lifecycle(self) -> None:
        """Push incremental audio and finalize within one connection."""
        model = _RealtimeASRModel(OpenAICredential(api_key="test"))

        async with model:
            responses = [
                await model.push(b"hello ", sample_rate=16000),
                await model.push(b"world", sample_rate=16000),
                await model.transcribe(),
            ]

        self.assertEqual(
            model.events,
            [
                "connect",
                "push:hello ",
                "push:world",
                "transcribe",
                "close",
            ],
        )
        self.assertEqual(
            model.push_options,
            [
                {"sample_rate": 16000},
                {"sample_rate": 16000},
            ],
        )
        self.assertEqual(
            [dict(response) for response in responses],
            [
                {
                    "content": TextBlock(
                        text="hello ",
                        id="text-id-1",
                        created_at="text-created-at-1",
                    ),
                    "id": "response-id-1",
                    "created_at": "response-created-at-1",
                    "type": "asr",
                    "metadata": None,
                    "is_last": False,
                },
                {
                    "content": TextBlock(
                        text="world",
                        id="text-id-2",
                        created_at="text-created-at-2",
                    ),
                    "id": "response-id-2",
                    "created_at": "response-created-at-2",
                    "type": "asr",
                    "metadata": None,
                    "is_last": False,
                },
                {
                    "content": TextBlock(
                        text=".",
                        id="text-id-3",
                        created_at="text-created-at-3",
                    ),
                    "id": "response-id-3",
                    "created_at": "response-created-at-3",
                    "type": "asr",
                    "metadata": None,
                    "is_last": True,
                },
            ],
        )

    async def test_closes_realtime_model_on_error(self) -> None:
        """Close the realtime connection when processing fails."""
        model = _RealtimeASRModel(OpenAICredential(api_key="test"))

        with self.assertRaisesRegex(RuntimeError, "processing stopped"):
            async with model:
                response = await model.push(b"hello")
                raise RuntimeError("processing stopped")

        self.assertEqual(
            dict(response),
            {
                "content": TextBlock(
                    text="hello",
                    id="text-id-1",
                    created_at="text-created-at-1",
                ),
                "id": "response-id-1",
                "created_at": "response-created-at-1",
                "type": "asr",
                "metadata": None,
                "is_last": False,
            },
        )
        self.assertEqual(
            model.events,
            [
                "connect",
                "push:hello",
                "close",
            ],
        )

    async def test_returns_empty_realtime_response(self) -> None:
        """Represent a push with no available text using empty content."""
        model = _RealtimeASRModel(OpenAICredential(api_key="test"))

        async with model:
            responses = [
                await model.push(b""),
                await model.push(b"hello"),
                await model.transcribe(),
            ]

        self.assertEqual(
            [dict(response) for response in responses],
            [
                {
                    "content": None,
                    "id": "response-id-1",
                    "created_at": "response-created-at-1",
                    "type": "asr",
                    "metadata": None,
                    "is_last": False,
                },
                {
                    "content": TextBlock(
                        text="hello",
                        id="text-id-2",
                        created_at="text-created-at-2",
                    ),
                    "id": "response-id-2",
                    "created_at": "response-created-at-2",
                    "type": "asr",
                    "metadata": None,
                    "is_last": False,
                },
                {
                    "content": TextBlock(
                        text=".",
                        id="text-id-3",
                        created_at="text-created-at-3",
                    ),
                    "id": "response-id-3",
                    "created_at": "response-created-at-3",
                    "type": "asr",
                    "metadata": None,
                    "is_last": True,
                },
            ],
        )
        self.assertEqual(
            model.events,
            [
                "connect",
                "push:",
                "push:hello",
                "transcribe",
                "close",
            ],
        )

    async def test_realtime_transcribe_accepts_final_audio(self) -> None:
        """Send an optional final chunk while finalizing realtime input."""
        model = _RealtimeASRModel(OpenAICredential(api_key="test"))

        async with model:
            response = await model.transcribe(b"last")

        self.assertEqual(
            dict(response),
            {
                "content": TextBlock(
                    text="last.",
                    id="text-id-1",
                    created_at="text-created-at-1",
                ),
                "id": "response-id-1",
                "created_at": "response-created-at-1",
                "type": "asr",
                "metadata": None,
                "is_last": True,
            },
        )
        self.assertEqual(
            model.events,
            [
                "connect",
                "transcribe:last",
                "close",
            ],
        )

    async def test_batch_model_rejects_realtime_input(self) -> None:
        """Reject realtime operations on a batch-only model."""
        model = OpenAIASRModel(
            credential=OpenAICredential(api_key="test"),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "OpenAIASRModel does not support realtime audio input",
        ):
            await model.push(b"audio")


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
                metadata={"language": "en"},
                is_last=False,
            )

        self.assertEqual(
            dict(response),
            {
                "content": content,
                "id": "response-id",
                "created_at": "FROZEN-TS",
                "type": "asr",
                "metadata": {"language": "en"},
                "is_last": False,
            },
        )
        self.assertEqual(
            (response.metadata, response.is_last),
            ({"language": "en"}, False),
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
                "agentscope.asr._asr_response._get_timestamp",
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
                "is_last": True,
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

    async def test_call_parameters_override_model_parameters(self) -> None:
        """Prefer per-call options over configured model parameters."""
        model = self.make_model(
            OpenAIASRModel.Parameters(language="en", prompt="AgentScope"),
        )
        await model.transcribe(
            b"audio",
            language="fr",
            prompt="Override",
        )

        model.client.audio.transcriptions.create.assert_awaited_once_with(
            file=("audio.wav", b"audio"),
            model="gpt-4o-mini-transcribe",
            response_format="json",
            language="fr",
            prompt="Override",
        )

    async def test_rejects_empty_audio(self) -> None:
        """Reject empty audio before reaching the API."""
        model = self.make_model()
        for audio in [None, b""]:
            with (
                self.subTest(audio=audio),
                self.assertRaisesRegex(
                    ValueError,
                    "audio must not be empty",
                ),
            ):
                await model.transcribe(audio)
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
                    "realtime": False,
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
                    "realtime": False,
                    "parameter_schema": parameter_schema,
                },
            ],
        )
