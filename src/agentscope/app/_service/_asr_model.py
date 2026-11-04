# -*- coding: utf-8 -*-
"""ASR model construction from a stored credential."""

from typing import Type

from fastapi import HTTPException, status

from ..storage import CredentialRecord
from ._access import ResourceAccessService
from ...asr import ASRModelBase
from ...credential import CredentialFactory


async def get_asr_model(
    user_id: str,
    credential_id: str,
    model: str,
    access: ResourceAccessService,
    parameters: dict | None = None,
    realtime: bool = False,
) -> ASRModelBase:
    """Build an ASR model from a visible stored credential.

    Args:
        user_id (`str`):
            The viewer's user id.
        credential_id (`str`):
            The stored credential to use.
        model (`str`):
            The requested ASR model name.
        access (`ResourceAccessService`):
            Resource access service used to resolve shared credentials.
        parameters (`dict | None`, defaults to `None`):
            Provider-specific speech recognition parameters.
        realtime (`bool`, defaults to `False`):
            Whether to resolve a realtime rather than batch implementation.

    Returns:
        `ASRModelBase`:
            An ASR model matching the requested input mode.

    Raises:
        `HTTPException`:
            If the credential has no matching ASR implementation or the model
            name is not available for it.
        `pydantic.ValidationError`:
            If the provider-specific parameters are invalid.
    """
    credential_record = await access.resolve_credential(
        user_id,
        credential_id,
    )
    return _build_asr_model(
        credential_record,
        model,
        parameters,
        realtime,
    )


def _build_asr_model(
    credential_record: CredentialRecord,
    model: str,
    parameters: dict | None = None,
    realtime: bool = False,
) -> ASRModelBase:
    """Construct the requested ASR model from a credential record.

    Args:
        credential_record (`CredentialRecord`):
            The unmasked stored credential record.
        model (`str`):
            The requested ASR model name.
        parameters (`dict | None`, defaults to `None`):
            Provider-specific speech recognition parameters.
        realtime (`bool`, defaults to `False`):
            Whether to select a realtime implementation.

    Returns:
        `ASRModelBase`:
            The configured ASR model.

    Raises:
        `HTTPException`:
            If no implementation advertises the requested model.
        `pydantic.ValidationError`:
            If the credential or provider-specific parameters are invalid.
    """
    credential = CredentialFactory.from_dict(credential_record.data)
    asr_classes = [
        asr_cls
        for asr_cls in credential.get_asr_model_classes()
        if asr_cls.realtime is realtime
    ]
    asr_cls = _resolve_asr_class(asr_classes, model)
    parsed_parameters = (
        asr_cls.Parameters(**parameters) if parameters is not None else None
    )
    return asr_cls(
        credential=credential,
        model=model,
        parameters=parsed_parameters,
    )


def _resolve_asr_class(
    classes: list[Type[ASRModelBase]],
    model: str,
) -> Type[ASRModelBase]:
    """Find the ASR implementation that advertises a model name."""
    for asr_cls in classes:
        if any(card.name == model for card in asr_cls.list_models()):
            return asr_cls
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"ASR model {model!r} is not available for this credential.",
    )
