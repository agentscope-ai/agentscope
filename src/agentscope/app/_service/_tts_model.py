# -*- coding: utf-8 -*-
"""TTS model service: builds a TTSModelBase from stored credential + config."""
from typing import Type

from fastapi import HTTPException, status

from ._access import ResourceAccessService
from ..storage import TTSModelConfig
from ...credential import CredentialFactory
from ...tts import TTSModelBase


async def get_tts_model(
    user_id: str,
    config: TTSModelConfig,
    access: ResourceAccessService,
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

    Returns:
        `TTSModelBase`:
            The TTS model instance.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    tts_classes = credential.get_tts_model_classes()
    if not tts_classes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider {config.type!r} does not support TTS models.",
        )

    tts_cls = _resolve_tts_class(tts_classes, config.model)
    parameters = (
        tts_cls.Parameters(**config.parameters) if config.parameters else None
    )
    return tts_cls(
        credential=credential,
        model=config.model,
        parameters=parameters,
    )


def _resolve_tts_class(
    classes: list[Type[TTSModelBase]],
    model: str,
) -> Type[TTSModelBase]:
    """Pick the TTS class that lists the given model name."""
    for cls in classes:
        if any(card.name == model for card in cls.list_models()):
            return cls
    return classes[0]
