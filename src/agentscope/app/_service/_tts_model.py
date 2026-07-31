# -*- coding: utf-8 -*-
"""TTS model service: builds a TTSModelBase from stored credential + config."""
from typing import Type

from fastapi import HTTPException, status

from ._access import ResourceAccessService
from ..storage import StorageBase, TTSModelConfig
from ..storage._model import (
    ENGINE_TO_CREDENTIAL_TYPE,
    get_missing_voice_profile_binding_fields,
)
from ...credential import CredentialBase, CredentialFactory
from ...tts import TTSModelBase, TTSModelCard


async def get_tts_model(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
    storage: StorageBase,
) -> TTSModelBase:
    """Build a TTS model instance from a stored credential and config.

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
        `TTSModelBase`:
            The TTS model instance.
    """
    credential, tts_cls = await _validate_and_resolve_tts_config(
        user_id,
        config,
        access,
        storage,
    )

    parameters = (
        tts_cls.Parameters(**config.parameters) if config.parameters else None
    )
    return tts_cls(
        credential=credential,
        model=config.model,
        parameters=parameters,
    )


async def validate_tts_model_config(
    user_id: str,
    config: TTSModelConfig | None,
    access: ResourceAccessService,
    storage: StorageBase,
) -> None:
    """Validate a TTS config before it is persisted on a session."""
    if config is None:
        return
    await _validate_and_resolve_tts_config(
        user_id,
        config,
        access,
        storage,
    )


async def _validate_and_resolve_tts_config(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
    storage: StorageBase,
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
    )
    return credential, tts_cls


async def _validate_voice_binding(
    *,
    user_id: str,
    config: TTSModelConfig,
    credential_owner_id: str,
    storage: StorageBase,
    card: TTSModelCard | None,
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

    profile = await storage.get_voice_profile(
        user_id,
        config.voice_profile_id,
    )
    if profile is None:
        # Owner-scoped lookup intentionally hides whether another tenant owns
        # the supplied profile id.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile {config.voice_profile_id!r} not found.",
        )

    data = profile.data
    missing = get_missing_voice_profile_binding_fields(data)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Voice profile {profile.id!r} is incomplete: "
                + ", ".join(missing)
                + "."
            ),
        )

    assert data.engine is not None
    assert data.credential_id is not None
    if credential_owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Custom voices cannot use a shared credential.",
        )

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
                f"TTS config does not match voice profile {profile.id!r}: "
                + ", ".join(mismatches)
                + "."
            ),
        )


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
