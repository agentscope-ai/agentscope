# -*- coding: utf-8 -*-
"""The realtime model router."""

from fastapi import APIRouter, Depends, HTTPException, status

from ._schema import ListRealtimeModelsResponse, ListRealtimeModelsRequest
from ...credential import CredentialFactory

realtime_model_router = APIRouter(
    prefix="/realtime-model",
    tags=["realtime-model"],
    responses={404: {"description": "Not found"}},
)


@realtime_model_router.get(
    "/",
    response_model=ListRealtimeModelsResponse,
    summary="List all candidate realtime models under the given credential "
    "type",
)
async def list_realtime_models(
    body: ListRealtimeModelsRequest = Depends(),
) -> ListRealtimeModelsResponse:
    """Return all candidate realtime models under the given credential type.

    Args:
        body (ListRealtimeModelsRequest): The request body.

    Returns:
        `ListRealtimeModelsResponse`: The response body.
    """
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{body.provider}' not found.",
        )

    models = credential_cls.list_realtime_models()
    return ListRealtimeModelsResponse(models=models, total=len(models))
