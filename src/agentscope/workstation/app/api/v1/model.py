# -*- coding: utf-8 -*-
"""Model related services"""
from fastapi import APIRouter, Depends

from app.api.deps import CurrentAccount, SessionDep, get_workspace_id
from app.schemas.response import create_response
from app.services.model_service import ModelService
from app.services.provider_service import ProviderService

router = APIRouter(prefix="/model", tags=["model"])


@router.get("/{model_type}/selector")
async def model_selector(
    model_type: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Model selector grouped by provider"""
    try:
        provider_service = ProviderService(session=session)
        model_service = ModelService(session=session)
        providers = provider_service.list_providers(workspace_id=workspace_id)
        models = model_service.list_models_by_type(
            model_type=model_type,
            workspace_id=workspace_id,
        )
        # Group model by provider
        provider_map = {p.provider: p for p in providers}
        result = []
        for provider_id, provider in provider_map.items():
            provider_models = [
                m.model_dump() for m in models if m.provider == provider_id
            ]
            if provider_models:
                result.append(
                    {
                        "provider": provider.model_dump(),
                        "model": provider_models,
                    },
                )
        return create_response(
            code="200",
            message="success",
            data=result,
        )
    except Exception as e:
        return create_response(
            code="500",
            message=str(e),
            data=[],
        )
