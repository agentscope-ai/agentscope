# -*- coding: utf-8 -*-
"""DashScope realtime speech recognition implementation."""

import asyncio
import json
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field

from ...credential import DashScopeCredential
from ...message import TextBlock
from .._asr_base import ASRModelBase
from .._asr_response import ASRResponse


_CONNECT_TIMEOUT_SECONDS = 15.0
_FINISH_TIMEOUT_SECONDS = 30.0
_REALTIME_PATH = "/api-ws/v1/inference"


def _realtime_url(base_url: str) -> str:
    """Build the native DashScope inference WebSocket endpoint."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid DashScope base_url: {base_url!r}.")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}{_REALTIME_PATH}"


class DashScopeRealtimeASRModel(ASRModelBase):
    """Realtime ASR through DashScope's native streaming protocol."""

    class Parameters(BaseModel):
        """Options supported by Qwen Audio streaming ASR models."""

        sample_rate: int = Field(
            default=16000,
            gt=0,
            description=(
                "PCM input sample rate in Hz. Browser sessions set this "
                "from their AudioContext."
            ),
        )
        language_hints: list[str] | None = Field(
            default=None,
            max_length=4,
            description=(
                "Optional language codes. Up to four languages are supported."
            ),
        )
        keep_dialect: bool = Field(
            default=False,
            description="Preserve dialect expressions in the transcript.",
        )
        vad_model: Literal[
            "near_meeting_16k",
            "far_field_meeting_16k",
        ] = Field(
            default="far_field_meeting_16k",
            description="Voice activity detection profile.",
        )
        vocabulary_id: str | None = Field(
            default=None,
            description="DashScope hotword vocabulary identifier.",
        )
        vocabulary: dict[str, int] | None = Field(
            default=None,
            description="Inline hotwords mapped to recognition weights.",
        )
        semantic_punctuation_enabled: bool = Field(
            default=True,
            description="Use semantic punctuation in recognition results.",
        )

    type: Literal["dashscope_realtime_asr"] = "dashscope_realtime_asr"
    realtime: bool = True

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "qwen-audio-3.1-asr-flash-streaming",
        parameters: "DashScopeRealtimeASRModel.Parameters | None" = None,
    ) -> None:
        """Initialize a DashScope realtime ASR model.

        Args:
            credential (`DashScopeCredential`):
                The credential used to authenticate with DashScope.
            model (`str`, defaults to
            ``"qwen-audio-3.1-asr-flash-streaming"``):
                The DashScope realtime ASR model name.
            parameters (`DashScopeRealtimeASRModel.Parameters | None`,
            defaults to `None`):
                Streaming recognition parameters, including the PCM sample
                rate. Default parameters are used when omitted.
        """
        super().__init__(credential, model, parameters)
        self._url = _realtime_url(credential.base_url)
        self._api_key = credential.api_key.get_secret_value()
        self._websocket: Any = None
        self._reader: asyncio.Task | None = None
        self._started = asyncio.Event()
        self._finished = asyncio.Event()
        self._error: Exception | None = None
        self._responses: asyncio.Queue[ASRResponse] = asyncio.Queue()
        self._task_id = ""
        self._committed_sentences: list[str] = []
        self._current_sentence = ""

    async def connect(self) -> None:
        """Connect to DashScope and start one recognition task.

        Calling this method resets any previous task state and waits until
        DashScope confirms that the new task has started.

        Raises:
            RuntimeError:
                If DashScope rejects the task or does not start it before the
                timeout.
        """
        import websockets

        await self.close()
        self._started.clear()
        self._finished.clear()
        self._error = None
        self._responses = asyncio.Queue()
        self._task_id = uuid4().hex
        self._committed_sentences = []
        self._current_sentence = ""
        self._websocket = await websockets.connect(
            self._url,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
                "X-DashScope-DataInspection": "disable",
            },
        )
        websocket = self._websocket
        self._reader = asyncio.create_task(
            self._read_events(websocket),
            name="dashscope-asr",
        )
        await self._send_json(self._run_task_event())
        try:
            await asyncio.wait_for(
                self._started.wait(),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as error:
            await self.close()
            raise RuntimeError(
                "Timed out waiting for DashScope ASR task startup.",
            ) from error
        try:
            self._raise_provider_error()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """Cancel the provider reader and close the active WebSocket."""
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            await websocket.close()

    async def push(
        self,
        audio: bytes,
        **kwargs: Any,
    ) -> ASRResponse:
        """Send one PCM16 chunk and return the latest available text.

        The audio sample rate must match :attr:`Parameters.sample_rate`.

        Args:
            audio (`bytes`):
                An incremental mono PCM16 audio chunk.
            **kwargs (`Any`):
                Reserved for interface compatibility and currently ignored.

        Returns:
            `ASRResponse`:
                The newest cumulative partial transcript, or an empty
                non-final response when no new text is available.

        Raises:
            `RuntimeError`:
                If the model is not connected or the provider task failed.
        """
        del kwargs
        if not audio:
            return ASRResponse(content=None, is_last=False)
        if self._websocket is None:
            raise RuntimeError("DashScope ASR model is not connected.")
        self._raise_provider_error()
        await self._websocket.send(audio)
        await asyncio.sleep(0)
        self._raise_provider_error()
        return self._drain_latest_response()

    async def transcribe(
        self,
        audio: bytes | None = None,
        filename: str = "audio.pcm",
        **kwargs: Any,
    ) -> ASRResponse:
        """Finish the current stream and return its final transcription.

        Args:
            audio (`bytes | None`, defaults to `None`):
                An optional final mono PCM16 audio chunk.
            filename (`str`, defaults to ``"audio.pcm"``):
                Reserved for interface compatibility and currently ignored.
            **kwargs (`Any`):
                Reserved for interface compatibility and currently ignored.

        Returns:
            `ASRResponse`:
                The final cumulative transcript with ``is_last=True``.

        Raises:
            `RuntimeError`:
                If the model is not connected, the provider task failed, or
                the final result does not arrive before the timeout.
        """
        del filename, kwargs
        if audio:
            await self.push(audio)
        if self._websocket is None:
            raise RuntimeError("DashScope ASR model is not connected.")
        self._raise_provider_error()
        await self._send_json(
            {
                "header": {
                    "action": "finish-task",
                    "task_id": self._task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {}},
            },
        )
        try:
            await asyncio.wait_for(
                self._finished.wait(),
                timeout=_FINISH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as error:
            raise RuntimeError(
                "Timed out waiting for DashScope ASR final result.",
            ) from error
        self._raise_provider_error()
        text = self._complete_text()
        return ASRResponse(
            content=TextBlock(text=text) if text else None,
            is_last=True,
        )

    def _run_task_event(self) -> dict[str, Any]:
        """Build DashScope's initial run-task event."""
        parameters = self.parameters.model_dump(exclude_none=True)
        parameters["format"] = "pcm"
        return {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": parameters,
                "input": {},
            },
        }

    async def _send_json(self, event: dict[str, Any]) -> None:
        """Send one JSON protocol event."""
        if self._websocket is None:
            raise RuntimeError("DashScope ASR model is not connected.")
        await self._websocket.send(json.dumps(event, ensure_ascii=False))

    async def _read_events(self, websocket: Any) -> None:
        """Read provider events until the task finishes or fails."""
        try:
            async for raw_event in websocket:
                if not isinstance(raw_event, str):
                    continue
                event = json.loads(raw_event)
                header = event.get("header", {})
                event_type = header.get("event")
                if event_type == "task-started":
                    self._started.set()
                elif event_type == "result-generated":
                    self._handle_result(event)
                elif event_type == "task-finished":
                    self._finished.set()
                    return
                elif event_type == "task-failed":
                    self._error = RuntimeError(
                        self._provider_error_message(header),
                    )
                    self._started.set()
                    self._finished.set()
                    return
        except Exception as error:
            self._error = RuntimeError(
                f"DashScope ASR connection failed: {error}",
            )
            self._started.set()
            self._finished.set()
        finally:
            if not self._finished.is_set() and self._error is None:
                self._error = RuntimeError(
                    "DashScope ASR connection closed unexpectedly.",
                )
                self._started.set()
                self._finished.set()

    def _handle_result(self, event: dict[str, Any]) -> None:
        """Accumulate one partial or completed sentence."""
        sentence = (
            event.get("payload", {})
            .get("output", {})
            .get(
                "sentence",
                {},
            )
        )
        text = sentence.get("text")
        if not isinstance(text, str):
            return
        text = text.strip()
        self._current_sentence = text
        transcript = self._complete_text()
        if transcript:
            self._responses.put_nowait(
                ASRResponse(
                    content=TextBlock(text=transcript),
                    is_last=False,
                ),
            )
        if sentence.get("sentence_end") and text:
            self._committed_sentences.append(text)
            self._current_sentence = ""

    def _complete_text(self) -> str:
        """Return committed sentences plus the active partial sentence."""
        parts = [*self._committed_sentences]
        if self._current_sentence:
            parts.append(self._current_sentence)
        result = ""
        for part in parts:
            if result and result[-1].isalnum() and part[0].isalnum():
                result = f"{result} {part}"
            else:
                result = f"{result}{part}"
        return result

    def _drain_latest_response(self) -> ASRResponse:
        """Return the newest queued partial response without blocking."""
        latest: ASRResponse | None = None
        while not self._responses.empty():
            latest = self._responses.get_nowait()
        return latest or ASRResponse(content=None, is_last=False)

    def _raise_provider_error(self) -> None:
        """Raise a provider failure captured by the reader task."""
        if self._error is not None:
            raise self._error

    @staticmethod
    def _provider_error_message(header: dict[str, Any]) -> str:
        """Build a readable provider failure message."""
        code = header.get("error_code") or header.get("code") or "unknown"
        message = (
            header.get("error_message")
            or header.get("message")
            or "Unknown provider error"
        )
        return f"DashScope ASR task failed ({code}): {message}"
