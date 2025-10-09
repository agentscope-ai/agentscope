# -*- coding: utf-8 -*-
"""Provider related services"""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentAccount, SessionDep, get_workspace_id
from app.schemas.provider import ModelCredential
from app.schemas.response import create_response, create_success_response
from app.services.model_service import ModelService
from app.services.provider_service import ProviderService
from app.utils.crypto import decrypt_with_rsa

router = APIRouter(prefix="/providers", tags=["provider"])


class AddProviderRequest(BaseModel):
    """Add provider request"""

    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    enable: bool = True
    credential_config: Optional[dict] = None
    # supported_model_types: Optional[list[str]] = None
    protocol: str = "OpenAI"


class UpdateProviderRequest(BaseModel):
    """Update provider request"""

    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    enable: Optional[bool] = None
    credential_config: Optional[dict] = None
    # supported_model_types: Optional[list[str]] = None
    protocol: Optional[str] = None


class AddModelRequest(BaseModel):
    """Add model request"""

    model_id: str
    model_name: str
    type: str
    tags: Optional[str] = None


class UpdateModelRequest(BaseModel):
    """Update model request"""

    model_name: Optional[str] = None
    tags: Optional[str] = None
    icon: Optional[str] = None
    enable: Optional[bool] = None


class CreateModelRequest(BaseModel):
    """Create model request"""

    model_id: Optional[str] = None
    model_name: str
    tags: Optional[str] = None
    type: str = "llm"


class UpdateModelApiRequest(BaseModel):
    """Update model api request"""

    model_name: Optional[str] = None
    tags: Optional[str] = None
    icon: Optional[str] = None
    enable: Optional[bool] = None


