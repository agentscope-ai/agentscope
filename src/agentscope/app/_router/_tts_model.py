# -*- coding: utf-8 -*-
"""The TTS model router."""

from fastapi import APIRouter, Depends, HTTPException, status

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
) -> ListTTSModelsResponse:
    """Return all candidate TTS models under the given credential type.

    Args:
        body (ListTTSModelsRequest): The request body.

    Returns:
        `ListTTSModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    models = credential_cls.list_tts_models()
    return ListTTSModelsResponse(models=models, total=len(models))
