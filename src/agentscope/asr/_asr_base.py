# -*- coding: utf-8 -*-
"""Base interface for speech recognition models."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
import inspect
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel

from .._logging import logger
from ..credential import CredentialBase
from ._asr_response import ASRResponse

if TYPE_CHECKING:
    from ._asr_model_card import ASRModelCard


class ASRModelBase(ABC):
    """Base class for batch and realtime speech recognition models.

    Batch implementations transcribe complete audio with :meth:`transcribe`.
    Realtime implementations send audio and receive transcription events
    concurrently through :meth:`transcribe_stream`.
    """

    class Parameters(BaseModel):
        """Provider-specific transcription parameters."""

    credential: CredentialBase
    """The credential used to authenticate against the ASR provider."""

    model: str
    """The name of the ASR model."""

    parameters: BaseModel
    """The provider-specific ASR model parameters."""

    realtime: bool = False
    """Whether the model supports realtime streaming input."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel | None = None,
    ) -> None:
        self.credential = credential
        self.model = model
        self.parameters = parameters or self.Parameters()

    async def __aenter__(self) -> "ASRModelBase":
        """Enter the realtime model lifecycle when required."""
        if self.realtime:
            await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        """Exit the realtime model lifecycle when required."""
        if self.realtime:
            await self.close()

    async def connect(self) -> None:
        """Connect to a realtime ASR provider.

        Realtime subclasses must override this method. The default is a no-op
        so batch implementations do not need lifecycle hooks.
        """
        return

    async def close(self) -> None:
        """Close a realtime ASR connection.

        Realtime subclasses must override this method. The default is a no-op
        so batch implementations do not need lifecycle hooks.
        """
        return

    async def push(  # pylint: disable=unused-argument
        self,
        audio: bytes,
        **kwargs: Any,
    ) -> None:
        """Send an audio chunk to a realtime ASR provider.

        Realtime subclasses must override this method. Transcription results
        are produced independently by :meth:`receive`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support realtime audio input",
        )

    async def finish(self) -> None:
        """Signal that no more realtime audio chunks will be sent.

        Realtime subclasses must override this method and let
        :meth:`receive` finish after yielding all remaining responses.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support realtime audio input",
        )

    def receive(self) -> AsyncIterator[ASRResponse]:
        """Subscribe to realtime incremental transcription results.

        Realtime subclasses must override this method with an asynchronous
        iterator that ends after :meth:`finish` has been fully processed.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support realtime audio input",
        )

    async def transcribe_stream(
        self,
        audio: AsyncIterable[bytes],
        **kwargs: Any,
    ) -> AsyncGenerator[ASRResponse, None]:
        """Transcribe an asynchronous stream of audio chunks in realtime.

        Audio sending and transcription receiving run concurrently, allowing
        callers to display partial results while input is still arriving.

        Args:
            audio (`AsyncIterable[bytes]`):
                The asynchronous source of audio chunks.
            **kwargs (`Any`):
                Additional keyword arguments passed to :meth:`push`.

        Yields:
            `ASRResponse`:
                Incremental text deltas from the provider. The last response
                has ``is_last=True``.
        """
        if not self.realtime:
            raise RuntimeError(
                f"{type(self).__name__} does not support realtime "
                f"transcription",
            )

        async def send_audio() -> None:
            """Forward input chunks while responses are received."""
            async for chunk in audio:
                await self.push(chunk, **kwargs)
            await self.finish()

        async with self:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(send_audio())
                async for response in self.receive():
                    yield response

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list["ASRModelCard"]:
        """List model cards associated with a concrete implementation."""
        from ._asr_model_card import ASRModelCard

        yaml_dir = (
            Path(custom_yaml_dir)
            if custom_yaml_dir is not None
            else Path(inspect.getfile(cls)).parent / "_models"
        )
        model_cards = []
        for path in sorted(yaml_dir.glob("*.yaml")):
            try:
                card = ASRModelCard.from_yaml(
                    str(path),
                    cls.Parameters,
                )
                if card.realtime != cls.realtime:
                    continue
                model_cards.append(card)
            # Keep one invalid card from hiding other valid cards.
            except Exception as error:
                logger.warning(
                    "Failed to load ASR model card %s: %s",
                    path,
                    error,
                )
        return model_cards

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Transcribe complete audio.

        ``filename`` supplies the format extension required by batch
        multipart APIs when ``audio`` comes from memory.
        """
