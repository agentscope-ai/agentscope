# -*- coding: utf-8 -*-
"""Base interface for batch speech recognition models."""

from abc import ABC, abstractmethod
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..credential import CredentialBase
from ._response import ASRResponse

if TYPE_CHECKING:
    from ._model_card import ASRModelCard


class ASRModelBase(ABC):
    """Transcribe a complete audio file into text.

    Streaming-input recognition has a different lifecycle and can extend this
    interface without imposing connection or buffering semantics on batch APIs.
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
        """List the model cards beside a concrete implementation."""
        from ._model_card import ASRModelCard

        yaml_dir = (
            Path(custom_yaml_dir)
            if custom_yaml_dir is not None
            else Path(inspect.getfile(cls)).parent / "_models"
        )
        return [
            ASRModelCard.from_yaml(str(path), cls.Parameters)
            for path in sorted(yaml_dir.glob("*.yaml"))
        ]

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
