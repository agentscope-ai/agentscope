# -*- coding: utf-8 -*-
"""Voice profile router — CRUD endpoints."""
import base64
import mimetypes

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_storage,
)
from .._service import ResourceAccessService
from ..storage import StorageBase, VoiceProfileRecord
from ..storage._model import (
    ENGINE_GPU_REQUIREMENT,
    ENGINE_SOURCE,
    ENGINE_TO_CREDENTIAL_TYPE,
    ENGINE_VOICE_CLONING,
)
from ...credential import CredentialFactory
from ._schema import (
    AvailableEnginesResponse,
    CloneVoiceRequest,
    CloneVoiceResponse,
    CreateVoiceProfileRequest,
    CreateVoiceProfileResponse,
    CredentialRef,
    EngineInfo,
    ListVoiceProfilesResponse,
    OpenAIConsentRequest,
    OpenAIConsentResponse,
    UpdateVoiceProfileRequest,
)

voice_profile_router = APIRouter(
    prefix="/voice-profile",
    tags=["voice_profile"],
    responses={404: {"description": "Not found"}},
)


async def _get_available_engines(
    access: ResourceAccessService,
    user_id: str,
) -> list[str]:
    """Return engine names whose credential is configured.

    Args:
        access: Resource access service.
        user_id: Authenticated user id.

    Returns:
        Sorted list of available engine names.
    """
    credentials = await access.list_resource(
        user_id,
        ResourceKind.CREDENTIAL,
    )
    cred_types: set[str] = set()
    for cred in credentials:
        cred_type = cred.data.get("type")
        if cred_type:
            cred_types.add(cred_type)

    engines: list[str] = []
    for engine, required_type in ENGINE_TO_CREDENTIAL_TYPE.items():
        if required_type in cred_types:
            engines.append(engine)
    return sorted(engines)


async def _ensure_engine_credential(
    access: ResourceAccessService,
    user_id: str,
    engine: str | None,
) -> None:
    """Raise 400 if engine requires a missing credential.

    Args:
        access: Resource access service.
        user_id: Authenticated user id.
        engine: Engine name from voice profile data.
    """
    if engine is None:
        return
    available = await _get_available_engines(access, user_id)
    if engine not in available:
        required = ENGINE_TO_CREDENTIAL_TYPE.get(engine, engine)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Engine '{engine}' requires a "
                f"'{required}' credential. "
                f"Please configure one first."
            ),
        )


@voice_profile_router.get(
    "/available-engines",
    response_model=AvailableEnginesResponse,
    summary="List available TTS engines",
)
async def list_available_engines(
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(
        get_resource_access_service,
    ),
) -> AvailableEnginesResponse:
    """Return TTS engines for which the user has a credential.

    Args:
        user_id (`str`): Injected authenticated user ID.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `AvailableEnginesResponse`: Available engine names.
    """
    credentials = await access.list_resource(
        user_id,
        ResourceKind.CREDENTIAL,
    )

    # Map cred_type -> list of (id, label)
    cred_by_type: dict[str, list[CredentialRef]] = {}
    for cred in credentials:
        cred_type = cred.data.get("type")
        if cred_type:
            label = cred.data.get("name") or cred_type
            cred_by_type.setdefault(cred_type, []).append(
                CredentialRef(id=cred.id, label=label),
            )

    engines: list[str] = []
    for engine, required_type in ENGINE_TO_CREDENTIAL_TYPE.items():
        if required_type in cred_by_type:
            engines.append(engine)
    engines.sort()

    details = [
        EngineInfo(
            name=eng,
            source=ENGINE_SOURCE.get(eng, "local"),
            gpu_requirement=ENGINE_GPU_REQUIREMENT.get(eng),
            voice_cloning=ENGINE_VOICE_CLONING.get(eng, False),
            credentials=cred_by_type.get(
                ENGINE_TO_CREDENTIAL_TYPE[eng],
                [],
            ),
        )
        for eng in engines
    ]
    return AvailableEnginesResponse(
        engines=engines,
        engine_details=details,
    )


