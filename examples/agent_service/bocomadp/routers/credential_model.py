# -*- coding: utf-8 -*-
"""按凭证查询可用模型（含"凭证绑定单模型"过滤）。

类似官方 ``GET /model/?provider=...``，但额外传 ``credential_id``：

- 凭证带 ``model`` 字段（B 方案：一凭证一模型）→ **只返回该模型**；
- 凭证没有 ``model`` 字段 → 返回该类型全部候选（``_models/*.yaml``）。

用法::

    GET /model/credential?credential_id=<id>&user_id=zy
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agentscope.app._router._schema import ListModelsResponse
from agentscope.app._service import ResourceAccessService
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_storage,
)
from agentscope.app.storage import StorageBase
from agentscope.credential import CredentialFactory

credential_model_router = APIRouter(prefix="/model", tags=["credential-model"])


@credential_model_router.get(
    "/credential",
    response_model=ListModelsResponse,
    summary="List models for a credential",
    description=(
        "Resolve the credential by id, then return its candidate models: "
        "the single bound model when the credential carries a ``model`` "
        "field, otherwise every candidate from ``_models/*.yaml``."
    ),
)
async def list_credential_models(
    credential_id: str = Query(
        ...,
        description="The credential to inspect.",
    ),
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
) -> ListModelsResponse:
    """按凭证返回可调用模型。

    ``resolve_credential`` 校验归属/共享（不可见 → 404），返回原始
    记录（含完整 payload）。从 payload 反序列化凭证后：

    - 凭证带 ``model`` → 从该类型候选里筛出对应模型（只返回一个）；
    - 不带 → 返回该类型全部候选。
    """
    record = await access.resolve_credential(user_id, credential_id)

    credential = CredentialFactory.from_dict(record.data)
    model_cls = credential.get_chat_model_class()
    cards = model_cls.list_models()

    bound = getattr(credential, "model", None)
    if bound:
        cards = [
            card
            for card in cards
            if card.name == bound
        ]
        if not cards:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Credential's model {bound!r} not found in "
                    "candidates."
                ),
            )

    return ListModelsResponse(models=cards, total=len(cards))


class ELLMCredentialPatch(BaseModel):
    """部分更新 ELLM 凭证的请求体——只包含要修改的字段。"""

    data: dict[str, Any] = Field(
        description=(
            "要更新的字段子集（如 {\"api_key\": \"sk-new\"}）；未传的字段 "
            "保持原值不变。"
        ),
    )


class ELLMCredentialPatchResponse(BaseModel):
    """部分更新后的凭证视图。"""

    credential_id: str = Field(description="凭证 id。")
    data: dict[str, Any] = Field(description="更新后的完整 payload data。")


@credential_model_router.patch(
    "/credential/{credential_id}",
    response_model=ELLMCredentialPatchResponse,
    summary="Partially update an ELLM credential",
    description=(
        "Merge only the fields sent in the request body into the stored "
        "credential payload — unpassed fields keep their current values "
        "(unlike the official ``PATCH /credential/{id}`` which replaces "
        "the whole payload). The merged result is re-validated as an "
        "``ELLMCredential`` (e.g. ``model`` must stay in candidates)."
    ),
)
async def patch_ellm_credential(
    credential_id: str,
    body: ELLMCredentialPatch,
    user_id: str = Depends(get_current_user_id),
    access: ResourceAccessService = Depends(get_resource_access_service),
    storage: StorageBase = Depends(get_storage),
) -> ELLMCredentialPatchResponse:
    """部分修改 ELLM 凭证：只覆盖前端传入的字段，其余保持原值。

    - ``resolve_credential`` 校验归属/共享，不可见 → 404；
    - 非 ``bocom_ellm_credential`` 类型 → 400；
    - 合并后整体重新校验（``model`` 必须仍在候选等），非法 → 422；
    - ``id``/``type`` 永远保持原值，不可被覆盖。
    """
    record = await access.resolve_credential(user_id, credential_id)

    existing = dict(record.data or {})
    if existing.get("type") != "bocom_ellm_credential":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Credential {credential_id!r} is type "
                f"{existing.get('type')!r}, not 'bocom_ellm_credential'."
            ),
        )

    # 只覆盖前端传入的字段；id/type 强制保持原值。
    merged = {**existing, **body.data}
    merged["id"] = existing.get("id") or credential_id
    merged["type"] = "bocom_ellm_credential"

    # 合并后整体校验（model 候选、必填字段等），非法 → 422。
    credential = CredentialFactory.from_dict(merged)
    credential.id = existing.get("id") or credential_id

    new_id = await storage.upsert_credential(user_id, credential)
    return ELLMCredentialPatchResponse(
        credential_id=new_id,
        data=_dump_credential_data(credential),
    )


def _dump_credential_data(credential: Any) -> dict[str, Any]:
    """序列化凭证为 payload data（SecretStr 解明文）。"""
    from agentscope.app.storage._utils import _dump_with_secrets

    return _dump_with_secrets(credential)
