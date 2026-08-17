"""DeerFlow 渠道兼容占位路由单测（routers/channels.py）。

覆盖：两个端点返回 200 且契约与 deer-flow 前端一致——providers 恒为空、
enabled 恒为 false、connections 恒为空（模仿 deer-flow 未配置渠道时的
响应，前端渲染"渠道已禁用"而非报错）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bocomadp.routers.channels import channels_router


def _make_app() -> FastAPI:
    # 与 main.py 一致：router 挂到子应用，再 mount 到 /api（对外路径不变）
    api = FastAPI()
    api.include_router(channels_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def test_providers_returns_disabled_empty_contract() -> None:
    with TestClient(_make_app()) as client:
        response = client.get("/api/deerflow/channels/providers")
    assert response.status_code == 200
    body = response.json()
    assert body == {"enabled": False, "providers": []}


def test_connections_returns_empty_contract() -> None:
    with TestClient(_make_app()) as client:
        response = client.get("/api/deerflow/channels/connections")
    assert response.status_code == 200
    assert response.json() == {"connections": []}
