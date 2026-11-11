# -*- coding: utf-8 -*-
"""Common interface for provider-specific voice creation clients."""
from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from ..credential import CredentialBase


class VoiceCloningClientBase(ABC):
    """Create a voice independently of speech synthesis and App storage.

    Providers declare their creation options in ``Parameters``. The common
    method returns a provider voice ID and raises ``ValueError`` for invalid
    input or ``VoiceCloningError`` for provider failures. Consent management
    is a provider-specific capability, not a requirement of this interface.
    """

    class Parameters(BaseModel):
        """Provider-specific options for one voice creation request."""

        model_config = ConfigDict(extra="forbid")

    def __init__(self, credential: CredentialBase) -> None:
        """Bind the credential used for voice creation."""
        self.credential = credential

    @classmethod
    # pylint: disable-next=unused-argument
    def get_enrollment_model(cls, model: str) -> str | None:
        """Return the separate enrollment model, if the provider uses one."""
        return None

    @abstractmethod
    async def create_voice(
        self,
        audio: bytes | None = None,
        *,
        filename: str = "voice.wav",
        audio_url: str | None = None,
        parameters: BaseModel | None = None,
    ) -> str:
        """Create a voice from one audio source and return its provider ID.

        Args:
            audio: Encoded audio bytes, if uploads are supported.
            filename: Filename used to determine the uploaded audio format.
            audio_url: Public audio URL, if supported. Mutually exclusive
                with ``audio``; implementations validate supported inputs.
            parameters: An instance of the client's ``Parameters`` model.
                When omitted, provider parameter defaults are validated.

        Returns:
            The provider's voice ID for use with a compatible TTS model.
        """
