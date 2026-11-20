# -*- coding: utf-8 -*-
"""Base interface for speech recognition models."""

from abc import ABC, abstractmethod
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

    Batch implementations transcribe complete audio by calling
    :meth:`transcribe` directly. Realtime implementations use the asynchronous
    context manager, call :meth:`push` for each audio chunk, and finally call
    :meth:`transcribe` without audio to finalize the current transcription.

    Every concrete implementation must override :meth:`transcribe`. Realtime
    implementations must additionally override :meth:`connect`,
    :meth:`close`, and :meth:`push`.
    """

    class Parameters(BaseModel):
        """Base schema for provider-specific transcription parameters.

        Concrete ASR implementations should override this class with the
        parameters supported by their provider.
        """

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
        """Initialize the ASR model.

        Args:
            credential (`CredentialBase`):
                The credential used to authenticate with the provider.
            model (`str`):
                The provider model name.
            parameters (`BaseModel | None`, defaults to `None`):
                Provider-specific parameters. When omitted, an instance of
                :attr:`Parameters` is created.
        """
        self.credential = credential
        self.model = model
        self.parameters = parameters or self.Parameters()

    async def __aenter__(self) -> "ASRModelBase":
        """Enter the model's asynchronous context.

        Realtime models establish their provider connection by calling
        :meth:`connect`. Entering a batch model is a no-op.

        Returns:
            `ASRModelBase`:
                This model instance.
        """
        if self.realtime:
            await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        """Exit the model's asynchronous context.

        Realtime models close their provider connection by calling
        :meth:`close`, including when the context exits with an exception.
        Exiting a batch model is a no-op.

        Args:
            exc_type (`Any`):
                The type of the exception that caused the context to exit, or
                ``None`` for a normal exit.
            exc_value (`Any`):
                The exception instance, or ``None`` for a normal exit.
            traceback (`Any`):
                The exception traceback, or ``None`` for a normal exit.
        """
        if self.realtime:
            await self.close()

    async def connect(self) -> None:
        """Establish the realtime provider connection.

        Realtime implementations must override this method and prepare any
        provider resources needed by :meth:`push` and :meth:`transcribe`. The
        default is a no-op so batch implementations do not need lifecycle
        hooks.
        """
        return

    async def close(self) -> None:
        """Close the realtime provider connection.

        Realtime implementations must override this method and release any
        resources created by :meth:`connect`. The default is a no-op so batch
        implementations do not need lifecycle hooks.
        """
        return

    async def push(  # pylint: disable=unused-argument
        self,
        audio: bytes,
        **kwargs: Any,
    ) -> ASRResponse:
        """Send an audio chunk and return currently available text.

        Realtime implementations must override this method. It should send the
        audio without waiting for the complete transcription and return any
        text already available from the provider.

        Args:
            audio (`bytes`):
                An incremental audio chunk in the format expected by the
                provider.
            **kwargs (`Any`):
                Provider-specific options for this audio chunk.

        Returns:
            `ASRResponse`:
                A non-final incremental response. Its content is ``None`` when
                no new text is available yet.

        Raises:
            RuntimeError:
                If this model does not support realtime audio input.
        """
        raise RuntimeError(
            f"{type(self).__name__} does not support realtime audio input",
        )

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list["ASRModelCard"]:
        """List model cards associated with the concrete implementation.

        Cards whose ``realtime`` value differs from the implementation are
        excluded. Invalid cards are logged and skipped so they do not prevent
        other cards from being discovered.

        Args:
            custom_yaml_dir (`str | None`, defaults to `None`):
                Directory containing model-card YAML files. When omitted, the
                ``_models`` directory beside the concrete implementation is
                used.

        Returns:
            `list[ASRModelCard]`:
                Valid model cards compatible with the implementation's
                realtime mode.
        """
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
        audio: bytes | None = None,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Transcribe complete audio or finalize realtime input.

        Batch subclasses require complete ``audio``. Realtime subclasses use
        ``audio=None`` to finalize previously pushed chunks and may accept a
        final audio chunk before finalizing. The final response has
        ``is_last=True``.

        ``filename`` supplies the format extension required by batch
        multipart APIs when ``audio`` comes from memory.

        Args:
            audio (`bytes | None`, defaults to `None`):
                Complete in-memory audio for a batch model. For a realtime
                model, ``None`` finalizes previously pushed audio; a byte
                string may provide one last chunk before finalization.
            filename (`str`, defaults to `"audio.wav"`):
                Filename metadata used by multipart batch APIs to identify the
                in-memory audio format.
            **kwargs (`Any`):
                Provider-specific transcription options.

        Returns:
            `ASRResponse`:
                The completed transcription response with ``is_last=True``.
        """
