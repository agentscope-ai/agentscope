# -*- coding: utf-8 -*-
"""ELLM 供应商凭证。

自研/自部署的 ELLM 平台（模型由 **DeepSeek-V4-Flash** 提供），
对外暴露 OpenAI 兼容端点（``/v1``），因此：

- ``base_url`` 填 ELLM 部署的 OpenAI 兼容地址（如
  ``http://host.docker.internal:8001/v1``）；
- 聊天模型直接复用官方的 :class:`OpenAIChatModel`（``get_chat_model_class``）；
- 配置 agent/会话时 ``chat_model_config.model`` 填 ``deepseek-v4-flash``。
"""
from __future__ import annotations

from typing import Any, Literal, Self, Type

from pydantic import ConfigDict, Field, SecretStr, model_validator

from agentscope.credential import CredentialBase
from agentscope.model import ChatModelBase, OpenAIChatModel, EllmChatModel


# class ELLMChatModel(OpenAIChatModel):
#     """ELLM 的聊天模型——继承 OpenAI 兼容实现。

#     基类 :meth:`list_models` 会读取**本类源文件旁**的 ``_models/``
#     目录（即 ``bocomadp/credential/_models/*.yaml``），因此前端候选
#     列表只有 ELLM 自己的模型（deepseek-v4-flash），而不是官方
#     OpenAI 的 gpt-* 候选。
#     """


class ELLMCredential(CredentialBase):
    """ELLM 自研供应商凭证。"""

    model_config = ConfigDict(
        title="ELLM",
    )

    type: Literal["ellm_credential"] = "ellm_credential"
    """凭证类型标识（唯一，Pydantic discriminator 使用）。"""

    api_key: SecretStr = Field(
        description="ELLM 服务的 API key（本地部署通常不校验，任意值即可）。",
    )
    """API key。"""

    base_url: str | None = Field(
        default=None,
        description=(
            "ELLM 的 OpenAI 兼容端点（以 /v1 结尾）。容器内访问宿主机服务 "
            "用 host.docker.internal。"
        ),
    )
    """自定义 base URL（OpenAI 兼容端点）。"""

    organization: str | None = Field(
        default=None,
        description=(
            "组织 ID——OpenAIChatModel 构造时会读取该字段，必须存在。"
        ),
    )
    """组织 ID（官方 OpenAIChatModel 需要读取的字段）。"""

    scene_code: str | None = Field(
        default=None,
        description="场景编码（业务字段，前端传入，原样存储）。",
    )
    """场景编码。"""

    api_key_url: str | None = Field(
        default=None,
        description="API key 地址（业务字段，前端传入，原样存储）。",
    )
    """API key 地址。"""

    apikey_expires_at: str | None = Field(
        default=None,
        description="API key 过期时间（业务字段，前端可传空，原样存储）。",
    )
    """API key 过期时间。"""

    model: str = Field(
        description="绑定的模型名——必须来自 _models/ 候选（B 方案：一凭证一模型）。",
    )
    """绑定的模型名（必填，候选见 ``bocomadp/credential/_models/*.yaml``）。"""

    @model_validator(mode="after")
    def _validate_model(self) -> Self:
        """校验 model 必须在 _models/ 候选列表中。"""
        candidates = {
            card.name for card in self.get_chat_model_class().list_models()
        }
        if self.model not in candidates:
            raise ValueError(
                f"model {self.model!r} 不在候选列表中: {sorted(candidates)}",
            )
        return self

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """动态注入 model 字段的 enum（候选来自 _models/），前端表单变下拉。"""
        schema = super().model_json_schema(*args, **kwargs)
        candidates = [
            card.name for card in cls.get_chat_model_class().list_models()
        ]
        model_prop = schema.get("properties", {}).get("model")
        if model_prop is not None:
            model_prop["enum"] = candidates
        return schema

    @classmethod
    def get_chat_model_class(cls) -> Type[ChatModelBase]:
        """ELLM 是 OpenAI 兼容接口——基于 :class:`OpenAIChatModel` 的子类。

        子类化使 :meth:`list_models` 读取本包的 ``_models/*.yaml``
        候选卡（而不是官方 OpenAI 的候选）。
        """
        return ELLMChatModel