@voice_profile_router.get(
    "/",
    response_model=ListVoiceProfilesResponse,
    summary="List all voice profiles",
)
async def list_voice_profiles(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListVoiceProfilesResponse:
    """Return all voice profiles for the authenticated user.

    Args:
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `ListVoiceProfilesResponse`: All voice profiles.
    """
    profiles = await storage.list_voice_profiles(user_id)
    return ListVoiceProfilesResponse(
        profiles=profiles,
        total=len(profiles),
    )


@voice_profile_router.get(
    "/{profile_id}",
    response_model=VoiceProfileRecord,
    summary="Get a voice profile",
)
async def get_voice_profile(
    profile_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> VoiceProfileRecord:
    """Fetch a single voice profile by id.

    Args:
        profile_id (`str`): The voice profile id.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `VoiceProfileRecord`: The voice profile record.
    """
    record = await storage.get_voice_profile(user_id, profile_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile {profile_id!r} not found.",
        )
    return record


@voice_profile_router.post(
    "/",
    response_model=CreateVoiceProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new voice profile",
)
async def create_voice_profile(
    body: CreateVoiceProfileRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(
        get_resource_access_service,
    ),
) -> CreateVoiceProfileResponse:
    """Create a new voice profile.

    Args:
        body (`CreateVoiceProfileRequest`): The profile data.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `CreateVoiceProfileResponse`: The created profile id.
    """
    if not body.data.engine:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="engine is required.",
        )
    await _ensure_engine_credential(
        access,
        user_id,
        body.data.engine,
    )
    # Check for duplicate name
    existing_profiles = await storage.list_voice_profiles(user_id)
    for p in existing_profiles:
        if p.data.name == body.data.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Voice profile named '{body.data.name}' "
                    f"already exists."
                ),
            )
    record = VoiceProfileRecord(
        user_id=user_id,
        data=body.data,
    )
    profile_id = await storage.upsert_voice_profile(
        user_id,
        record,
    )
    return CreateVoiceProfileResponse(profile_id=profile_id)


@voice_profile_router.patch(
    "/{profile_id}",
    response_model=VoiceProfileRecord,
    summary="Update a voice profile",
)
async def update_voice_profile(
    profile_id: str,
    body: UpdateVoiceProfileRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    access: ResourceAccessService = Depends(
        get_resource_access_service,
    ),
) -> VoiceProfileRecord:
    """Update an existing voice profile.

    Args:
        profile_id (`str`): The voice profile id.
        body (`UpdateVoiceProfileRequest`): New profile data.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
        access (`ResourceAccessService`): Injected access service.

    Returns:
        `VoiceProfileRecord`: The updated voice profile.
    """
    if not body.data.engine:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="engine is required.",
        )
    await _ensure_engine_credential(
        access,
        user_id,
        body.data.engine,
    )
    existing = await storage.get_voice_profile(
        user_id,
        profile_id,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile {profile_id!r} not found.",
        )
    # Check for duplicate name (exclude self)
    all_profiles = await storage.list_voice_profiles(user_id)
    for p in all_profiles:
        if p.id != profile_id and p.data.name == body.data.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Voice profile named '{body.data.name}' "
                    f"already exists."
                ),
            )
    existing.data = body.data
    await storage.upsert_voice_profile(user_id, existing)
    updated = await storage.get_voice_profile(
        user_id,
        profile_id,
    )
    if updated is None:
        raise RuntimeError(
            f"Voice profile {profile_id!r} disappeared " "after upsert.",
        )
    return updated


