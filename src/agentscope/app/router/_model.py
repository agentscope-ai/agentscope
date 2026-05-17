# -*- coding: utf-8 -*-
"""The model router."""

from fastapi import APIRouter, Depends, HTTPException, status

from .._schema import ListModelResponse, ListModelRequest
from ...credential import CredentialFactory

model_router = APIRouter(
    prefix="/model",
    tags=["model"],
    responses={404: {"description": "Not found"}},
)


@model_router.get(
    "/",
    response_model=ListModelResponse,
    summary="List all candidate models under the given credential type",
)
async def list_models(
    body: ListModelRequest = Depends(),
) -> ListModelResponse:
    """Return all candidate models under the given credential type.

    Args:
        body (ListModelRequest): The request body.

    Returns:
        `ListModelResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    models = credential_cls.get_chat_model_class().list_models()
    return ListModelResponse(models=models, total=len(models))
