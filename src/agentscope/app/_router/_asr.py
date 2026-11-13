# -*- coding: utf-8 -*-
"""Speech recognition endpoints for browser-recorded audio."""

import json
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import status
from pydantic import ValidationError

from ..deps import (
    get_current_user_id,
    get_download_secret,
    get_resource_access_service,
)
from .._service import (
    ResourceAccessService,
    get_asr_model,
    sign_download_token,
    verify_download_token,
)
from ._schema import (
    ListASRModelsRequest,
    ListASRModelsResponse,
    RealtimeASRStartRequest,
    RealtimeASRTokenRequest,
    RealtimeASRTokenResponse,
    TranscriptionResponse,
)
from ...credential import CredentialFactory


asr_router = APIRouter(
    tags=["asr"],
    responses={404: {"description": "Not found"}},
)

_MAX_AUDIO_UPLOAD_BYTES = 25 * 1024 * 1024
_REALTIME_TOKEN_TTL_SECONDS = 60


def _realtime_token_scope(credential_id: str, model: str) -> str:
    """Return the capability scope for one realtime ASR configuration."""
    encoded_credential = quote(credential_id, safe="")
    encoded_model = quote(model, safe="")
    return f"/asr/realtime/{encoded_credential}/{encoded_model}"


@asr_router.get(
    "/asr-model/",
    response_model=ListASRModelsResponse,
    summary="List ASR models under a credential provider",
)
async def list_asr_models(
    body: ListASRModelsRequest = Depends(),
) -> ListASRModelsResponse:
    """Return batch and realtime ASR cards for a credential provider.

    Args:
        body (`ListASRModelsRequest`):
            The provider whose ASR models should be listed.

    Returns:
        `ListASRModelsResponse`:
            The available ASR model cards and their total count.

    Raises:
        `HTTPException`:
            If the requested credential provider is not registered.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {body.provider!r} not found.",
        )

    models = credential_cls.list_asr_models()
    return ListASRModelsResponse(models=models, total=len(models))


@asr_router.post(
    "/asr/transcriptions",
    response_model=TranscriptionResponse,
    summary="Transcribe an uploaded audio recording",
)
async def transcribe_audio(
    file: UploadFile = File(),
    credential_id: str = Form(),
    model: str = Form(),
    parameters: str = Form(default="{}"),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> TranscriptionResponse:
    """Transcribe one complete browser recording with a batch ASR model.

    Args:
        file (`UploadFile`):
            The uploaded audio recording. Files larger than 25 MiB are
            rejected.
        credential_id (`str`):
            The stored credential used to call the ASR provider.
        model (`str`):
            The batch ASR model name.
        parameters (`str`):
            Provider-specific parameters encoded as a JSON object.
        user_id (`str`):
            The authenticated viewer's user ID.
        access (`ResourceAccessService`):
            The service used to authorize and resolve the credential.

    Returns:
        `TranscriptionResponse`:
            The recognized text.

    Raises:
        `HTTPException`:
            If the upload is empty or too large, the parameters are invalid,
            or the credential and model cannot be used by the viewer.
    """
    if file.size is not None and file.size > _MAX_AUDIO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Audio file exceeds the 25 MiB upload limit.",
        )
    audio = await file.read(_MAX_AUDIO_UPLOAD_BYTES + 1)
    if len(audio) > _MAX_AUDIO_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Audio file exceeds the 25 MiB upload limit.",
        )
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file must not be empty.",
        )
    try:
        parsed_parameters = json.loads(parameters)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ASR parameters must be valid JSON.",
        ) from error
    if not isinstance(parsed_parameters, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ASR parameters must be a JSON object.",
        )

    try:
        asr_model = await get_asr_model(
            user_id=user_id,
            credential_id=credential_id,
            model=model,
            access=access,
            parameters=parsed_parameters,
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    result = await asr_model.transcribe(
        audio=audio,
        filename=file.filename or "audio.webm",
    )
    return TranscriptionResponse(
        text=result.content.text if result.content is not None else "",
    )


@asr_router.post(
    "/asr/realtime/token",
    response_model=RealtimeASRTokenResponse,
    summary="Mint a short-lived realtime ASR WebSocket token",
)
async def create_realtime_asr_token(
    body: RealtimeASRTokenRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    download_secret: str = Depends(get_download_secret),
) -> RealtimeASRTokenResponse:
    """Authorize one credential and model before opening a WebSocket.

    The returned token is scoped to the requested credential and model and
    expires after a short interval.

    Args:
        body (`RealtimeASRTokenRequest`):
            The credential and realtime ASR model to authorize.
        user_id (`str`):
            The authenticated viewer's user ID.
        access (`ResourceAccessService`):
            The service used to authorize and resolve the credential.
        download_secret (`str`):
            The server secret used to sign the capability token.

    Returns:
        `RealtimeASRTokenResponse`:
            The signed token and its expiration timestamp.

    Raises:
        `HTTPException`:
            If the credential is unavailable to the viewer or does not
            support the requested realtime model.
    """
    await get_asr_model(
        user_id=user_id,
        credential_id=body.credential_id,
        model=body.model,
        access=access,
        realtime=True,
    )
    token, expires_at = sign_download_token(
        download_secret,
        user_id,
        _realtime_token_scope(body.credential_id, body.model),
        ttl=_REALTIME_TOKEN_TTL_SECONDS,
    )
    return RealtimeASRTokenResponse(
        token=token,
        expires_at=expires_at,
    )


@asr_router.websocket("/asr/realtime")
async def transcribe_realtime(websocket: WebSocket) -> None:
    """Proxy browser PCM audio to a server-side realtime ASR model.

    The connection requires a signed ``token`` query parameter. The first
    client frame must contain :class:`RealtimeASRStartRequest` JSON, followed
    by binary PCM16 audio frames and a final ``{"type": "stop"}`` control
    frame. The server emits ``ready``, ``transcript``, or ``error`` JSON
    frames and closes the connection after the final transcript.

    Args:
        websocket (`WebSocket`):
            The browser WebSocket connection carrying control and audio
            frames.
    """
    await websocket.accept()
    try:
        start = RealtimeASRStartRequest.model_validate(
            await websocket.receive_json(),
        )
        token = websocket.query_params.get("token")
        if token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Realtime ASR token is required.",
            )
        try:
            user_id = verify_download_token(
                websocket.app.state.download_secret,
                token,
                _realtime_token_scope(start.credential_id, start.model),
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired realtime ASR token.",
            ) from error
        access = websocket.app.state.resource_access_service
        parameters = {**start.parameters, "sample_rate": start.sample_rate}
        asr_model = await get_asr_model(
            user_id=user_id,
            credential_id=start.credential_id,
            model=start.model,
            access=access,
            parameters=parameters,
            realtime=True,
        )
        async with asr_model:
            await websocket.send_json({"type": "ready"})
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                audio = message.get("bytes")
                if audio is not None:
                    response = await asr_model.push(audio)
                    await _send_transcript(websocket, response)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                control = json.loads(text)
                if control.get("type") != "stop":
                    raise ValueError("Unsupported realtime ASR control frame.")
                response = await asr_model.transcribe()
                await _send_transcript(websocket, response)
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return
    except WebSocketDisconnect:
        return
    except ValidationError as error:
        await _send_realtime_error(websocket, str(error))
    except HTTPException as error:
        close_code = (
            status.WS_1008_POLICY_VIOLATION
            if error.status_code
            in {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            }
            else status.WS_1011_INTERNAL_ERROR
        )
        await _send_realtime_error(
            websocket,
            str(error.detail),
            close_code=close_code,
        )
    except (json.JSONDecodeError, ValueError) as error:
        await _send_realtime_error(websocket, str(error))
    except Exception as error:  # pylint: disable=broad-exception-caught
        await _send_realtime_error(websocket, str(error))


async def _send_transcript(websocket: WebSocket, response: object) -> None:
    """Send one non-empty ASR response to the browser."""
    content = getattr(response, "content", None)
    is_last = bool(getattr(response, "is_last", False))
    text = content.text if content is not None else ""
    if text or is_last:
        await websocket.send_json(
            {
                "type": "transcript",
                "text": text,
                "is_last": is_last,
            },
        )


async def _send_realtime_error(
    websocket: WebSocket,
    detail: str,
    close_code: int = status.WS_1011_INTERNAL_ERROR,
) -> None:
    """Report a realtime failure and close the browser connection."""
    try:
        await websocket.send_json({"type": "error", "detail": detail})
        await websocket.close(code=close_code)
    except RuntimeError:
        return