@voice_profile_router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a voice profile",
)
async def delete_voice_profile(
    profile_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Delete a voice profile.

    Args:
        profile_id (`str`): The voice profile id.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.
    """
    deleted = await storage.delete_voice_profile(
        user_id,
        profile_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile {profile_id!r} not found.",
        )


# ------------------------------------------------------------------
# Voice Cloning
# ------------------------------------------------------------------

_CLONE_URL = (
    "https://dashscope.aliyuncs.com/api"
    + "/v1/services/audio/tts/customization"
)


@voice_profile_router.post(
    "/openai-consent",
    response_model=OpenAIConsentResponse,
    summary="Upload OpenAI consent recording",
)
async def upload_openai_consent(
    body: OpenAIConsentRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(
        get_resource_access_service,
    ),
) -> OpenAIConsentResponse:
    """Upload a consent recording to OpenAI to obtain a consent ID.

    The consent recording must contain the voice actor reading
    a specific consent phrase (see OpenAI docs).

    Args:
        body: Consent request with audio and language.
        user_id: Injected authenticated user ID.
        access: Injected access service.

    Returns:
        OpenAIConsentResponse with the consent_id.
    """
    cred_data, _ = await _find_credential(
        access,
        user_id,
        "openai_credential",
    )
    api_key = cred_data.get("api_key", "")

    audio_bytes = base64.b64decode(body.audio_base64)
    mime_type, _ = mimetypes.guess_type(body.audio_filename)
    if mime_type is None:
        mime_type = "audio/wav"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/voice_consents",
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                data={
                    "name": body.name,
                    "language": body.language,
                },
                files={
                    "recording": (
                        body.audio_filename,
                        audio_bytes,
                        mime_type,
                    ),
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Network error calling OpenAI: {exc}",
        ) from exc

    if resp.status_code not in (200, 201):
        detail = resp.text[:500]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"OpenAI consent upload failed "
                f"({resp.status_code}): {detail}"
            ),
        )

    result = resp.json()
    consent_id = result.get("id", "")
    if not consent_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "OpenAI returned success but no consent "
                f"id in response: {result}"
            ),
        )

    return OpenAIConsentResponse(consent_id=consent_id)


async def _find_credential(
    access: ResourceAccessService,
    user_id: str,
    cred_type: str,
) -> tuple[dict, str]:
    """Find and resolve a credential by type for a user.

    Args:
        access: Resource access service.
        user_id: Authenticated user id.
        cred_type: Credential type string (e.g.
            'dashscope_credential', 'openai_credential').

    Returns:
        Tuple of (raw credential data dict, credential_id).

    Raises:
        HTTPException: If no matching credential is found.
    """
    credentials = await access.list_resource(
        user_id,
        ResourceKind.CREDENTIAL,
    )
    cred_id: str | None = None
    for cred in credentials:
        if cred.data.get("type") == cred_type:
            cred_id = cred.id
            break

    if cred_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"No {cred_type} found. " f"Please configure one first."),
        )

    record = await access.resolve_credential(user_id, cred_id)
    return record.data, cred_id


@voice_profile_router.post(
    "/clone",
    response_model=CloneVoiceResponse,
    summary="Clone a voice from audio",
)
async def clone_voice(
    body: CloneVoiceRequest,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(
        get_resource_access_service,
    ),
) -> CloneVoiceResponse:
    """Clone a voice by uploading audio to DashScope.

    Supports both CosyVoice (url-based) and Qwen-TTS
    (base64-based) voice enrollment APIs.

    Args:
        body: Clone request with engine, model, and audio.
        user_id: Injected authenticated user ID.
        access: Injected access service.

    Returns:
        CloneVoiceResponse with the generated voice_id.
    """
    if not ENGINE_VOICE_CLONING.get(body.engine, False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Engine '{body.engine}' does not " f"support voice cloning."
            ),
        )

    # Model-level voice_cloning validation (skip for OpenAI
    # whose voice creation API is model-agnostic)
    if body.engine != "openai_tts":
        cred_type = ENGINE_TO_CREDENTIAL_TYPE.get(body.engine)
        if cred_type:
            cred_cls = CredentialFactory.get_credential_class(
                cred_type,
            )
            if cred_cls is not None:
                cards = cred_cls.list_tts_models()
                card = next(
                    (c for c in cards if c.name == body.model),
                    None,
                )
                if card is not None and not card.voice_cloning:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Model '{body.model}' does not "
                            f"support voice cloning."
                        ),
                    )

    # OpenAI cloning: multipart/form-data upload
    if body.engine == "openai_tts":
        return await _clone_openai(body, access, user_id)

    # DashScope-based cloning (Qwen-TTS / CosyVoice)
    return await _clone_dashscope(body, access, user_id)


async def _clone_dashscope(
    body: CloneVoiceRequest,
    access: ResourceAccessService,
    user_id: str,
) -> CloneVoiceResponse:
    """Clone a voice via DashScope (Qwen-TTS or CosyVoice).

    Args:
        body: Clone request data.
        access: Resource access service.
        user_id: Authenticated user id.

    Returns:
        CloneVoiceResponse with the generated voice_id.
    """
    if not body.model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model is required for DashScope cloning.",
        )
    cred_data, cred_id = await _find_credential(
        access,
        user_id,
        "dashscope_credential",
    )
    api_key = cred_data.get("api_key", "")

    mime_type, _ = mimetypes.guess_type(body.audio_filename)
    if mime_type is None:
        mime_type = "audio/wav"

    if body.engine in ("dashscope_tts",):
        if not body.audio_base64:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="audio_base64 is required for Qwen-TTS.",
            )
        audio_data_url = f"data:{mime_type};base64,{body.audio_base64}"
        payload: dict = {
            "model": "qwen-voice-enrollment",
            "input": {
                "action": "create",
                "target_model": body.model,
                "preferred_name": body.prefix,
                "audio": {"data": audio_data_url},
            },
        }
        if body.text:
            payload["input"]["text"] = body.text
    else:
        if not body.audio_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "audio_url is required for CosyVoice "
                    "(must be a publicly accessible URL)."
                ),
            )
        payload = {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": body.model,
                "prefix": body.prefix,
                "url": body.audio_url,
            },
        }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                _CLONE_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Network error calling DashScope: {exc}",
        ) from exc

    if resp.status_code != 200:
        detail = resp.text[:500]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"DashScope voice clone failed "
                f"({resp.status_code}): {detail}"
            ),
        )

    result = resp.json()
    output = result.get("output", {})
    voice_id = (
        output.get("voice")
        or output.get("voice_id")
        or output.get("voice_name", "")
    )
    if not voice_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "DashScope returned success but no "
                f"voice found in response: {result}"
            ),
        )

    return CloneVoiceResponse(
        voice_id=voice_id,
        credential_id=cred_id,
    )


_OPENAI_VOICES_URL = "https://api.openai.com/v1/audio/voices"


async def _clone_openai(
    body: CloneVoiceRequest,
    access: ResourceAccessService,
    user_id: str,
) -> CloneVoiceResponse:
    """Clone a voice via OpenAI's voice creation API.

    Args:
        body: Clone request data.
        access: Resource access service.
        user_id: Authenticated user id.

    Returns:
        CloneVoiceResponse with the generated voice id.
    """
    if not body.audio_base64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="audio_base64 is required for OpenAI.",
        )
    if not body.consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("consent token is required for OpenAI " "voice cloning."),
        )

    cred_data, cred_id = await _find_credential(
        access,
        user_id,
        "openai_credential",
    )
    api_key = cred_data.get("api_key", "")

    audio_bytes = base64.b64decode(body.audio_base64)
    mime_type, _ = mimetypes.guess_type(body.audio_filename)
    if mime_type is None:
        mime_type = "audio/wav"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                _OPENAI_VOICES_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                data={
                    "name": body.prefix,
                    "consent": body.consent,
                },
                files={
                    "audio_sample": (
                        body.audio_filename,
                        audio_bytes,
                        mime_type,
                    ),
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Network error calling OpenAI: {exc}",
        ) from exc

    if resp.status_code not in (200, 201):
        detail = resp.text[:500]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"OpenAI voice clone failed " f"({resp.status_code}): {detail}"
            ),
        )

    result = resp.json()
    voice_id = result.get("id", "")
    if not voice_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "OpenAI returned success but no voice "
                f"id in response: {result}"
            ),
        )

    return CloneVoiceResponse(
        voice_id=voice_id,
        credential_id=cred_id,
    )
