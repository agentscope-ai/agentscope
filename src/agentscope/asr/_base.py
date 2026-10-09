# -*- coding: utf-8 -*-
"""Base interface for batch speech recognition models."""

from abc import ABC, abstractmethod
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .._logging import logger
from ..credential import CredentialBase
from ._response import ASRResponse

if TYPE_CHECKING:
    from ._model_card import ASRModelCard


class ASRModelBase(ABC):
    """Transcribe a complete audio file into text.

    Streaming-input recognition requires a separate lifecycle and is outside
    this batch-only contract.
    """

    class Parameters(BaseModel):
        """Provider-specific transcription parameters."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel | None = None,
    ) -> None:
        self.credential = credential
        self.model = model
        self.parameters = parameters or self.Parameters()

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list["ASRModelCard"]:
        """List model cards associated with a concrete implementation."""
        from ._model_card import ASRModelCard

        yaml_dir = (
            Path(custom_yaml_dir)
            if custom_yaml_dir is not None
            else Path(inspect.getfile(cls)).parent / "_models"
        )
        model_cards = []
        for path in sorted(yaml_dir.glob("*.yaml")):
            try:
                model_cards.append(
                    ASRModelCard.from_yaml(str(path), cls.Parameters),
                )
            # Keep one invalid card from hiding other valid cards.
            # pylint: disable-next=broad-exception-caught
            except Exception as error:
                # pylint: disable-next=logging-fstring-interpolation
                logger.warning(
                    f"Failed to load ASR model card {path}: {error}",
                )
        return model_cards

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        filename: str = "audio.wav",
    ) -> ASRResponse:
        """Transcribe a complete audio file.

        ``filename`` supplies the format extension required by multipart
        transcription APIs when ``audio`` comes from memory.
        """
