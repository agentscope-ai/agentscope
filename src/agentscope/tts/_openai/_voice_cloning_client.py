# -*- coding: utf-8 -*-
"""OpenAI voice creation and consent recording uploads."""
import os

from pydantic import BaseModel, Field

from ...credential import OpenAICredential
from .._voice_cloning_client_base import VoiceCloningClientBase
from .._voice_utils import (
    VoiceCloningError,
    _audio_file,
    _post_voice_request,
)


class OpenAIVoiceCloningClient(VoiceCloningClientBase):
    """Create OpenAI custom voices independently of a synthesis model.

    Reuse the same credential for creation and synthesis. Consent recordings
    must contain the provider's exact declaration in the supplied language.
    Methods raise ``ValueError`` for invalid inputs and ``VoiceCloningError``
    for failed provider requests. No App service or storage is required.
    """

    class Parameters(VoiceCloningClientBase.Parameters):
        """OpenAI voice creation options."""

        name: str = Field(default="agentscope", min_length=1)
        """Label for the created voice."""

        consent: str = Field(min_length=1)
        """ID of the speaker's consent recording."""

    credential: OpenAICredential

    def __init__(self, credential: OpenAICredential) -> None:
        """Bind the credential used for voice and consent creation."""
        super().__init__(credential)

    def _request_config(self) -> tuple[str, dict[str, str]]:
        """Match OpenAI endpoint, organization and project defaults."""
        base_url = (
            self.credential.base_url
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        headers = {
            "Authorization": (
                f"Bearer {self.credential.api_key.get_secret_value()}"
            ),
        }
        organization = self.credential.organization or os.getenv(
            "OPENAI_ORG_ID",
        )
        if organization:
            headers["OpenAI-Organization"] = organization
        project = os.getenv("OPENAI_PROJECT_ID")
        if project:
            headers["OpenAI-Project"] = project
        return base_url.rstrip("/"), headers

    async def create_consent(
        self,
        audio: bytes,
        *,
        name: str,
        language: str,
        filename: str = "consent.wav",
    ) -> str:
        """Upload a consent recording and return its provider ID.

        Args:
            audio: Encoded recording bytes, not base64 or raw PCM.
            name: Label for the consent recording.
            language: BCP 47 language tag matching the recorded declaration.
            filename: Recording filename used to determine its MIME type.

        Returns:
            The consent ID required by :meth:`create_voice`.
        """
        if not name or not language:
            raise ValueError("name and language must not be empty")
        recording = _audio_file(filename, audio)
        base_url, headers = self._request_config()
        result = await _post_voice_request(
            f"{base_url}/audio/voice_consents",
            provider="OpenAI",
            operation="consent upload",
            success_codes=(200, 201),
            headers=headers,
            data={"name": name, "language": language},
            files={"recording": recording},
        )
        consent_id = result.get("id")
        if not isinstance(consent_id, str) or not consent_id:
            raise VoiceCloningError(
                "OpenAI returned success but no consent "
                f"id in response: {result}",
            )
        return consent_id

    async def create_voice(
        self,
        audio: bytes | None = None,
        *,
        filename: str = "voice.wav",
        audio_url: str | None = None,
        parameters: BaseModel | None = None,
    ) -> str:
        """Create a custom voice and return its provider ID.

        Args:
            audio: Encoded sample bytes from the consenting speaker.
            filename: Sample filename used to determine its MIME type.
            audio_url: Unsupported by OpenAI; must be ``None``.
            parameters: ``Parameters`` containing the name and consent ID
                returned by :meth:`create_consent`.

        Returns:
            The voice ID. Pass ``{"id": voice_id}`` as the ``voice`` parameter
            of a compatible ``OpenAITTSModel`` when synthesizing speech.
        """
        options = self.Parameters.model_validate(
            parameters.model_dump() if parameters is not None else {},
        )
        if audio_url is not None or audio is None:
            raise ValueError("OpenAI requires audio bytes, not audio_url")
        sample = _audio_file(filename, audio)
        base_url, headers = self._request_config()
        result = await _post_voice_request(
            f"{base_url}/audio/voices",
            provider="OpenAI",
            operation="voice clone",
            success_codes=(200, 201),
            headers=headers,
            data={"name": options.name, "consent": options.consent},
            files={"audio_sample": sample},
        )
        voice_id = result.get("id")
        if not isinstance(voice_id, str) or not voice_id:
            raise VoiceCloningError(
                "OpenAI returned success but no voice "
                f"id in response: {result}",
            )
        return voice_id
