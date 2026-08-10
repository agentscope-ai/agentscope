# -*- coding: utf-8 -*-
"""事件日志中间件 —— 记录模型调用与工具调用的输入/输出到 ``events.log``。

通过 ``MiddlewareRegistry.load_custom()`` 自动发现并注册（``custom/``
下任何模块级 ``_is_agent_middleware`` 实例都会被扫描）。本中间件无状态，
``session_id`` / ``reply_id`` 通过 ``agent.state`` 自动读取。

支持钩子：

- ``on_model_call`` —— 记录 ``MODEL_INPUT`` / ``MODEL_OUTPUT``
- ``on_acting`` —— 记录 ``TOOL_INPUT`` / ``TOOL_OUTPUT``

输出通道：``logging.getLogger("as")``，复用 ``main.py`` 的 ``setup_logger``
配置，自动落到 ``/app/logs/events.log``。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Awaitable, Callable

from agentscope.middleware import MiddlewareBase

# 显式打标记：即便模块加载顺序让 MiddlewareBase._is_agent_middleware
# 还没被 agent_middleware.py 设置，本中间件也能被扫描器识别。
MiddlewareBase._is_agent_middleware = True  # type: ignore[attr-defined]

_logger = logging.getLogger("as")

# 单条日志字段的最大长度（避免超长消息把日志刷爆）
_MAX_CONTENT = 2000
_MAX_OUTPUT = 2000


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _session_id(agent: Any) -> str:
    return getattr(getattr(agent, "state", None), "session_id", "-")


def _reply_id(agent: Any) -> str:
    """``AgentState.reply_id`` 是 property，本质读 reply_context.reply_id。"""
    state = getattr(agent, "state", None)
    if state is None:
        return "-"
    # 优先用 property，缺失时回退 reply_context.reply_id
    return getattr(state, "reply_id", "-") or "-"


def _block_text(block: Any) -> str:
    """从 content block 提取可读文本（TextBlock / ThinkingBlock / str）。"""
    if isinstance(block, str):
        return block
    return getattr(block, "text", "") or getattr(block, "thinking", "")


def _truncate(text: str, limit: int) -> str:
    if len(text) > limit:
        return text[:limit] + f"<truncated {len(text) - limit} chars>"
    return text


def _format_messages(messages: Any) -> str:
    """把 messages 列表格式化成单行可读文本。"""
    parts: list[str] = []
    for msg in messages or []:
        role = getattr(msg, "role", "?")
        name = getattr(msg, "name", "?")
        content = getattr(msg, "content", [])
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " | ".join(_block_text(b) for b in content)
        else:
            text = _block_text(content)
        parts.append(f"[{role}:{name}] {_truncate(text, _MAX_CONTENT)}")
    return " || ".join(parts) or "-"


def _response_text(content: Any) -> str:
    """从 ChatResponse.content 提取完整文本（多个 block 拼接）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_block_text(b) for b in content)
    return _block_text(content)


def _emit_model_output(
    session_id: str,
    reply_id: str,
    model_name: str,
    text: str,
    usage: Any,
) -> None:
    in_tok = getattr(usage, "input_tokens", "-") if usage else "-"
    out_tok = getattr(usage, "output_tokens", "-") if usage else "-"
    _logger.info(
        "MODEL_OUTPUT session_id=%s reply_id=%s model=%s "
        "output=%s input_tokens=%s output_tokens=%s",
        session_id,
        reply_id,
        model_name,
        _truncate(text or "-", _MAX_OUTPUT),
        in_tok,
        out_tok,
    )


