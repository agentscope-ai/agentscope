# -*- coding: utf-8 -*-
"""TTS model service: builds a TTSModelBase from stored credential + config."""
import re
from typing import Type

from fastapi import HTTPException, status
from pydantic import BaseModel, SecretStr, ValidationError

from ._access import ResourceAccessService
from ..storage import StorageBase, TTSModelConfig, VoiceProfileData
from ..storage._model import (
    ENGINE_TO_CREDENTIAL_TYPE,
    get_missing_voice_profile_binding_fields,
)
from ...credential import CredentialBase, CredentialFactory
from ...tts import OpenAITTSModel, TTSModelBase, TTSModelCard
from ..._logging import logger


async def get_tts_model(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
    storage: StorageBase,
) -> TTSModelBase | None:
    """Build a TTS model using the latest owned voice profile, if bound.

    Args:
        user_id (`str`):
            The viewer's user id (may differ from the credential owner
            for shared credentials).
        config (`TTSModelConfig`):
            The TTS model configuration.
        access (`ResourceAccessService`):
            Injected resource access service.
        storage (`StorageBase`):
            Owner-scoped storage used to validate custom voice profiles.

    Returns:
        `TTSModelBase | None`:
            The TTS model instance, or ``None`` when its configuration
            is no longer valid or accessible.
    """
    credential = None
    data = None
    try:
        if config.voice_profile_id is not None:
            data = await _get_voice_profile_data(
                user_id,
                config.voice_profile_id,
                storage,
            )
            assert data.engine is not None
            provider_type = ENGINE_TO_CREDENTIAL_TYPE[data.engine]
            values = (
                dict(config.parameters)
                if config.type == provider_type and config.model == data.model
                else {}
            )
            values["voice"] = data.voice
            config = config.model_copy(
                update={
                    "type": provider_type,
                    "credential_id": data.credential_id,
                    "model": data.model,
                    "parameters": values,
                },
            )
        credential, tts_cls = await _validate_and_resolve_tts_config(
            user_id,
            config,
            access,
            storage,
            resolved_profile=data,
        )
        parameters = _tts_parameters(tts_cls, config)
        return tts_cls(
            credential=credential,
            model=config.model,
            parameters=parameters,
        )
    except (HTTPException, ValueError) as exc:
        logger.warning(
            "Skipping TTS for user %r, model %r: %s",
            user_id,
            config.model,
            _config_error_detail(exc, credential),
        )
        return None


