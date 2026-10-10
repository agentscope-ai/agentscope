# -*- coding: utf-8 -*-
"""Unit tests for batch and realtime ASR model behavior."""

import asyncio
from collections.abc import AsyncGenerator
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
        self.partial_received = asyncio.Event()
        self.responses: asyncio.Queue[ASRResponse | None] = asyncio.Queue()
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
    ) -> None:
        """Send audio and queue an incremental transcription event."""
        text = audio.decode("utf-8")
        self.events.append(f"push:{text}")
        self.push_options.append(kwargs)
        self.response_index += 1
        await self.responses.put(
            ASRResponse(
                content=TextBlock(
                    text=text,
                    id=f"text-id-{self.response_index}",
                    created_at=f"text-created-at-{self.response_index}",
                ),
                id=f"response-id-{self.response_index}",
                created_at=f"response-created-at-{self.response_index}",
                is_last=False,
            ),
        )

    async def finish(self) -> None:
        """Queue the last text delta and close the response stream."""
        self.events.append("finish")
        self.response_index += 1
        await self.responses.put(
            ASRResponse(
                content=TextBlock(
                    text=".",
                    id=f"text-id-{self.response_index}",
                    created_at=f"text-created-at-{self.response_index}",
                ),
                id=f"response-id-{self.response_index}",
                created_at=f"response-created-at-{self.response_index}",
            ),
        )
        await self.responses.put(None)

    def receive(self) -> AsyncGenerator[ASRResponse, None]:
        """Return the asynchronous transcription event iterator."""
        return self._receive()

    async def _receive(self) -> AsyncGenerator[ASRResponse, None]:
        """Yield queued transcription events until input is finished."""
        self.events.append("receive")
        while (response := await self.responses.get()) is not None:
            self.events.append(f"receive:{response.content.text}")
            if not response.is_last:
                self.partial_received.set()
            yield response
        self.events.append("receive_done")

    async def transcribe(  # pylint: disable=unused-argument
        self,
        audio: bytes,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Reject batch transcription in this realtime-only test model."""
        raise NotImplementedError(
            f"{type(self).__name__} only supports realtime transcription",
        )


class TestASRModelBase(IsolatedAsyncioTestCase):
    """Test the shared ASR model lifecycle."""

    async def test_realtime_lifecycle(self) -> None:
        """Send audio while receiving incremental text results."""
        model = _RealtimeASRModel(OpenAICredential(api_key="test"))

        async def audio_chunks() -> AsyncGenerator[bytes, None]:
            """Wait for the first partial result before sending more audio."""
            yield b"hello "
            await model.partial_received.wait()
            yield b"world"

        responses = [
            response
            async for response in model.transcribe_stream(
                audio_chunks(),
                sample_rate=16000,
            )
        ]

        self.assertEqual(
            model.events,
            [
                "connect",
                "receive",
                "push:hello ",
                "receive:hello ",
                "push:world",
                "finish",
                "receive:world",
                "receive:.",
                "receive_done",
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

    async def test_batch_model_rejects_realtime_transcription(self) -> None:
        """Reject the realtime helper for a batch-only model."""
        model = OpenAIASRModel(
            credential=OpenAICredential(api_key="test"),
        )

        async def audio_chunks() -> AsyncGenerator[bytes, None]:
            """Provide one audio chunk."""
            yield b"audio"

        with self.assertRaisesRegex(
            RuntimeError,
            "OpenAIASRModel does not support realtime transcription",
        ):
            async for _ in model.transcribe_stream(audio_chunks()):
                pass


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
