# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for provider ``error`` events on the DashScope realtime TTS
socket.

``DashScopeRealtimeTTSModel`` shares the OpenAI-style realtime event protocol
used by ``DashScopeRealtimeModel``, whose ``_parse`` turns
``{"type": "error", ...}`` into a ``ModelErrorEvent`` (pinned by
``tests/realtime_dashscope_test.py``). The TTS callback must not drop that
event: an abort after partial audio currently reads as a completed
synthesis.
"""

import base64
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, Mock, patch

from agentscope.credential import DashScopeCredential
from agentscope.tts import DashScopeRealtimeTTSModel, TTSResponse

# The streaming WAV header the model prepends, as in the DashScope TTS
# tests.
_WAV_HEADER_LEN = 44


def _mock_dashscope_modules() -> dict:
    """Mock the ``dashscope`` modules the model imports lazily."""
    mock_qwen = MagicMock()
    mock_qwen.QwenTtsRealtime = Mock
    mock_qwen.QwenTtsRealtimeCallback = Mock

    mock_audio = MagicMock()
    mock_audio.qwen_tts_realtime = mock_qwen

    mock_dashscope = MagicMock()
    mock_dashscope.api_key = None
    mock_dashscope.audio = mock_audio

    return {
        "dashscope": mock_dashscope,
        "dashscope.audio": mock_audio,
        "dashscope.audio.qwen_tts_realtime": mock_qwen,
    }


def _mock_tts_client() -> Mock:
    """A WebSocket client stub that records the outbound calls."""
    client = Mock()
    client.connect = Mock()
    client.close = Mock()
    client.finish = Mock()
    client.commit = Mock()
    client.update_session = Mock()
    client.append_text = Mock()
    return client


def _callback_class() -> Any:
    """Build the real callback class the model installs on the socket."""
    from agentscope.tts._dashscope._realtime_model import (
        _make_callback_class,
    )

    return _make_callback_class()


def _delta(pcm: bytes) -> dict:
    """An audio delta event as the SDK delivers it."""
    return {
        "type": "response.audio.delta",
        "delta": base64.b64encode(pcm).decode(),
    }


_ERROR_EVENT = {
    "type": "error",
    "error": {"code": "AudioTaskFailed", "message": "voice not found"},
}


class RealtimeTTSErrorEventTest(IsolatedAsyncioTestCase):
    """A provider ``error`` event must not look like finished audio."""

    def setUp(self) -> None:
        """Keep the mocked modules active for the whole test."""
        patcher = patch.dict("sys.modules", _mock_dashscope_modules())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _model(self, **kwargs: Any) -> DashScopeRealtimeTTSModel:
        """A connected realtime TTS model over a stub client."""
        defaults: dict[str, Any] = {
            "credential": DashScopeCredential(api_key="test"),
            "model": "qwen3-tts-flash-realtime",
            "stream": False,
            "max_retries": 1,
            "retry_delay": 0.0,
        }
        defaults.update(kwargs)
        model = DashScopeRealtimeTTSModel(**defaults)
        model._tts_client = _mock_tts_client()
        await model.connect()
        self.addAsyncCleanup(model.close)
        return model

    async def test_error_after_partial_audio_is_not_a_completed_synthesis(
        self,
    ) -> None:
        """``synthesize`` must raise when the provider aborts mid-audio."""
        model = await self._model()
        model._callback.on_event(_delta(b"HALFPCM"))
        model._callback.on_event(_ERROR_EVENT)
        # How the SDK ends the turn after an error: the socket closes.
        model._callback.on_close(None, None)

        with self.assertRaises(RuntimeError) as caught:
            await model.synthesize("Hello")

        self.assertEqual(
            str(caught.exception),
            "DashScope realtime TTS error "
            "(AudioTaskFailed): voice not found",
        )

    async def test_error_before_audio_raises_without_retrying(
        self,
    ) -> None:
        """A refusal carries the provider reason instead of "no audio"."""
        model = await self._model()
        client = model._tts_client

        model._callback.on_event(_ERROR_EVENT)
        model._callback.on_close(None, None)

        with self.assertRaises(RuntimeError) as caught:
            await model.synthesize("Hello")

        self.assertEqual(
            str(caught.exception),
            "DashScope realtime TTS error "
            "(AudioTaskFailed): voice not found",
        )
        self.assertEqual(client.commit.call_count, 1)

    async def test_finished_session_still_returns_audio(self) -> None:
        """The normal path is unchanged: deltas arrive, session finishes."""
        model = await self._model()
        model._callback.on_event(_delta(b"ALLPCM"))
        model._callback.on_event({"type": "session.finished"})

        response = await model.synthesize("Hello")

        self.assertIsInstance(response, TTSResponse)
        payload = base64.b64decode(response.content.source.data)
        self.assertEqual(
            {
                "magic": payload[:4],
                "wave_tag": payload[8:12],
                "pcm": payload[_WAV_HEADER_LEN:],
                "media_type": response.content.source.media_type,
                "is_last": response.is_last,
            },
            {
                "magic": b"RIFF",
                "wave_tag": b"WAVE",
                "pcm": b"ALLPCM",
                "media_type": "audio/wav",
                "is_last": True,
            },
        )
