# -*- coding: utf-8 -*-
"""Realtime model service: builds a RealtimeModelBase from stored credential +
config."""
from fastapi import HTTPException, status

from ._access import ResourceAccessService
from ..storage import ChatModelConfig
from ...credential import CredentialFactory
from ...realtime import RealtimeModelBase


async def get_realtime_model(
    user_id: str,
    config: ChatModelConfig,
    access: ResourceAccessService,
) -> RealtimeModelBase:
    """Build a realtime model from the configured concrete credential.

    Args:
        user_id (`str`):
            The user id.
        config (`ChatModelConfig`):
            The chat model configuration (reused for realtime — same shape:
            credential_id + model + parameters).
        access (`ResourceAccessService`):
            Resolves the concrete credential, including shared credentials.

    Returns:
        `RealtimeModelBase`:
            The realtime model instance.
    """
    credential_record = await access.resolve_credential(
        user_id,
        config.credential_id,
    )

    credential = CredentialFactory.from_dict(credential_record.data)
    if not credential.supports_realtime():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Realtime is unavailable for this credential. Configure "
                "realtime_base_url when using a custom base_url."
            ),
        )
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
