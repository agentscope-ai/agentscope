# -*- coding: utf-8 -*-
"""deerflow 模型凭证共享逻辑：默认凭证入库 + 凭证 id 约定。

deer-flow 前端的模型名需要映射到原生 ``ChatModelConfig`` 的
``credential_id``；本模块定义「默认凭证 + 用户维度凭证」的两级约定：

- **默认凭证**：config.yaml 的模型条目在启动时（lifespan）作为
  ``default`` 用户的凭证入库，id 形如 ``deerflow-default-<provider_id>``
  —— 默认模型参数（api_key/base_url）的单一来源，运行时可修改该记录
  实现全局切换，无需改 config.yaml。
- **用户维度凭证**：id 形如 ``deerflow-<user_id>-<provider_id>``；首次
  使用时从默认凭证复制参数入库（原生 ChatService 按 run 的 user_id
  解析 credential，owner-scoping 不允许跨用户引用 default 的 id），
  已存在时直接引用（重复则使用本用户的，用户改过的 key 生效）。

``_resolve_chat_model_config``（deerflow_chat.py）与
``/api/deerflow/models``（routers/models.py）共用本模块的 id 约定，
保证「列表返回的 credential id」与「run 实际引用的 credential id」同源。
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agentscope.app.storage import StorageBase
from agentscope.credential import CredentialFactory

from bocomadp.config import load_models_from_yaml

if TYPE_CHECKING:  # pragma: no cover —— 仅类型标注
    from bocomadp.config.app_config import ModelEntry

logger = logging.getLogger(__name__)

# 默认凭证归属 default 用户（与 DEERFLOW_DEFAULT_USER_ID 同值）。
DEFAULT_CREDENTIAL_OWNER = "default"

# 用户/默认凭证 id 前缀（与 deerflow_chat 原逻辑一致，保持兼容）。
_CREDENTIAL_PREFIX = "deerflow"


def default_credential_id(provider_id: str) -> str:
    """default 用户默认凭证 id。"""
    return f"{_CREDENTIAL_PREFIX}-{DEFAULT_CREDENTIAL_OWNER}-{provider_id}"


def user_credential_id(user_id: str, provider_id: str) -> str:
    """用户维度凭证 id（user 恰为 default 时与默认凭证 id 合一）。"""
    return f"{_CREDENTIAL_PREFIX}-{user_id}-{provider_id}"


def is_deerflow_credential_id(hint: str, user_id: str) -> str | None:
    """hint 为约定 credential id 时解析出 provider_id。

    识别 ``/api/deerflow/models`` 返回的两种 id 形态：

    - ``deerflow-<user_id>-<provider>`` → provider；
    - ``deerflow-default-<provider>`` → provider（user_id 恰为 default
      时与前一种同形，先匹配用户前缀即可）。

    其余形态（模型名 / 任意 uuid 等）返回 None。
    """
    hint = (hint or "").strip()
    if not hint:
        return None
    prefix = f"{_CREDENTIAL_PREFIX}-{user_id}-"
    if hint.startswith(prefix):
        provider = hint[len(prefix):]
        if provider:
            return provider
    default_prefix = f"{_CREDENTIAL_PREFIX}-{DEFAULT_CREDENTIAL_OWNER}-"
    if hint.startswith(default_prefix):
        provider = hint[len(default_prefix):]
        if provider:
            return provider
    return None


def credential_cls_for_entry(entry: "ModelEntry") -> type | None:
    """ModelEntry → credential 类（简写匹配失败补 _credential 后缀）。

    与 bocomadp.config.build_model_instance 同构：CredentialFactory 只
    注册全称类（如 ``deepseek_credential``），config.yaml 简写
    （``deepseek``）需补后缀再试。
    """
    credential_cls = CredentialFactory.get_credential_class(
        entry.provider_type,
    )
    if (
        credential_cls is None
        and not entry.provider_type.endswith("_credential")
    ):
        credential_cls = CredentialFactory.get_credential_class(
            f"{entry.provider_type}_credential",
        )
    return credential_cls


def credential_kwargs_for_entry(
    entry: "ModelEntry",
    credential_id: str,
    credential_cls: type,
    model: str | None = None,
) -> dict[str, Any]:
    """ModelEntry → credential 构造参数（api_key/base_url/model + 固定 id）。

    ``model`` 非空且凭证类声明了 model 字段时才写入（如 ELLMCredential
    的必填 model）；其余凭证类不受影响。
    """
    kwargs: dict[str, Any] = {
        "api_key": entry.api_key,
        "id": credential_id,
    }
    if entry.base_url and "base_url" in credential_cls.model_fields:
        kwargs["base_url"] = entry.base_url
    if model and "model" in credential_cls.model_fields:
        kwargs["model"] = model
    return kwargs


async def ensure_default_credentials(storage: StorageBase) -> None:
    """把 config.yaml 模型条目作为 default 用户的默认凭证入库。

    幂等（upsert）；单条目失败仅告警不阻断启动。入库后默认模型参数
    以 storage 中 default 凭证为单一来源，运行时可修改该记录实现全局
    切换，无需改 config.yaml。
    """
    for entry in load_models_from_yaml():
        try:
            credential_cls = credential_cls_for_entry(entry)
            if credential_cls is None:
                logger.warning(
                    "deerflow: unknown provider_type %r; default "
                    "credential for provider %r skipped.",
                    entry.provider_type,
                    entry.provider_id,
                )
                continue
            credential = credential_cls(
                **credential_kwargs_for_entry(
                    entry,
                    default_credential_id(entry.provider_id),
                    credential_cls,
                    model=entry.model_name or entry.provider_id,
                ),
            )
            await storage.upsert_credential(
                DEFAULT_CREDENTIAL_OWNER,
                credential,
            )
            logger.info(
                "deerflow: default credential %s ensured (provider=%s).",
                default_credential_id(entry.provider_id),
                entry.provider_id,
            )
        except Exception:  # noqa: BLE001 —— 单条目失败不阻断启动
            logger.warning(
                "deerflow: failed to ensure default credential for "
                "provider %r; skipped.",
                entry.provider_id,
                exc_info=True,
            )


__all__ = [
    "DEFAULT_CREDENTIAL_OWNER",
    "default_credential_id",
    "user_credential_id",
    "is_deerflow_credential_id",
    "credential_cls_for_entry",
    "credential_kwargs_for_entry",
    "ensure_default_credentials",
]
