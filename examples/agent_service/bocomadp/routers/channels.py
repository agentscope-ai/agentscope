# -*- coding: utf-8 -*-
"""DeerFlow 渠道连接兼容路由（占位实现）。

bocomadp 未实现 IM 渠道（telegram/slack/discord/feishu/dingtalk/wechat/wecom）
能力，本模块仅提供与 deer-flow 2.0 前端契约一致的只读占位端点，行为完全
模仿 deer-flow 在 ``channel_connections.enabled = false``（默认未配置）时的
响应：返回 200 与空列表，前端据此渲染"渠道功能已禁用"提示而非报错。

契约对齐 deer-flow ``backend/app/gateway/routers/channel_connections.py``：

- ``GET /api/deerflow/channels/providers``   → ``{enabled: bool, providers: [...]}``
- ``GET /api/deerflow/channels/connections`` → ``{connections: [...]}``

前端消费方（frontend/src/core/channels/hooks.ts）：``useChannelProviders``
与 ``useChannelConnections`` 无条件并行请求两个端点，任一 404 都会触发
渠道区域的错误态（unavailable），因此两个端点必须同时提供。

未来接入真实渠道时：将 ``enabled`` 置为 True 并填充 providers/connections
数据即可，响应模型字段契约无需变动。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

channels_router = APIRouter(prefix="/deerflow/channels", tags=["deerflow-channels"])

# 注意：本路由挂载在 main.py 的 /api 子应用下，对外路径为
# /api/deerflow/channels/...；deer-flow 前端旧路径 /api/channels/... 由
# nginx 网关 rewrite 兼容。


# ── 响应模型（字段契约与 deer-flow 完全对齐）─────────────────────────


class ChannelCredentialFieldResponse(BaseModel):
    """渠道凭据字段元数据（前端表单渲染用）。"""

    name: str
    label: str
    type: str = "text"
    required: bool = True


class ChannelProviderResponse(BaseModel):
    """单个渠道提供方的状态描述。"""

    provider: str
    display_name: str
    enabled: bool
    configured: bool
    connectable: bool
    unavailable_reason: str | None = None
    auth_mode: str
    connection_status: str
    credential_fields: list[ChannelCredentialFieldResponse] = Field(default_factory=list)
    credential_values: dict[str, str] = Field(default_factory=dict)


class ChannelProvidersResponse(BaseModel):
    """渠道提供方列表响应。"""

    enabled: bool
    providers: list[ChannelProviderResponse]


class ChannelConnectionResponse(BaseModel):
    """用户在某渠道上的绑定连接记录。"""

    id: str
    provider: str
    status: str
    external_account_id: str | None = None
    external_account_name: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ChannelConnectionsResponse(BaseModel):
    """用户渠道连接列表响应。"""

    connections: list[ChannelConnectionResponse]


# ── 端点 ─────────────────────────────────────────────────────────────


@channels_router.get(
    "/providers",
    response_model=ChannelProvidersResponse,
    summary="List Channel Providers",
    description=(
        "占位实现：恒返回渠道功能禁用（enabled=false）与空列表，"
        "对齐 deer-flow 未配置渠道时的响应，供前端优雅降级。"
    ),
)
async def list_channel_providers() -> ChannelProvidersResponse:
    """返回渠道提供方列表（bocomadp 未接入渠道，恒为空）。"""
    return ChannelProvidersResponse(enabled=False, providers=[])


@channels_router.get(
    "/connections",
    response_model=ChannelConnectionsResponse,
    summary="List Channel Connections",
    description=(
        "占位实现：恒返回空连接列表，对齐 deer-flow 未配置渠道时的响应，"
        "供前端优雅降级。"
    ),
)
async def list_channel_connections() -> ChannelConnectionsResponse:
    """返回当前用户的渠道绑定连接（bocomadp 未接入渠道，恒为空）。"""
    return ChannelConnectionsResponse(connections=[])
