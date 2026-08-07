# -*- coding: utf-8 -*-
"""按凭证查询可用模型（含"凭证绑定单模型"过滤）。

类似官方 ``GET /model/?provider=...``，但额外传 ``credential_id``：

- 凭证带 ``model`` 字段（B 方案：一凭证一模型）→ **只返回该模型**；
- 凭证没有 ``model`` 字段 → 返回该类型全部候选（``_models/*.yaml``）。

用法::

    GET /model/credential?credential_id=<id>&user_id=zy
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from agentscope.app._router._schema import ListModelsResponse
from agentscope.app._service import ResourceAccessService
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
)
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
