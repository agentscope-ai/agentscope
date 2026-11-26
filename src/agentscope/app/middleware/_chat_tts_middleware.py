# -*- coding: utf-8 -*-
"""Apply a speech configuration to one reply, including parked resumes."""
from contextlib import aclosing
from typing import AsyncGenerator, Awaitable, Callable, TYPE_CHECKING

from ..._logging import logger
from ...event import CustomEvent, ReplyStartEvent
from ...message import Msg
from ...middleware import MiddlewareBase, TTSMiddleware
from ...tts import TTSModelBase
from ..storage import TTSModelConfig

if TYPE_CHECKING:
    from ...agent import Agent

REPLY_TTS_CONFIG = "_agentscope_tts_config"


class ChatTTSMiddleware(MiddlewareBase):
    """Use the initial turn's snapshot, then ordinary chat speech settings."""

    def __init__(
        self,
        initial_config: TTSModelConfig | None,
        default_config: TTSModelConfig | None,
        create_model: Callable[
            [TTSModelConfig, bool],
            Awaitable[tuple[TTSModelConfig, TTSModelBase | None]],
        ],
        *,
        initial_is_snapshot: bool = False,
    ) -> None:
        """Keep reply policy separate from the agent's other middleware."""
        self._next_config = (initial_config, initial_is_snapshot)
        self._default_config = default_config
        self._create_model = create_model

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Persist the effective policy on reply start and preserve cleanup."""
        config, use_snapshot = self._next_config
        self._next_config = (self._default_config, False)
        middleware = None
        if config is not None:
            try:
                config, model = await self._create_model(config, use_snapshot)
            except Exception as exc:
                logger.warning(
                    "TTS initialization failed: %s",
                    type(exc).__name__,
                )
                model = None
            if model is not None:
                middleware = TTSMiddleware(model, continue_on_error=True)
            else:
                inputs = input_kwargs.get("inputs")
                messages = inputs if isinstance(inputs, list) else [inputs]
                yield CustomEvent(
                    name="tts_error",
                    value={
                        "stage": "configuration",
                        "reply_id": getattr(inputs, "reply_id", None),
                        "message_ids": [
                            msg.id for msg in messages if isinstance(msg, Msg)
                        ],
                    },
                )
        stream = (
            middleware.on_reply(agent, input_kwargs, next_handler)
            if middleware is not None
            else next_handler(**input_kwargs)
        )
        async with aclosing(stream):
            async for event in stream:
                if isinstance(event, ReplyStartEvent):
                    event.metadata[REPLY_TTS_CONFIG] = (
                        config.model_dump(mode="json") if config else None
                    )
                yield event
