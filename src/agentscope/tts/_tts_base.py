# -*- coding: utf-8 -*-
"""The TTS model base class."""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

from agentscope.message import Msg

from ._tts_response import TTSResponse


class TTSModelBase(ABC):
    """Base class for TTS models in AgentScope.

    This base class provides general abstraction for both realtime and
    non-realtime TTS models (depending on whether streaming input is
    supported).

    For non-realtime TTS models, the `synthesize` method is used to
    synthesize speech from the input text. You only need to implement the
    `_call_api` method to handle the TTS API calls.

    For realtime TTS models, its lifecycle is managed via the async context
    manager or calling `connect` and `close` methods. The `push` method will
    append text chunks and return the received TTS response, while the
    `synthesize` method will block until the full speech is synthesized.
    You need to implement the `connect`, `close`, and `_call_api` methods
    to handle the TTS API calls and resource management.
    """

    supports_streaming_input: bool = False
    """If the TTS model class supports streaming input."""

    model_name: str
    """The name of the TTS model."""

    stream: bool
    """Whether to use streaming synthesis if supported by the model."""

    def __init__(self, model_name: str, stream: bool) -> None:
        """Initialize the TTS model base class.

        Args:
            model_name (`str`):
                The name of the TTS model
            stream  (`bool`):
                Whether to use streaming synthesis if supported by the model.
        """
        self.model_name = model_name
        self.stream = stream

    async def __aenter__(self) -> "TTSModelBase":
        """Enter the async context manager and initialize resources if
        needed."""
        if self.supports_streaming_input:
            await self.connect()

        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and clean up resources if needed."""
        if self.supports_streaming_input:
            await self.close()

    async def connect(self) -> None:
        """Connect to the TTS model and initialize resources. For non-realtime
        TTS models, leave this method empty.

        .. note:: Only needs to be implemented for realtime TTS models.

        """
        raise NotImplementedError(
            f"The connect method is not implemented for "
            f"{self.__class__.__name__} class.",
        )

    async def close(self) -> None:
        """Close the connection to the TTS model and clean up resources. For
        non-realtime TTS models, leave this method empty.

        .. note:: Only needs to be implemented for realtime TTS models.

        """
        raise NotImplementedError(
            "The close method is not implemented for "
            f"{self.__class__.__name__} class.",
        )

    async def push(
        self,
        msg: Msg,
        **kwargs: Any,
    ) -> TTSResponse:
        """Append text to be synthesized and return the received TTS response.
        Note this method is non-blocking, and maybe return an empty response
        if no audio is received yet.

        To receive all the synthesized speech, call the `synthesize` method
        after pushing all the text chunks.

        .. note:: Only needs to be implemented for realtime TTS models.

        Args:
            msg (`Msg`):
                The message to be synthesized. The `msg.id` identifies the
                streaming input request.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse containing audio block.
        """
        raise NotImplementedError(
            "The push method is not implemented for "
            f"{self.__class__.__name__} class.",
        )

    @abstractmethod
    async def synthesize(
        self,
        msg: Msg | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Synthesize speech from the appended text. Different from the `push`
        method, this method will block until the full speech is synthesized.

        Args:
            msg (`Msg | None`, defaults to `None`):
                The message to be synthesized. If `None`, this method will
                wait for all previously pushed text to be synthesized, and
                return the last synthesized TTSResponse.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                The TTSResponse containing audio blocks, or an async generator
                yielding TTSResponse objects in streaming mode.
        """
