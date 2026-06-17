# -*- coding: utf-8 -*-
"""Middleware that turns reasoning text into speech and injects it as
``DATA_BLOCK_*`` events into the agent's event stream."""
import uuid
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from ._base import MiddlewareBase
from ..event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from ..tts import TTSModelBase, TTSResponse

if TYPE_CHECKING:
    from ..agent import Agent


class TTSMiddleware(MiddlewareBase):
    """Synthesize speech for every text block produced during reasoning and
    inject the audio as ``DATA_BLOCK_*`` events into the stream.

    - Non-realtime TTS (``realtime=False``): on each
      ``TextBlockEndEvent`` the accumulated text is sent to
      :meth:`TTSModelBase.synthesize`; the resulting audio chunks are
      emitted as one ``DATA_BLOCK_START`` + N ``DATA_BLOCK_DELTA`` +
      ``DATA_BLOCK_END``.
    - Realtime TTS (``realtime=True``): each
      ``TextBlockDeltaEvent`` is pushed into the model via
      :meth:`TTSModelBase.push`; any audio produced is emitted immediately.
      On ``TextBlockEndEvent`` :meth:`TTSModelBase.synthesize` is called
      to drain remaining audio, and the data block is closed.

    Each ``DataBlockDeltaEvent.data`` carries an **incremental** base64 PCM
    chunk; the full audio is the concatenation of every delta's decoded
    bytes (the data block is keyed by ``block_id``).
    """

    def __init__(self, tts_model: TTSModelBase) -> None:
        """Initialize the TTS middleware.

        Args:
            tts_model (`TTSModelBase`):
                The TTS model used to synthesize speech for assistant text
                blocks produced during reasoning.
        """
        self.tts = tts_model

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept the reply stream, synthesize speech for text blocks,
        and inject ``DATA_BLOCK_*`` audio events into the output."""
        text_buffer: str = ""
        audio_block_id: str | None = None
        audio_media_type: str | None = None

        async with self.tts:
            async for evt in next_handler(**input_kwargs):
                yield evt

                if isinstance(evt, TextBlockDeltaEvent):
                    text_buffer += evt.delta
                    if self.tts.realtime and evt.delta:
                        tts_res = await self.tts.push(evt.delta)
                        async for audio_evt in self._emit_chunk(
                            agent,
                            tts_res,
                            audio_block_id,
                            audio_media_type,
                        ):
                            if isinstance(audio_evt, DataBlockStartEvent):
                                audio_block_id = audio_evt.block_id
                                audio_media_type = audio_evt.media_type
                            yield audio_evt

                elif isinstance(evt, TextBlockEndEvent):
                    text = text_buffer
                    text_buffer = ""

                    if self.tts.realtime:
                        res = await self.tts.synthesize()
                        async for audio_evt in self._emit_synth_result(
                            agent,
                            res,
                            audio_block_id,
                            audio_media_type,
                        ):
                            if isinstance(audio_evt, DataBlockStartEvent):
                                audio_block_id = audio_evt.block_id
                                audio_media_type = audio_evt.media_type
                            yield audio_evt
                    elif text.strip():
                        res = await self.tts.synthesize(text)
                        async for audio_evt in self._emit_synth_result(
                            agent,
                            res,
                            audio_block_id,
                            audio_media_type,
                        ):
                            if isinstance(audio_evt, DataBlockStartEvent):
                                audio_block_id = audio_evt.block_id
                                audio_media_type = audio_evt.media_type
                            yield audio_evt

                    if audio_block_id is not None:
                        yield DataBlockEndEvent(
                            reply_id=agent.state.reply_id,
                            block_id=audio_block_id,
                        )
                    audio_block_id = None
                    audio_media_type = None

    async def _emit_synth_result(
        self,
        agent: "Agent",
        res: TTSResponse | AsyncGenerator[TTSResponse, None],
        audio_block_id: str | None,
        audio_media_type: str | None,
    ) -> AsyncGenerator:
        """Normalize ``synthesize()`` returns (single response or async
        generator) into a stream of ``DATA_BLOCK_*`` events."""
        if isinstance(res, AsyncGenerator):
            async for chunk in res:
                async for ae in self._emit_chunk(
                    agent,
                    chunk,
                    audio_block_id,
                    audio_media_type,
                ):
                    if isinstance(ae, DataBlockStartEvent):
                        audio_block_id = ae.block_id
                        audio_media_type = ae.media_type
                    yield ae
        else:
            async for ae in self._emit_chunk(
                agent,
                res,
                audio_block_id,
                audio_media_type,
            ):
                yield ae

    @staticmethod
    async def _emit_chunk(
        agent: "Agent",
        tts_res: TTSResponse | None,
        audio_block_id: str | None,
        audio_media_type: str | None,
    ) -> AsyncGenerator:
        """Emit one TTSResponse chunk as ``DATA_BLOCK_START`` (if needed)
        followed by ``DATA_BLOCK_DELTA``."""
        if tts_res is None or tts_res.content is None:
            return
        media_type = tts_res.content.source.media_type
        data = tts_res.content.source.data
        if not data:
            return

        if audio_block_id is None:
            audio_block_id = uuid.uuid4().hex
            audio_media_type = media_type
            yield DataBlockStartEvent(
                reply_id=agent.state.reply_id,
                block_id=audio_block_id,
                media_type=media_type,
            )

        yield DataBlockDeltaEvent(
            reply_id=agent.state.reply_id,
            block_id=audio_block_id,
            data=data,
            media_type=audio_media_type,
        )
