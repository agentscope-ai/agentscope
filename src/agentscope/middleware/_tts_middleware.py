# -*- coding: utf-8 -*-
"""Middleware that turns reasoning text into speech and injects it as
``DATA_BLOCK_*`` events into the agent's event stream."""
from contextlib import AsyncExitStack, aclosing
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from ._base import MiddlewareBase
from .._logging import logger
from .._utils._common import _generate_id
from ..event import (
    CustomEvent,
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

    Provider failures propagate by default. With ``continue_on_error=True``,
    they disable speech for this reply and emit ``CustomEvent("tts_error")``
    with a reply ID and failure stage, while text continues. Upstream errors,
    injected consumer errors and task cancellation always propagate.
    """

    def __init__(
        self,
        tts_model: TTSModelBase,
        *,
        continue_on_error: bool = False,
    ) -> None:
        """Initialize the TTS middleware.

        Args:
            tts_model (`TTSModelBase`):
                The TTS model used to synthesize speech for assistant text
                blocks produced during reasoning.
            continue_on_error (`bool`, defaults to `False`):
                Emit a failure notification and continue text on TTS errors.
        """
        self.tts = tts_model
        self.continue_on_error = continue_on_error

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

        enabled = True
        cleanup_failed = False
        propagating_error: BaseException | None = None
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(self.tts)
        except Exception as exc:
            if not self.continue_on_error:
                raise
            logger.warning("Skipping TTS initialization: %s", exc)
            enabled = False

        try:
            if not enabled:
                yield self._failure_event(agent, "initialization")
            async for evt in next_handler(**input_kwargs):
                yield evt
                if not enabled:
                    continue
                push = False
                if isinstance(evt, TextBlockDeltaEvent):
                    text_buffer += evt.delta
                    if not self.tts.realtime or not evt.delta:
                        continue
                    text = evt.delta
                    push = True
                elif isinstance(evt, TextBlockEndEvent):
                    text = text_buffer
                    text_buffer = ""
                else:
                    continue

                # Catch errors from advancing TTS only. Outward yields must
                # stay outside the handler so consumer athrow() propagates.
                pending = self._audio_events(
                    agent,
                    text,
                    push,
                    audio_block_id,
                    audio_media_type,
                )
                async with aclosing(pending):
                    while True:
                        try:
                            audio_evt = await anext(pending)
                        except StopAsyncIteration:
                            break
                        except Exception as exc:
                            if not self.continue_on_error:
                                raise
                            logger.warning(
                                "Skipping TTS for the rest of this reply: %s",
                                exc,
                            )
                            enabled = False
                            text_buffer = ""
                            break
                        if isinstance(audio_evt, DataBlockStartEvent):
                            audio_block_id = audio_evt.block_id
                            audio_media_type = audio_evt.media_type
                        yield audio_evt

                if not push or not enabled:
                    if audio_block_id is not None:
                        yield DataBlockEndEvent(
                            reply_id=agent.state.reply_id,
                            block_id=audio_block_id,
                        )
                    audio_block_id = None
                    audio_media_type = None
                if not enabled:
                    yield self._failure_event(agent, "synthesis")
        except BaseException as exc:
            propagating_error = exc
            raise
        finally:
            error = (
                (
                    type(propagating_error),
                    propagating_error,
                    propagating_error.__traceback__,
                )
                if propagating_error is not None
                else (None, None, None)
            )
            try:
                await stack.__aexit__(*error)
            except Exception as exc:
                # Cleanup must not replace an upstream/consumer exception.
                if propagating_error is None and not self.continue_on_error:
                    raise
                logger.warning("TTS cleanup failed: %s", exc)
                cleanup_failed = True
        if cleanup_failed and enabled:
            yield self._failure_event(agent, "cleanup")

    @staticmethod
    def _failure_event(agent: "Agent", stage: str) -> CustomEvent:
        """Notify consumers without exposing upstream response contents."""
        return CustomEvent(
            name="tts_error",
            value={"reply_id": agent.state.reply_id, "stage": stage},
        )

    async def _audio_events(
        self,
        agent: "Agent",
        text: str,
        push: bool,
        audio_block_id: str | None,
        audio_media_type: str | None,
    ) -> AsyncGenerator:
        """Perform one TTS operation and yield its incremental audio."""
        if push:
            result = await self.tts.push(text)
            events = self._emit_chunk(
                agent,
                result,
                audio_block_id,
                audio_media_type,
            )
        else:
            if not self.tts.realtime and not text.strip():
                return
            result = (
                await self.tts.synthesize()
                if self.tts.realtime
                else await self.tts.synthesize(text)
            )
            events = self._emit_synth_result(
                agent,
                result,
                audio_block_id,
                audio_media_type,
            )
        async with aclosing(events):
            async for event in events:
                yield event

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
            async with aclosing(res):
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
            audio_block_id = _generate_id()
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
