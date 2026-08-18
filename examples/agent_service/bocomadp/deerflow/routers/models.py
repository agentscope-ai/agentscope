# -*- coding: utf-8 -*-
"""deer-flow 模型列表端点（deer-flow Model 格式）。

``GET /api/deerflow/models`` 按用户名检索 credential id 并与 default 用户
默认凭证合并，返回 deer-flow 前端模型选择器期望的 ``{models: [...]}``
结构（对齐 ``deerflow/frontend/src/core/models/types.ts`` 的 Model）：

- 每个 config.yaml 模型条目先查用户维度凭证
  ``deerflow-<user_id>-<provider_id>`` —— **存在则用本用户的 credential
  id**（重复则使用本用户的，用户改过的 api_key 等生效）；
- 不存在则回退 default 用户默认凭证
  ``deerflow-default-<provider_id>``（lifespan 由
  :func:`ensure_default_credentials` 入库），该 id 作为「默认凭证 id」
  返回给前端；
- 默认凭证亦不存在（未入库/被删）→ 跳过该条目并告警。

返回的 credential id 与 ``_resolve_chat_model_config`` 实际引用的 id
同源（共用 :mod:`bocomadp.deerflow.credentials` 的命名约定）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from agentscope.app.deps import get_storage
from agentscope.app.storage import StorageBase

from bocomadp.config import load_models_from_yaml

from ..credentials import (
    DEFAULT_CREDENTIAL_OWNER,
    default_credential_id,
    user_credential_id,
)
from ..deps import get_deerflow_user_id

logger = logging.getLogger(__name__)

deerflow_models_router = APIRouter(
    prefix="/deerflow/models",
    tags=["deerflow-models"],
)

# 注意：本路由挂载在 main.py 的 /api 子应用下，对外路径为
# /api/deerflow/models；与 bocomadp 原生 /api/models（models_router）
# 前缀不同，无冲突。


@deerflow_models_router.get(
    "",
    summary="List deer-flow models (user credential merged with defaults)",
)
async def list_deerflow_models(
    user_id: str = Depends(get_deerflow_user_id),
    storage: StorageBase = Depends(get_storage),
) -> dict[str, Any]:
    """按用户名检索 credential id，合并默认凭证返回模型列表。

    返回 deer-flow ``Model`` 格式：``{id, name, model, display_name,
    supports_thinking, supports_reasoning_effort}``；``id`` 为
    credential id（用户自己的或 default 的），前端据此选择模型并在
    ``context.model_name`` 中传回 ``name``。
    """
    models: list[dict[str, Any]] = []
    for entry in load_models_from_yaml():
        provider_id = entry.provider_id
        own_id = user_credential_id(user_id, provider_id)
        own = await storage.get_credential(user_id, own_id)
        if own is not None:
            # 重复则使用本用户的 credential id
            credential_id = own.id
            logger.debug(
                "deerflow: model %r resolved to user credential %s.",
                provider_id,
                credential_id,
            )
        else:
            default_id = default_credential_id(provider_id)
            default_rec = await storage.get_credential(
                DEFAULT_CREDENTIAL_OWNER,
                default_id,
            )
            if default_rec is None:
                logger.warning(
                    "deerflow: default credential %s missing; model %r "
                    "skipped in list.",
                    default_id,
                    provider_id,
                )
                continue
            credential_id = default_id
        models.append(
            {
                "id": credential_id,
                "name": provider_id,
                "model": entry.model_name or provider_id,
                "display_name": (
                    entry.display_name
                    or entry.model_name
                    or provider_id
                ),
                "supports_thinking": entry.supports_thinking,
                "supports_reasoning_effort": False,
            },
        )
    return {"models": models}


__all__ = ["deerflow_models_router"]
