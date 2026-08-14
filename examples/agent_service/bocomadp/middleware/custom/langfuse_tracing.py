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
3. monkey-patch 内置 extractor：AgentScope 的
   ``_get_llm_request_attributes`` 只提取生成参数与工具定义，忽略
   ``input_kwargs["messages"]``（含系统提示词 / 历史对话），导致
   Langfuse 中 GENERATION 的 Input 缺失。补丁为其补上
   ``gen_ai.input.messages`` 属性（Langfuse OTLP 映射为 input）。
4. 导出模块级 ``TracingMiddleware`` 实例，被 registry 扫描后自动注入
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


def _patch_llm_request_extractor() -> None:
    """给 TracingMiddleware 的属性提取器补上输入 messages 与系统提示词。

    AgentScope 内置 ``_get_llm_request_attributes`` 不读取
    ``input_kwargs["messages"]``，导致 Langfuse 中 GENERATION 的
    Input 只有工具定义、缺少系统提示词与对话上下文；
    ``_get_agent_request_attributes`` 只记录用户输入，AGENT（trace
    根）的 Input 同样缺系统提示词。这里通过 monkey-patch 注入
    ``gen_ai.input.messages``（实测 Langfuse OTLP 会将其映射为
    observation input），零框架源码改动。

    注意：TracingMiddleware（``_trace`` 模块）通过
    ``from ._extractor import ...`` 在导入时绑定函数引用，只替换
    ``_extractor`` 命名空间不会影响其调用，因此两个命名空间都要
    替换。

    幂等：模块被重复导入（如 worker fork）时靠标记位跳过二次包装。
    """
    try:
        from agentscope.middleware._tracing import _extractor, _trace
        from agentscope.middleware._tracing._attributes import (
            SpanAttributes,
        )
        from agentscope.middleware._tracing._extractor import (
            _get_agent_messages,
        )
        from agentscope.middleware._tracing._utils import _serialize_to_str
        from agentscope.message import Msg
    except ImportError:
        _logger.warning(
            "langfuse tracing: cannot patch request extractors",
            exc_info=True,
        )
        return

    # ---- LLM 层：GENERATION input 带上完整 messages（含 SystemMsg） ----
    _llm_original = _trace._get_llm_request_attributes
    if not getattr(_llm_original, "_langfuse_patched", False):

        def _patched_llm(instance, kwargs):
            attributes = _llm_original(instance, kwargs)
            messages = kwargs.get("messages")
            if messages:
                attributes[SpanAttributes.GEN_AI_INPUT_MESSAGES] = (
                    _serialize_to_str(_get_agent_messages(messages))
                )
            return attributes

        _patched_llm._langfuse_patched = True  # type: ignore[attr-defined]
        _trace._get_llm_request_attributes = _patched_llm
        _extractor._get_llm_request_attributes = _patched_llm
        _logger.info("langfuse tracing: llm request extractor patched")

    # ---- AGENT 层：trace 根 input 前插系统提示词 ----
    _agent_original = _trace._get_agent_request_attributes
    if not getattr(_agent_original, "_langfuse_patched", False):

        def _patched_agent(instance, kwargs):
            attributes = _agent_original(instance, kwargs)
            inputs = kwargs.get("inputs")
            if isinstance(inputs, (Msg, list)):
                sys_prompt = getattr(instance, "_system_prompt", None)
                if sys_prompt:
                    system_msg = {
                        "role": "system",
                        "parts": [{"type": "text", "content": sys_prompt}],
                        "name": "system",
                        "finish_reason": "stop",
                    }
                    attributes[SpanAttributes.GEN_AI_INPUT_MESSAGES] = (
                        _serialize_to_str(
                            [system_msg] + _get_agent_messages(inputs)
                        )
                    )
            return attributes

        _patched_agent._langfuse_patched = True  # type: ignore[attr-defined]
        _trace._get_agent_request_attributes = _patched_agent
        _extractor._get_agent_request_attributes = _patched_agent
        _logger.info("langfuse tracing: agent request extractor patched")


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
if setup_langfuse_tracing():
    # tracing 启用后才打补丁：未配置凭据时保持近零开销、零侵入
    _patch_llm_request_extractor()

langfuse_tracing_mw = TracingMiddleware()