def _config_error_detail(
    exc: HTTPException | ValueError,
    credential: CredentialBase | None,
) -> str:
    """Keep diagnostic fields while excluding submitted values and secrets."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['type']}"
            for item in exc.errors(include_input=False, include_context=False)
        )[:500]
    detail = str(exc.detail if isinstance(exc, HTTPException) else exc)

    if credential is not None:
        for value in credential.model_dump().values():
            if isinstance(value, SecretStr) and value.get_secret_value():
                detail = detail.replace(value.get_secret_value(), "[redacted]")
    detail = re.sub(r"(?:https?|wss?)://\S+", "[URL]", detail)
    return f"{type(exc).__name__}: {detail}"[:500]


async def validate_tts_model_config(
    user_id: str,
    config: TTSModelConfig | None,
    access: ResourceAccessService,
    storage: StorageBase,
) -> None:
    """Validate a TTS config before it is persisted on a session."""
    if config is None:
        return
    _, tts_cls = await _validate_and_resolve_tts_config(
        user_id,
        config,
        access,
        storage,
    )

    try:
        _tts_parameters(tts_cls, config)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TTS parameters for the selected model.",
        ) from exc


def _tts_parameters(
    tts_cls: Type[TTSModelBase],
    config: TTSModelConfig,
) -> BaseModel:
    """Validate parameters, adapting profile voice IDs for OpenAI."""
    values = dict(config.parameters)
    card = _find_model_card(tts_cls, config.model)
    if issubclass(tts_cls, OpenAITTSModel) and config.voice_profile_id:
        voice = values.get("voice")
        if isinstance(voice, str) and voice not in _get_preset_voices(card):
            values["voice"] = {"id": voice}
    return tts_cls.Parameters(**values)


def validate_voice_profile_model(
    credential: CredentialBase,
    data: VoiceProfileData,
) -> None:
    """Reject known incompatible profiles without blocking new deployments."""
    classes = credential.get_tts_model_classes()
    if not classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credential does not support TTS models.",
        )
    assert data.model is not None
    tts_cls = _resolve_tts_class(classes, data.model)
    _validate_profile_model(_find_model_card(tts_cls, data.model), data.voice)
    try:
        tts_cls.Parameters(voice=data.voice)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid voice for the selected model.",
        ) from exc


def _validate_profile_model(
    card: TTSModelCard | None,
    voice: object,
) -> None:
    """Known models must support either this preset or custom voices."""
    if (
        card is not None
        and not card.voice_cloning
        and not (isinstance(voice, str) and voice in _get_preset_voices(card))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model {card.name!r} does not support custom voices.",
        )


async def _validate_and_resolve_tts_config(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
    storage: StorageBase,
    *,
    resolved_profile: VoiceProfileData | None = None,
) -> tuple[CredentialBase, Type[TTSModelBase]]:
    """Resolve the provider and enforce custom voice ownership."""
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )
    credential_type = credential_record.data.get("type")
    if credential_type != config.type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"TTS config type {config.type!r} does not match credential "
                f"type {credential_type!r}."
            ),
        )

    credential = CredentialFactory.from_dict(credential_record.data)
    tts_classes = credential.get_tts_model_classes()
    if not tts_classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {config.type!r} does not support TTS models.",
        )

    tts_cls = _resolve_tts_class(tts_classes, config.model)
    card = _find_model_card(tts_cls, config.model)
    await _validate_voice_binding(
        user_id=user_id,
        config=config,
        credential_owner_id=credential_record.user_id,
        storage=storage,
        card=card,
        resolved_profile=resolved_profile,
    )
    if config.voice_profile_id or (
        issubclass(tts_cls, OpenAITTSModel)
        and isinstance(config.parameters.get("voice"), dict)
    ):
        _validate_profile_model(card, config.parameters.get("voice"))
    return credential, tts_cls


async def _validate_voice_binding(
    *,
    user_id: str,
    config: TTSModelConfig,
    credential_owner_id: str,
    storage: StorageBase,
    card: TTSModelCard | None,
    resolved_profile: VoiceProfileData | None = None,
) -> None:
    """Validate preset voices or an exact owner-scoped custom voice profile.

    Profile CRUD performs the write-time counterpart in
    ``_router._voice_profile._validate_voice_profile_data``. Runtime
    validation remains here as defense against stale or externally persisted
    data, while both layers share the required binding-field definition.
    """
    voice = config.parameters.get("voice")
    preset_voices = _get_preset_voices(card)
    if config.voice_profile_id is None:
        if credential_owner_id == user_id and voice is not None:
            return
        if _requires_voice_profile(card, preset_voices):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"TTS model {config.model!r} requires a cloned voice "
                    "profile."
                ),
            )
        if voice is None:
            return
        if isinstance(voice, str) and voice in preset_voices:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("A non-preset voice requires an owned voice_profile_id."),
        )

    data = resolved_profile
    if data is None:
        data = await _get_voice_profile_data(
            user_id,
            config.voice_profile_id,
            storage,
        )

    assert data.engine is not None
    assert data.credential_id is not None
    if credential_owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Custom voices cannot use a shared credential.",
        )

    # Runtime already derived its config from this owner-scoped snapshot.
    # Persisted session writes still require an exact binding comparison.
    if resolved_profile is not None:
        return
    expected_type = ENGINE_TO_CREDENTIAL_TYPE[data.engine]
    bindings = {
        "credential_id": (config.credential_id, data.credential_id),
        "provider type": (config.type, expected_type),
        "model": (config.model, data.model),
        "voice": (voice, data.voice),
    }
    mismatches = [
        name
        for name, (actual, expected) in bindings.items()
        if actual != expected
    ]
    if mismatches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "TTS config does not match voice profile "
                f"{config.voice_profile_id!r}: " + ", ".join(mismatches) + "."
            ),
        )


async def _get_voice_profile_data(
    user_id: str,
    profile_id: str,
    storage: StorageBase,
) -> VoiceProfileData:
    """Load a complete profile using an owner-scoped lookup."""
    profile = await storage.get_voice_profile(user_id, profile_id)
    if profile is None:
        # Do not reveal whether another tenant owns the supplied profile id.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile {profile_id!r} not found.",
        )
    missing = get_missing_voice_profile_binding_fields(profile.data)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Voice profile {profile.id!r} is incomplete: "
                + ", ".join(missing)
                + "."
            ),
        )
    return profile.data


def _find_model_card(
    tts_cls: Type[TTSModelBase],
    model: str,
) -> TTSModelCard | None:
    """Return the exact model card used to classify preset voices."""
    return next(
        (card for card in tts_cls.list_models() if card.name == model),
        None,
    )


def _get_preset_voices(card: TTSModelCard | None) -> set[str]:
    """Return the provider-declared preset voices for a model card."""
    if card is None:
        return set()
    properties = card.parameter_schema.get("properties", {})
    voice_schema = properties.get("voice", {})
    voices = voice_schema.get("enum", [])
    if not isinstance(voices, list):
        return set()
    return {voice for voice in voices if isinstance(voice, str)}


def _requires_voice_profile(
    card: TTSModelCard | None,
    preset_voices: set[str] | None = None,
) -> bool:
    """Return whether a model can only synthesize with a cloned voice.

    A voice-cloning model with no provider-declared preset voices is treated
    as clone-only. In particular, omitting ``voice`` must not fall through to
    a shared parameter class default that is invalid for that model.
    """
    if card is None or not card.voice_cloning:
        return False
    if preset_voices is None:
        preset_voices = _get_preset_voices(card)
    return not preset_voices


def _resolve_tts_class(
    classes: list[Type[TTSModelBase]],
    model: str,
) -> Type[TTSModelBase]:
    """Pick the TTS class that lists the given model name."""
    for cls in classes:
        if any(card.name == model for card in cls.list_models()):
            return cls
    return classes[0]
