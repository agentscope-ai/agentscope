# -*- coding: utf-8 -*-
"""Realtime model service: builds a RealtimeModelBase from stored credential +
config."""
from fastapi import HTTPException, status

from ..storage import StorageBase, ChatModelConfig
from ...credential import CredentialFactory
from ...realtime import RealtimeModelBase


async def get_realtime_model(
    user_id: str,
    config: ChatModelConfig,
    storage: StorageBase,
) -> RealtimeModelBase:
    """Get a realtime model instance from the configuration and storage.

    Args:
        user_id (`str`):
            The user id.
        config (`ChatModelConfig`):
            The chat model configuration (reused for realtime — same shape:
            credential_id + model + parameters).
        storage (`StorageBase`):
            The storage instance.

    Returns:
        `RealtimeModelBase`:
            The realtime model instance.
    """
    credential_record = await storage.get_credential(
        user_id,
        config.credential_id,
    )
    if credential_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential {config.credential_id!r} not found.",
        )

    credential = CredentialFactory.from_dict(credential_record.data)
    realtime_cls = credential.get_realtime_model_class()
    if realtime_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Credential type {type(credential).__name__!r} does not "
                f"support realtime models."
            ),
        )
    parameters = (
        realtime_cls.Parameters(**config.parameters)
        if config.parameters
        else None
    )
    return realtime_cls(
        model_name=config.model,
        credential=credential,
        parameters=parameters,
    )