def _tool_content_text(content: Any) -> str:
    """从 ``ToolResponse.content`` / ``ToolChunk.content`` 提取可读文本。

    ``content`` 可能是：
    - ``str`` —— 直接返回
    - ``List[TextBlock | DataBlock]`` —— 逐 block 取 ``text`` 字段；
      ``DataBlock`` 没有 ``text``，用 ``str(block)`` 兜底
    - 其他 —— 走 ``_block_text`` 兜底
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            else:
                txt = getattr(block, "text", None)
                parts.append(txt if txt else str(block))
        return " | ".join(parts)
    return _block_text(content)


def _emit_tool_output(
    session_id: str,
    reply_id: str,
    tool_call_id: str,
    state: Any,
    output_text: str,
) -> None:
    """记录工具执行结果（state 取自 ``ToolResponse.state``）。"""
    _logger.info(
        "TOOL_OUTPUT session_id=%s reply_id=%s tool_call_id=%s "
        "state=%s output=%s",
        session_id,
        reply_id,
        tool_call_id,
        state,
        _truncate(output_text or "-", _MAX_OUTPUT),
    )


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------
class EventLogMiddleware(MiddlewareBase):
    """事件日志中间件 —— 订阅 ``on_model_call`` / ``on_acting`` 钩子。

    设计要点：

    - 用 ``on_model_call`` 而非 ``on_reply`` 事件流，因为 model_call 直接
      拿到 ``ChatResponse``（含完整 content 与 usage），不需要处理流式
      chunk 的累积。
    - ``on_model_call`` 是 **async 函数**，**return** 一个
      ``ChatResponse`` 或 ``AsyncGenerator[ChatResponse]`` —— **不能**
      让函数本身成为 async generator（即不能用 yield）。AgentScope
      框架在 ``_agent.py`` 用 ``await mw.on_model_call(...)`` 接收
      返回值，再判断是 async generator 还是 ChatResponse 进一步处理。
    - 流式响应场景：``next_handler()`` 返回 AsyncGenerator。我们用
      一个**内嵌 async generator wrapper** 透传每个 chunk，同时累积
      text/usage，wrapper 消费完毕后再 ``_emit_model_output``。
    - ``on_acting`` 与 ``on_model_call`` 不同：**必须是 async generator**
      （函数体 yield），因为 AgentScope 框架在 ``_agent.py`` 用
      ``async for item in mw.on_acting(...)`` 消费。
    - token 来自 ``ChatResponse.usage``（最后一次 chunk 的 usage 覆盖
      前面的，符合 AgentScope 的 ``append_chat_response`` 语义）。
    - 工具输出基于 ``next_handler()`` 最后产出的 item 提取
      ``state`` / ``content``，符合 ``_acting_impl`` 的
      ``ToolChunk → ToolResponse`` 序列约定。
    """

    async def on_model_call(
        self,
        agent: "Any",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[Any]],
    ):
        """``on_model_call`` 钩子：记录输入 + 透传 + 记录输出。"""
        # 1. 记录模型输入
        messages = input_kwargs.get("messages") or []
        current_model = input_kwargs.get("current_model")
        # ChatModelBase 的模型名字段是 ``model``（见
        # src/agentscope/model/_base.py:46），不是 ``model_name``。
        # 用 fallback 链兼容不同子类可能扩展的属性。
        model_name = (
            getattr(current_model, "model", None)
            or getattr(current_model, "model_name", None)
            or getattr(current_model, "name", None)
            or "-"
        )
        session_id = _session_id(agent)
        reply_id = _reply_id(agent)

        _logger.info(
            "MODEL_INPUT session_id=%s reply_id=%s model=%s messages=%s",
            session_id,
            reply_id,
            model_name,
            _format_messages(messages),
        )

        # 2. 调用下一个中间件 / 真实模型调用
        result = await next_handler()

        # 3. 流式 vs 非流式分发 —— 注意：函数本身不能 yield！
        if hasattr(result, "__aiter__"):
            # 流式：用内嵌 async generator 透传 + 累积
            # 上层会拿到这个 generator 并 async for 消费，消费完后才会
            # 执行 _emit_model_output。
            async def _wrapped() -> AsyncGenerator[Any, None]:
                full_text = ""
                full_usage = None
                async for chunk in result:
                    full_text += _response_text(getattr(chunk, "content", []))
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        full_usage = chunk_usage
                    yield chunk
                _emit_model_output(
                    session_id,
                    reply_id,
                    model_name,
                    full_text,
                    full_usage,
                )
            return _wrapped()
        else:
            # 非流式：直接拿到 ChatResponse，记录后透传
            text = _response_text(getattr(result, "content", []))
            _emit_model_output(
                session_id,
                reply_id,
                model_name,
                text,
                getattr(result, "usage", None),
            )
            return result

    async def on_acting(
        self,
        agent: "Any",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """``on_acting`` 钩子：记录工具输入 + 透传 ToolChunk/ToolResponse + 记录输出。

        重要：``on_acting`` 与 ``on_model_call`` 的契约不同 —— 框架用
        ``async for item in mw.on_acting(...)`` 消费结果，因此本函数
        **必须**是 async generator（函数体内 yield），不能 return。
        """
        # 1. 解析 tool_call 元信息
        tool_call = input_kwargs.get("tool_call")
        session_id = _session_id(agent)
        reply_id = _reply_id(agent)
        tool_call_id = getattr(tool_call, "id", "-")
        tool_name = getattr(tool_call, "name", "-")
        # tool_call.input 已经是 JSON 字符串（见 _extractor.py:553-555），
        # 直接截断使用，避免重复 json.dumps。
        arguments = getattr(tool_call, "input", "-") or "-"

        _logger.info(
            "TOOL_INPUT session_id=%s reply_id=%s tool_call_id=%s "
            "tool_name=%s arguments=%s",
            session_id,
            reply_id,
            tool_call_id,
            tool_name,
            _truncate(arguments, _MAX_CONTENT),
        )

        # 2. 透传 ToolChunk / ToolResponse，同时追踪最后一个 item
        last_item: Any = None
        try:
            async for item in next_handler(**input_kwargs):
                last_item = item
                yield item
        finally:
            # 3. 记录工具输出（无论成功 / 异常都打日志）
            # next_handler 的最后一个 item 一定是 ToolResponse（见
            # _acting_impl 的实现）；若 ToolResponse 缺失则跳过。
            state = getattr(last_item, "state", "-") if last_item else "-"
            content = getattr(last_item, "content", None) if last_item else None
            output_text = _tool_content_text(content) if content is not None else "-"
            _emit_tool_output(
                session_id,
                reply_id,
                tool_call_id,
                state,
                output_text,
            )


# ---------------------------------------------------------------------------
# 模块级实例 —— MiddlewareRegistry.load_custom() 会扫描并自动注册
# ---------------------------------------------------------------------------
event_log_mw = EventLogMiddleware()