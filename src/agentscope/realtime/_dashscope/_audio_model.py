# -*- coding: utf-8 -*-
"""The DashScope Qwen-Audio realtime model."""
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ._model import DashScopeRealtimeModel
from .. import _events as me
from .._base import RealtimeModelBase
from .._model_card import RealtimeModelCard
from ..._logging import logger


class DashScopeAudioRealtimeModel(DashScopeRealtimeModel):
    """The Qwen-Audio-3.0-Realtime API over WebSocket.

    Same endpoint and framing as Qwen-Omni, but it accepts text turns,
    spells both PCM formats as ``"pcm"``, and offers ``smart_turn``
    endpointing in place of ``semantic_vad``.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Tuneables surfaced to the UI."""

        voice: str = Field(default="longanqian", title="Voice")
        turn_detection: Literal["server_vad", "smart_turn", "none"] = Field(
            default="server_vad",
            title="Turn Detection",
            description="``none`` hands endpointing to the caller.",
        )
        vad_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
        vad_silence_duration_ms: int = Field(default=800, ge=200, le=6000)
        voiceprint_audio_urls: list[str] = Field(
            default_factory=list,
            title="Voiceprint Audio URLs",
            description="Speaker references, used by ``smart_turn`` only.",
        )
        max_history_turns: int = Field(default=20, ge=1, le=50)

    type = "dashscope_audio_realtime"
    supports_text_input = True

    parameters: "DashScopeAudioRealtimeModel.Parameters"

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list[RealtimeModelCard]:
        """List the Qwen-Audio cards, which live beside the Omni ones."""
        return super().list_models(
            custom_yaml_dir or str(Path(__file__).parent / "_audio_models"),
        )

    async def push_text(self, text: str) -> None:
        """Send a text user turn and ask for the reply."""
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        )
        await self.request_response()

    def _session_update(
        self,
        instructions: str,
        tools: list[dict] | None,
    ) -> dict:
        """Build the ``session.update`` message. ``voice`` only takes
        effect here, on the first update of the session."""
        p = self.parameters
        turn_detection: dict[str, Any] | None = None
        if p.turn_detection == "server_vad":
            turn_detection = {
                "type": "server_vad",
                "threshold": p.vad_threshold,
                "silence_duration_ms": p.vad_silence_duration_ms,
            }
        elif p.turn_detection == "smart_turn":
            urls = p.voiceprint_audio_urls
            turn_detection = {"type": "smart_turn"}
            if urls:
                turn_detection["voiceprint_audio_urls"] = urls

        session: dict[str, Any] = {
            "instructions": instructions,
            "modalities": ["audio", "text"],
            "voice": p.voice,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "max_history_turns": p.max_history_turns,
            "turn_detection": turn_detection,
        }
        if tools and self.card.supports_tools:
            session["tools"] = tools
        return {"type": "session.update", "session": session}

    def _parse(self, data: dict) -> me.ModelEvent | None:
        """Translate one server frame, dropping the two families that are
        specific to this API before falling back to the Omni parser."""
        match data.get("type", ""):
            # Speech that ``smart_turn`` judged not to be addressed at the
            # model, and the progress of voiceprint registration.
            case "conversation.item.ambient_audio_transcription.delta":
                logger.debug(
                    "DashScopeAudioRealtimeModel: ambient speech %r",
                    data.get("delta", ""),
                )
                return None

            case (
                "voiceprint_audio_list.in_progress"
                | "voiceprint_audio_list.completed"
                | "voiceprint_audio_list.failed"
            ) as kind:
                logger.debug("DashScopeAudioRealtimeModel: %s", kind)
                return None

            case _:
                return super()._parse(data)
