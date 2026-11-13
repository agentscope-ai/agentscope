# -*- coding: utf-8 -*-
"""MiniMax voice cloning through file upload and voice creation."""
from pathlib import PurePosixPath
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from ...credential import MiniMaxCredential
from .._voice_cloning_client_base import VoiceCloningClientBase
from .._voice_utils import VoiceCloningError, _post_voice_request

MAX_CLONE_AUDIO_SIZE_BYTES = 20 * 1024 * 1024
_AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
}


def _check_response(result: dict) -> None:
    """Require explicit provider success, including content checks."""
    status = result.get("base_resp")
    if not isinstance(status, dict) or status.get("status_code") != 0:
        code = status.get("status_code") if isinstance(status, dict) else None
        message = (
            status.get("status_msg", "unknown error")
            if isinstance(status, dict)
            else "missing base_resp"
        )
        raise VoiceCloningError(f"MiniMax API error {code}: {message}")
    sensitive = result.get("input_sensitive")
    if isinstance(sensitive, dict):
        sensitive = sensitive.get("type", 0)
    if sensitive:
        raise VoiceCloningError("MiniMax rejected the voice sample.")


class MiniMaxVoiceCloningClient(VoiceCloningClientBase):
    """Create a voice for synthesis with the same MiniMax credential.

    Samples must be MP3, M4A or WAV, no larger than 20 MiB. MiniMax checks
    the required duration of 10 seconds to 5 minutes. Voices must be used
    for synthesis within 7 days to avoid expiry. Creation does not request
    a paid synthesis preview.
    """

    class Parameters(VoiceCloningClientBase.Parameters):
        """Options for generating a unique provider voice identifier."""

        prefix: str = Field(
            default="agentscope",
            min_length=1,
            max_length=223,
            pattern=r"^[A-Za-z][A-Za-z0-9_-]*$",
        )
        """Voice ID prefix, followed by an underscore and UUID."""

    credential: MiniMaxCredential

    def __init__(self, credential: MiniMaxCredential) -> None:
        """Bind the credential used for upload and voice creation."""
        super().__init__(credential)

    async def create_voice(
        self,
        audio: bytes | None = None,
        *,
        filename: str = "voice.wav",
        audio_url: str | None = None,
        parameters: BaseModel | None = None,
    ) -> str:
        """Upload a sample, clone its voice and return the generated ID."""
        options = self.Parameters.model_validate(
            parameters.model_dump() if parameters is not None else {},
        )
        if audio is None or audio_url is not None:
            raise ValueError("MiniMax requires audio bytes without audio_url")
        if not audio:
            raise ValueError("audio must not be empty")
        if len(audio) > MAX_CLONE_AUDIO_SIZE_BYTES:
            raise ValueError("MiniMax audio must not exceed 20 MiB")
        # Strip both Windows and POSIX paths independently of the host OS.
        name = PurePosixPath(filename.replace("\\", "/")).name
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in _AUDIO_TYPES:
            raise ValueError("MiniMax requires an MP3, M4A or WAV file")
        base_url = httpx.URL(self.credential.base_url)
        root_path = base_url.path.rstrip("/").removesuffix("/anthropic")
        headers = {
            "Authorization": (
                f"Bearer {self.credential.api_key.get_secret_value()}"
            ),
        }
        uploaded = await _post_voice_request(
            str(base_url.copy_with(path=root_path + "/v1/files/upload")),
            provider="MiniMax",
            operation="audio upload",
            headers=headers,
            data={"purpose": "voice_clone"},
            files={"file": (name, audio, _AUDIO_TYPES[suffix])},
        )
        _check_response(uploaded)
        file = uploaded.get("file")
        file_id = file.get("file_id") if isinstance(file, dict) else None
        if (
            isinstance(file_id, bool)
            or not isinstance(file_id, (str, int))
            or not str(file_id).isascii()
            or not str(file_id).isdigit()
            or int(file_id) <= 0
        ):
            raise VoiceCloningError("MiniMax returned an invalid file_id.")
        voice_id = f"{options.prefix}_{uuid4().hex}"
        result = await _post_voice_request(
            str(base_url.copy_with(path=root_path + "/v1/voice_clone")),
            provider="MiniMax",
            operation="voice clone",
            headers=headers,
            json={"file_id": int(file_id), "voice_id": voice_id},
        )
        _check_response(result)
        return voice_id