@router.post("")
async def add_provider(
    request: AddProviderRequest,
    session: SessionDep,
    current_account: CurrentAccount,
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """New providers"""
    try:
        provider_service = ProviderService(session=session)
        provider = provider_service.create_provider(
            # Generate an 8-digit random UUID
            provider=str(uuid.uuid4())[:8],
            name=request.name,
            description=request.description,
            icon=request.icon,
            enable=request.enable,
            credential=request.credential_config,
            supported_model_types=["llm"],
            protocol=request.protocol,
            workspace_id=workspace_id,
            current_account=current_account,
        )
        return create_success_response(data=provider)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/{provider}")
async def update_provider(
    provider: str,
    request: UpdateProviderRequest,
    session: SessionDep,
    current_account: CurrentAccount,
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Update provider"""
    try:
        provider_service = ProviderService(session=session)
        updated_provider = provider_service.update_provider(
            provider=provider,
            name=request.name,
            description=request.description,
            icon=request.icon,
            enable=request.enable,
            credential=request.credential_config,
            # supported_model_types=request.supported_model_types,
            protocol=request.protocol,
            workspace_id=workspace_id,
            current_account=current_account,
        )
        return create_success_response(
            message="Update provider successfully.",
            data=updated_provider,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/{provider}")
async def delete_provider(
    provider: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Delete provider"""
    try:
        provider_service = ProviderService(session=session)
        provider_service.delete_provider(provider, workspace_id)
        return create_response(
            code="200",
            message="Delete provider successfully.",
            data=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("")
async def list_providers(
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    name: Optional[str] = None,
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Search provider list"""
    try:
        provider_service = ProviderService(session=session)
        providers = provider_service.list_providers(
            name=name,
            workspace_id=workspace_id,
        )
        return create_success_response(
            code="200",
            message="List providers successfully.",
            data=providers,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# pylint: disable=unused-argument
@router.get("/protocols")
async def get_provider_protocols(
    session: SessionDep,
    current_account: CurrentAccount,
    workspace_id: str = Depends(
        get_workspace_id,
    ),
) -> dict:
    """Obtain a list of provider agreement types"""
    try:
        # The types of protocols supported can be expanded based on actual
        # conditions.
        protocols = ["OpenAI"]
        return create_response(
            code="200",
            message="Get provider protocols successfully.",
            data=protocols,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{provider}")
async def get_provider_detail(
    provider: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Get provider details"""
    try:
        provider_service = ProviderService(session=session)
        provider_detail = provider_service.get_provider(provider, workspace_id)
        if provider_detail.credential:
            credential_dict = json.loads(provider_detail.credential)
            credential = ModelCredential(**credential_dict)
            credential.api_key = decrypt_with_rsa(credential.api_key)
            provider_detail.credential = credential.__json__()
        else:
            provider_detail.credential = None
        return create_response(
            code="200",
            message="Get provider detail successfully.",
            data=provider_detail,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{provider}/model")
async def add_model(
    provider: str,
    request: CreateModelRequest,
    session: SessionDep,
    current_account: CurrentAccount,
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """New model"""
    try:
        model_service = ModelService(session=session)
        result = model_service.add_model(
            provider=provider,
            model_id=request.model_id,
            model_name=request.model_name,
            tags=request.tags,
            type_=request.type,
            workspace_id=workspace_id,
            creator=(
                current_account.account_id
                if hasattr(current_account, "account_id")
                else None
            ),
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
            data=False,
        )


@router.put("/{provider}/model/{model_id}")
async def update_model(
    provider: str,
    model_id: str,
    request: UpdateModelApiRequest,
    session: SessionDep,
    current_account: CurrentAccount,
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Update model info"""
    try:
        model_service = ModelService(session=session)
        result = model_service.update_model(
            provider=provider,
            model_id=model_id,
            model_name=request.model_name,
            tags=request.tags,
            icon=request.icon,
            enable=request.enable,
            modifier=(
                current_account.account_id
                if hasattr(current_account, "account_id")
                else None
            ),
            workspace_id=workspace_id,
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
            data=False,
        )


@router.delete("/{provider}/model/{model_id}")
async def delete_model(
    provider: str,
    model_id: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Delete model"""
    try:
        model_service = ModelService(session=session)
        result = model_service.delete_model(
            provider=provider,
            model_id=model_id,
            workspace_id=workspace_id,
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
            data=False,
        )


@router.get("/{provider}/model")
async def list_models(
    provider: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """List model for a provider"""
    try:
        model_service = ModelService(session=session)
        models = model_service.list_models(
            provider=provider,
            workspace_id=workspace_id,
        )
        # Format tags as list, and only return required fields
        data = []
        for m in models:
            data.append(
                {
                    "model_id": m.model_id,
                    "name": m.name,
                    "provider": m.provider,
                    "mode": m.mode,
                    "type": m.type,
                    "tags": (
                        [t.strip() for t in m.tags.split(",") if t.strip()]
                        if m.tags
                        else []
                    ),
                    "icon": m.icon,
                },
            )
        return create_response(
            code="200",
            message="success",
            data=data,
        )
    except Exception as e:
        return create_response(
            code="500",
            message=str(e),
            data=[],
        )


@router.get("/{provider}/model/{model_id}")
async def get_model_detail(
    provider: str,
    model_id: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(get_workspace_id),
) -> dict:
    """Get model detail"""
    try:
        model_service = ModelService(session=session)
        m = model_service.get_model(
            provider=provider,
            model_id=model_id,
            workspace_id=workspace_id,
        )
        if not m:
            raise ValueError("Model not found")
        data = {
            "model_id": m.model_id,
            "name": m.name,
            "provider": m.provider,
            "mode": m.mode,
            "type": m.type,
            "tags": (
                [t.strip() for t in m.tags.split(",") if t.strip()]
                if m.tags
                else []
            ),
            "icon": m.icon,
        }
        return create_response(
            code="200",
            message="success",
            data=data,
        )
    except Exception as e:
        return create_response(
            code="500",
            message=str(e),
            data=None,
        )


@router.get("/{provider}/model/{model_id}/parameter_rules")
async def get_model_parameter_rules(
    provider: str,
    model_id: str,
    session: SessionDep,
    current_account: CurrentAccount,  # pylint: disable=unused-argument
    workspace_id: str = Depends(
        get_workspace_id,
    ),  # pylint: disable=unused-argument
) -> dict:
    """Get model parameter rules"""
    try:
        model_service = ModelService(session=session)
        rules = model_service.get_parameter_rules(
            provider=provider,
            model_id=model_id,
        )
        return create_response(
            code="200",
            message="success",
            data=rules,
        )
    except Exception as e:
        return create_response(
            code="500",
            message=str(e),
            data=[],
        )
