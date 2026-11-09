# -*- coding: utf-8 -*-
"""Voice profile router — CRUD endpoints."""
import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, status

from ..access import ResourceKind
from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_storage,
)
from .._service import ResourceAccessService, validate_voice_profile_model
from ..storage import StorageBase, VoiceProfileData, VoiceProfileRecord
from ..storage._model import (
    ENGINE_TO_CREDENTIAL_TYPE,
    get_missing_voice_profile_binding_fields,
)
from ...credential import (
    CredentialFactory,
    OpenAICredential,
)
from ...tts import (
    OpenAIVoiceCloningClient,
    TTSModelCard,
    VoiceCloningError,
)
from ._schema import (
    AvailableEnginesResponse,
    CloneVoiceRequest,
    CloneVoiceResponse,
    CreateVoiceProfileRequest,
    CreateVoiceProfileResponse,
    CredentialRef,
    EngineInfo,
    EngineModelInfo,
    ListVoiceProfilesResponse,
    OpenAIConsentRequest,
    OpenAIConsentResponse,
    UpdateVoiceProfileRequest,
)
from ._schema._voice_profile import MAX_AUDIO_SIZE_BYTES

voice_profile_router = APIRouter(
    prefix="/voice-profile",
    tags=["voice_profile"],
    responses={404: {"description": "Not found"}},
)


async def _resolve_owned_credential(
    access: ResourceAccessService,
    user_id: str,
    credential_id: str,
    expected_type: str,
) -> dict:
    """Resolve an owner-scoped credential and validate its provider type.

    Args:
        access: Resource access service.
        user_id: Authenticated user id.
        credential_id: Credential selected for the voice.
        expected_type: Credential type required by the engine.
    """
    record = await access.resolve_credential(
        user_id,
        credential_id,
    )
    if record.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Custom voices require a credential owned by the "
                "authenticated user; shared credentials are not allowed."
            ),
        )

    actual_type = record.data.get("type")
    if actual_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Credential {credential_id!r} has type {actual_type!r}; "
                f"expected {expected_type!r}."
            ),
        )
    return record.data


async def _validate_voice_profile_data(
    access: ResourceAccessService,
    user_id: str,
    data: VoiceProfileData,
) -> None:
    """Validate an authorization binding before storing a voice profile.

    Runtime use is independently guarded by
    ``_service._tts_model._validate_voice_binding``. Both layers share the
    required-field definition so stored and runtime validation stay aligned.
    """
    missing = get_missing_voice_profile_binding_fields(data)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Voice profile requires " + ", ".join(missing) + ".",
        )

    assert data.engine is not None
    assert data.credential_id is not None
    credential_data = await _resolve_owned_credential(
        access,
        user_id,
        data.credential_id,
        ENGINE_TO_CREDENTIAL_TYPE[data.engine],
    )
    validate_voice_profile_model(
        CredentialFactory.from_dict(credential_data),
        data,
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
        # Sharing a provider credential must not implicitly share every custom
        # voice in the upstream account.
        if cred.user_id != user_id:
            continue
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

    details = []
    models_by_type: dict[str, list[TTSModelCard]] = {}
    for eng in engines:
        credential_type = ENGINE_TO_CREDENTIAL_TYPE[eng]
        cred_cls = CredentialFactory.get_credential_class(credential_type)
        client_cls = (
            cred_cls.get_voice_cloning_client_class() if cred_cls else None
        )
        if credential_type not in models_by_type:
            models_by_type[credential_type] = (
                cred_cls.list_tts_models() if cred_cls else []
            )
        models = models_by_type[credential_type]
        if credential_type == "dashscope_credential":
            # The App groups Qwen models together, including Qwen Audio.
            models = [
                card
                for card in models
                if card.name.startswith("cosyvoice-") == (eng == "cosyvoice")
            ]
        details.append(
            EngineInfo(
                name=eng,
                credential_type=credential_type,
                models=[
                    EngineModelInfo(
                        **card.model_dump(),
                        enrollment_model=client_cls.get_enrollment_model(
                            card.name,
                        )
                        if client_cls and card.voice_cloning
                        else None,
                    )
                    for card in models
                ],
                voice_cloning=client_cls is not None,
                credentials=cred_by_type.get(
                    credential_type,
                    [],
                ),
            ),
        )
    return AvailableEnginesResponse(
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
    await _validate_voice_profile_data(
        access,
        user_id,
        body.data,
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
    await _validate_voice_profile_data(
        access,
        user_id,
        body.data,
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


def _decode_audio(audio_base64: str) -> bytes:
    """Decode a non-empty audio payload or reject malformed input."""
    try:
        audio = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="audio_base64 must contain valid base64 audio.",
        ) from exc
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="audio_base64 must contain non-empty audio.",
        )
    if len(audio) > MAX_AUDIO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio must not exceed 10 MiB.",
        )
    return audio


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
    cred_data = await _resolve_owned_credential(
        access,
        user_id,
        body.credential_id,
        "openai_credential",
    )
    audio = _decode_audio(body.audio_base64)
    client = OpenAIVoiceCloningClient(
        OpenAICredential.model_validate(cred_data),
    )
    try:
        consent_id = await client.create_consent(
            audio,
            name=body.name,
            language=body.language,
            filename=body.audio_filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VoiceCloningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return OpenAIConsentResponse(consent_id=consent_id)


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
    """Clone a voice using OpenAI or DashScope voice enrollment.

    Supports OpenAI and Qwen-TTS uploads and CosyVoice audio URLs.

    Args:
        body: Clone request with engine, model, and audio.
        user_id: Injected authenticated user ID.
        access: Injected access service.

    Returns:
        CloneVoiceResponse with the generated voice_id.
    """
    cred_type = ENGINE_TO_CREDENTIAL_TYPE.get(body.engine)
    cred_cls = (
        CredentialFactory.get_credential_class(cred_type)
        if cred_type is not None
        else None
    )
    client_cls = (
        cred_cls.get_voice_cloning_client_class() if cred_cls else None
    )
    if client_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Engine '{body.engine}' does not support voice cloning.",
        )

    if body.model and cred_cls is not None:
        card = next(
            (c for c in cred_cls.list_tts_models() if c.name == body.model),
            None,
        )
        if card is not None and not card.voice_cloning:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{body.model}' does not support voice cloning.",
            )

    assert cred_type is not None
    cred_data = await _resolve_owned_credential(
        access,
        user_id,
        body.credential_id,
        cred_type,
    )
    # Adapt existing HTTP fields to provider options without SDK dependencies
    # on App schemas. New provider-specific UI fields belong in this adapter.
    values = {
        "name": body.prefix,
        "prefix": body.prefix,
        "target_model": body.model,
        "text": body.text,
        "consent": body.consent,
    }
    audio = (
        _decode_audio(body.audio_base64)
        if body.audio_base64 is not None
        else None
    )
    try:
        credential = CredentialFactory.from_dict(cred_data)
        client = client_cls(credential)
        parameters = client.Parameters.model_validate(
            {
                name: value
                for name, value in values.items()
                if name in client.Parameters.model_fields
            },
        )
        voice_id = await client.create_voice(
            audio,
            filename=body.audio_filename,
            audio_url=body.audio_url,
            parameters=parameters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VoiceCloningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return CloneVoiceResponse(
        voice_id=voice_id,
        credential_id=body.credential_id,
    )
