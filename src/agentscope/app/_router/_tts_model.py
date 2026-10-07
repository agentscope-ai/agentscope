# -*- coding: utf-8 -*-
"""The TTS model router."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_model_card_dirs
from ._schema import ListTTSModelsResponse, ListTTSModelsRequest
from ...credential import CredentialFactory

tts_model_router = APIRouter(
    prefix="/tts-model",
    tags=["tts-model"],
    responses={404: {"description": "Not found"}},
)


@tts_model_router.get(
    "/",
    response_model=ListTTSModelsResponse,
    summary="List all candidate TTS models under the given credential type",
)
async def list_tts_models(
    body: ListTTSModelsRequest = Depends(),
    model_card_dirs: dict = Depends(get_model_card_dirs),
) -> ListTTSModelsResponse:
    """Return all candidate TTS models under the given credential type.

    Includes the built-in YAML cards plus any cards configured through
    ``create_app(model_card_dirs=...)`` for this credential type.

    Args:
        body (ListTTSModelsRequest): The request body.
        model_card_dirs (dict): Extra card directories keyed by
            credential type.

    Returns:
        `ListTTSModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    from .._service import card_subdirs

    extra_dirs = card_subdirs(model_card_dirs, body.provider).get("tts")
    models = credential_cls.list_tts_models(extra_yaml_dirs=extra_dirs)
    return ListTTSModelsResponse(models=models, total=len(models))
