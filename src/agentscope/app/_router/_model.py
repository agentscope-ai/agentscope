# -*- coding: utf-8 -*-
"""The model router."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_model_card_dirs
from ._schema import ListModelsResponse, ListModelsRequest
from ...credential import CredentialFactory

model_router = APIRouter(
    prefix="/model",
    tags=["model"],
    responses={404: {"description": "Not found"}},
)


@model_router.get(
    "/",
    response_model=ListModelsResponse,
    summary="List all candidate models under the given credential type",
)
async def list_models(
    body: ListModelsRequest = Depends(),
    model_card_dirs: dict = Depends(get_model_card_dirs),
) -> ListModelsResponse:
    """Return all candidate models under the given credential type.

    Includes the built-in YAML cards plus any cards configured through
    ``create_app(model_card_dirs=...)`` for this credential type.

    Args:
        body (ListModelsRequest): The request body.
        model_card_dirs (dict): Extra card directories keyed by
            credential type.

    Returns:
        `ListModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    from .._service import card_subdirs

    extra_dirs = card_subdirs(model_card_dirs, body.provider).get("chat")
    models = credential_cls.get_chat_model_class().list_models(
        extra_yaml_dirs=extra_dirs,
    )
    return ListModelsResponse(models=models, total=len(models))
