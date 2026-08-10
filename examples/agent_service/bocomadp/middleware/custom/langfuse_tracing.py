# -*- coding: utf-8 -*-
"""Langfuse 可观测性中间件 —— 复用 AgentScope 内置 TracingMiddleware。

在 ``custom/`` 包被 ``MiddlewareRegistry.load_custom()`` 扫描导入时
（进程启动、Agent 创建之前）完成一次性 OpenTelemetry 装配：

1. 读取环境变量（与 Langfuse 官方 OTLP 配置一致）：

   - ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` —— Project 凭据
   - ``LANGFUSE_OTLP_ENDPOINT`` —— OTLP HTTP 端点；未设置时由
     ``LANGFUSE_BASE_URL`` 推导（``<base>/api/public/otel/v1/traces``）

2. 装配 ``TracerProvider`` + ``OTLPSpanExporter``（Basic Auth）+
   ``BatchSpanProcessor``，并设为全局 TracerProvider。
3. 导出模块级 ``TracingMiddleware`` 实例，被 registry 扫描后自动注入
   所有 Agent，将模型调用 / 工具调用 / 回复链路以 OTel GenAI 语义
   上报 Langfuse。

未配置凭据（或装配失败）时不做任何设置：``TracingMiddleware`` 内部
检测到 no-op provider 会自动短路，近零开销，服务照常运行。
"""
from __future__ import annotations

import base64
import logging
import os

from agentscope.middleware import TracingMiddleware

# 显式打标记：与 event_log.py 一致，确保扫描器识别。
TracingMiddleware._is_agent_middleware = True  # type: ignore[attr-defined]

_logger = logging.getLogger("as")

_OTLP_PATH = "/api/public/otel/v1/traces"


def _derive_otlp_endpoint() -> str | None:
    """从 LANGFUSE_BASE_URL 推导 OTLP 端点；容器内把 localhost 换成宿主机。"""
    base_url = os.environ.get("LANGFUSE_BASE_URL")
    if not base_url:
        return None
    # 容器内访问宿主机上的 langfuse：Docker Desktop 提供 host.docker.internal
    if os.path.exists("/.dockerenv"):
        base_url = (
            base_url.replace("://localhost:", "://host.docker.internal:")
            .replace("://127.0.0.1:", "://host.docker.internal:")
        )
    return base_url.rstrip("/") + _OTLP_PATH


def setup_langfuse_tracing() -> bool:
    """装配 OTel SDK 并指向 Langfuse；返回是否成功启用。"""
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    endpoint = os.environ.get("LANGFUSE_OTLP_ENDPOINT") or _derive_otlp_endpoint()
    if not (pk and sk and endpoint):
        _logger.info(
            "langfuse tracing disabled: missing LANGFUSE_PUBLIC_KEY / "
            "LANGFUSE_SECRET_KEY / LANGFUSE_OTLP_ENDPOINT"
        )
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        _logger.warning(
            "langfuse tracing disabled: opentelemetry SDK not installed",
            exc_info=True,
        )
        return False

    try:
        # Langfuse OTLP 端点要求 Basic Auth：base64(public_key:secret_key)
        token = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: "agentscope-service"}),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    headers={"Authorization": f"Basic {token}"},
                ),
            ),
        )
        trace.set_tracer_provider(provider)
        _logger.info(
            "langfuse tracing enabled: endpoint=%s", endpoint
        )
        return True
    except Exception:
        _logger.warning(
            "langfuse tracing setup failed, fallback to no-op",
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# 模块级实例 —— MiddlewareRegistry.load_custom() 会扫描并自动注册
# ---------------------------------------------------------------------------
setup_langfuse_tracing()

langfuse_tracing_mw = TracingMiddleware()
