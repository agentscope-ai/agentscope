# -*- coding: utf-8 -*-
"""The TTS model base class."""

from abc import ABC, abstractmethod
from typing import Any

from agentscope.message import Msg

from ._tts_response import TTSResponse


class TTSModelBase(ABC):
    """Base class for TTS models."""

    # Class attribute to indicate if this TTS model supports streaming input
    supports_streaming_input: bool = False

    def __init__(
        self,
        model_name: str,
        stream: bool = True,
    ) -> None:
        """Initialize the TTS model base class.

        Args:
            model_name (`str`):
                The name of the TTS model
            stream (`bool`):
                Whether to send text in streaming mode (send incrementally
                as text arrives)
                or batch mode (wait for complete text before sending).
                Defaults to True (streaming mode).
        """
        self.model_name = model_name
        self.stream = stream

    async def __aenter__(self) -> "TTSModelBase":
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        await self.close()

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the TTS model and prepare it for use.

        This method should establish any necessary connections,
        configure the service, and prepare the model for synthesis.
        """

    async def __call__(
        self,
        msg: Msg,
        last: bool = False,
        **kwargs: Any,
    ) -> TTSResponse:
        """Call the TTS model to synthesize text from a message.

        This is a convenience method that delegates to `_call_api`.

        Args:
            msg (`Msg`):
                The message to be synthesized.
            last (`bool`):
                Whether this is the last chunk of text. Defaults to False.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse containing audio blocks.
        """
        return await self._call_api(msg, last=last, **kwargs)

    @abstractmethod
    async def _call_api(
        self,
        msg: Msg,
        last: bool = False,
        **kwargs: Any,
    ) -> TTSResponse:
        """Append text to be synthesized and return TTS response.

        Args:
            msg (`Msg`): The msg to be synthesized
            last (`bool`): Whether this is the last chunk of text.
             Defaults to False.
            **kwargs (`Any`): Additional keyword arguments to pass to the
             TTS API call.

        Returns:
            `TTSResponse`: The TTSResponse containing audio blocks.

        Note:
            - If `stream=True` (default): Text is sent incrementally as it
            arrives. Each call to `append_text()` immediately sends the text to
              TTS service.
            - If `stream=False`: Text is buffered until `last=True` is passed,
              then all buffered text is sent at once.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the TTS model and clean up resources."""
