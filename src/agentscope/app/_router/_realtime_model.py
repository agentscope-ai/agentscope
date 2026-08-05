# -*- coding: utf-8 -*-
"""The realtime model router."""

from fastapi import APIRouter, Depends

from ._schema import ListRealtimeModelsResponse, ListRealtimeModelsRequest
from ..deps import get_current_user_id, get_resource_access_service
from .._service import ResourceAccessService
from ...credential import CredentialFactory

realtime_model_router = APIRouter(
    prefix="/realtime-model",
    tags=["realtime-model"],
    responses={404: {"description": "Not found"}},
)


@realtime_model_router.get(
    "/",
    response_model=ListRealtimeModelsResponse,
    summary="List candidate realtime models for a concrete credential",
)
async def list_realtime_models(
    body: ListRealtimeModelsRequest = Depends(),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListRealtimeModelsResponse:
    """Return candidate realtime models available to one credential.

    Args:
        body (`ListRealtimeModelsRequest`): The credential selection.
        user_id (`str`): The current viewer.
        access (`ResourceAccessService`): Credential access resolver.

    Returns:
        `ListRealtimeModelsResponse`: The response body.
    """
    record = await access.resolve_credential(user_id, body.credential_id)
    credential = CredentialFactory.from_dict(record.data)
    models = credential.list_available_realtime_models()
    return ListRealtimeModelsResponse(models=models, total=len(models))
