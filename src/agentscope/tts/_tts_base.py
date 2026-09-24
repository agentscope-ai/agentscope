# -*- coding: utf-8 -*-
"""The TTS model base class."""
import inspect
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator

from pydantic import BaseModel

from ._tts_response import TTSResponse
from .._logging import logger
from ..credential import CredentialBase

if TYPE_CHECKING:
    from ._tts_model_card import TTSModelCard


class TTSModelBase:
    """Base class for TTS models in AgentScope.

    This base class provides a unified abstraction for both non-realtime and
    realtime (streaming-input) TTS models, governed by the
    ``realtime`` flag.

    For non-realtime TTS models, only :meth:`synthesize` needs to be
    implemented. For realtime TTS models, the lifecycle is managed via the
    async context manager (``async with model: ...``) or by calling
    :meth:`connect` / :meth:`close` manually; :meth:`push` appends text
    chunks and returns whatever audio is currently available, while
    :meth:`synthesize` blocks until the full speech has been synthesized.
    """

    class Parameters(BaseModel):
        """Base parameter schema for TTS models. Subclasses should override
        this with provider-specific parameters."""

    credential: CredentialBase
    """The credential used to authenticate against the TTS provider."""

    model: str
    """The name of the TTS model."""

    parameters: BaseModel
    """The TTS model parameters."""

    stream: bool
    """Whether to use streaming output if supported by the model."""

    realtime: bool = False
    """Whether the TTS model supports realtime (streaming-input) mode."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel | None = None,
        stream: bool = True,
    ) -> None:
        """Initialize the TTS model base class.

        Args:
            credential (`CredentialBase`):
                The credential used to authenticate against the TTS provider.
            model (`str`):
                The name of the TTS model.
            parameters (`BaseModel | None`, defaults to `None`):
                The TTS model parameters.
            stream (`bool`, defaults to `True`):
                Whether to use streaming output if supported by the model.
        """
        self.credential = credential
        self.model = model
        self.parameters = parameters or self.Parameters()
        self.stream = stream

    async def __aenter__(self) -> "TTSModelBase":
        """Enter the async context manager and initialize resources if
        needed."""
        if self.realtime:
            await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and clean up resources if needed."""
        if self.realtime:
            await self.close()

    async def connect(self) -> None:
        """Connect to the TTS model and initialize resources.

        .. note:: Only relevant for realtime TTS models — realtime subclasses
              must override this. The default is a no-op so that non-realtime
              models can ignore the lifecycle hooks; :meth:`__aenter__` only
              calls it when ``realtime`` is True.
        """
        return

    async def close(self) -> None:
        """Close the connection to the TTS model and clean up resources.

        .. note:: Only relevant for realtime TTS models — realtime subclasses
              must override this. See :meth:`connect` for the rationale behind
              the no-op default.
        """
        return

    async def push(  # pylint: disable=unused-argument
        self,
        text: str,
        **kwargs: Any,
    ) -> TTSResponse:
        """Append text to be synthesized and return the received TTS response.
        This method is non-blocking and may return an empty response if no
        audio is available yet.

        To receive all the synthesized speech, call :meth:`synthesize` after
        pushing all the text chunks.

        .. note:: Only relevant for realtime TTS models — realtime subclasses
              must override this. Non-realtime models should call
              :meth:`synthesize` directly and never reach this method.

        Args:
            text (`str`):
                The text chunk to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse containing the audio block.
        """
        return TTSResponse(content=None)

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list["TTSModelCard"]:
        """List candidate TTS models by scanning YAML model cards.

        Args:
            custom_yaml_dir (`str | None`):
                The custom YAML directory. If ``None``, uses the ``_models``
                directory next to the concrete subclass's source file.

        Returns:
            `list[TTSModelCard]`:
                A list of TTS model cards.
        """
        from ._tts_model_card import TTSModelCard

        if custom_yaml_dir is None:
            subclass_file = Path(inspect.getfile(cls))
            yaml_dir = subclass_file.parent / "_models"
        else:
            yaml_dir = Path(custom_yaml_dir)

        yaml_files = list(yaml_dir.glob("*.yaml"))

        model_cards = []
        for yaml_file in yaml_files:
            try:
                card = TTSModelCard.from_yaml(
                    yaml_path=str(yaml_file),
                    parameter_class=cls.Parameters,
                )
                if card.realtime != cls.realtime:
                    continue
                model_cards.append(card)
            except Exception as e:
                logger.warning(
                    "Warning: Failed to load %s: %s",
                    yaml_file,
                    str(e),
                )
                continue

        return model_cards

    @abstractmethod
    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Synthesize speech from the appended text. Different from
        :meth:`push`, this method blocks until the full speech has been
        synthesized.

        Args:
            text (`str | None`, defaults to `None`):
                The text to be synthesized. If `None`, this method will
                wait for all previously pushed text to be synthesized and
                return the last synthesized TTSResponse.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single TTSResponse containing the full audio when
                ``stream=False``. When ``stream=True``, an async generator
                yielding TTSResponse chunks where each chunk carries an
                **incremental** audio delta (not a cumulative buffer); the
                full audio is the concatenation of every chunk's decoded
                bytes. The final yielded chunk has ``is_last=True``.
        """
