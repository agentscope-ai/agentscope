# 调试指南

> **Level 8**: 能提交高质量 PR
> **前置要求**: [源码导航地图](./12-codebase-navigation.md)
> **后续章节**: [架构决策记录](./12-architecture-decisions.md)

---

## 学习目标

学完本章后，你能：
- 掌握 AgentScope 的调试工具和技巧
- 学会使用日志定位问题
- 理解常见错误的排查方法
- 知道如何编写可调试的 Agent 代码

---

## 背景问题

Agent 系统的调试比传统应用复杂得多：LLM 调用是不确定的、工具执行是异步的、MsgHub 广播是并发的。简单的 `print()` 不足以追踪跨越 Agent→Formatter→Model→Toolkit 的消息流。需要一套系统化方法。

---

## 源码入口

| 项目 | 值 |
|------|-----|
| **日志系统** | `src/agentscope/_logging.py` (日志配置) |
| **Tracing** | `src/agentscope/tracing/_trace.py` (OpenTelemetry spans) |
| **Agent 调试入口** | `src/agentscope/agent/_react_agent.py:376` (reply 循环) |

---

## 日志系统

### 初始化日志

```python
import agentscope

agentscope.init(
    project="my-project",
    name="debug-session",
    logging_level="DEBUG",  # 关键：开启 DEBUG 级别
)
```

### 日志级别

| 级别 | 用途 | 何时使用 |
|------|------|---------|
| `DEBUG` | 详细调试信息 | 开发调试 |
| `INFO` | 一般信息 | 正常运行 |
| `WARNING` | 警告 | 潜在问题 |
| `ERROR` | 错误 | 出错时 |

---

## 常见问题排查

### 问题 1：Agent 不调用工具

```python
# DEBUG 日志检查
import logging
logging.getLogger("agentscope").setLevel("DEBUG")

# 预期输出：
# DEBUG - Toolkit: get_json_schemas() = [...]
# DEBUG - Calling LLM with tools: [...]
# DEBUG - LLM returned: ToolUseBlock(...)
```

### 问题 2：消息丢失

```python
# 检查 memory 状态
print(f"Memory size: {len(agent.memory.get_memory())}")
print(f"Memory content: {agent.memory.get_memory()}")
```

### 问题 3：工具执行失败

```python
# 直接调用工具调试
result = await toolkit.call_tool_function({
    "type": "tool_use",
    "id": "test",
    "name": "my_tool",
    "input": {"arg1": "value1"},
})
print(f"Result: {result}")
```

---

## 工具执行追踪

### trace_reply 装饰器

**源码**: `src/agentscope/agent/_react_agent.py:375`

```python
@trace_reply
async def reply(self, msg: Msg) -> Msg:
    """核心回复方法"""
```

启用追踪：
```python
agentscope.init(trace_enabled=True, studio_url="...")
```

### 异步调试技巧

```python
# 使用 traceback 打印完整堆栈
try:
    await agent(msg)
except Exception:
    import traceback
    traceback.print_exc()
```

---

## 常见错误码

| 错误码 | 含义 | 解决方案 |
|--------|------|---------|
| `401` | API Key 无效 | 检查 API Key 配置 |
| `429` | 请求频率超限 | 添加重试延迟 |
| `500` | 服务器内部错误 | 检查模型服务状态 |

---

## 调试最佳实践

### 1. 使用单元测试

```python
import pytest

@pytest.mark.asyncio
async def test_toolkit_registration():
    toolkit = Toolkit()
    toolkit.register_tool_function(my_func)
    assert len(toolkit.tools) == 1
```

### 2. Mock 外部依赖

```python
from unittest.mock import AsyncMock, patch

@patch("agentscope.model.OpenAIChatModel")
async def test_agent_with_mock_model(mock_model):
    mock_model.return_value = AsyncMock(return_value=mock_response)
    agent = ReActAgent(model=mock_model)
    result = await agent(msg)
    assert result.content == expected
```

### 3. 隔离测试

```bash
# 使用 --forked 隔离测试
pytest tests/ --forked
```

---

## 下一步

接下来学习 [架构决策记录](./12-architecture-decisions.md)。


---

## 工程现实与架构问题

### 技术债 (源码级)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| `tracing/` | trace_reply 装饰器有副作用 | 嵌套调用时 trace 碎片化 | 中 |
| `agentscope.init()` | 全局状态难以 Mock | 单元测试需要特殊处理 | 高 |
| `logging_level` | 默认 INFO 级别 | 调试信息被截断 | 低 |
| `trace_enabled=True` | Studio 连接失败无提示 | 用户不知道 tracing 未生效 | 中 |

**[HISTORICAL INFERENCE]**: tracing 系统是后期添加的功能，装饰器设计未考虑嵌套场景。全局 init 状态是早期设计决策，测试友好性与运行时便利性之间做了权衡。

### 性能考量

```python
# DEBUG 日志开销估算
每次 log.debug() 调用: ~0.1-0.5ms
完整 trace 单次 agent.reply(): ~5-20ms

# 追踪系统开销
trace_enabled=True 时: 额外 ~10-50ms/请求
Studio 连接不稳定时: 可能阻塞主流程
```

### 渐进式重构方案

```python
# 方案 1: 无副作用的 trace 装饰器
from functools import wraps
from contextvars import ContextVar

_trace_context: ContextVar[list[dict]] = ContextVar("trace_context")

def trace_reply_v2(func):
    @wraps(func)
    async def wrapper(self, msg, *args, **kwargs):
        # 不修改原始 msg 对象
        local_trace = []
        _trace_context.set(local_trace)

        try:
            result = await func(self, msg, *args, **kwargs)
            return result
        finally:
            # 上报 trace，但不修改返回值
            if self._trace_enabled:
                await self._upload_trace(local_trace)

    return wrapper

# 方案 2: 可测试的 init
class DebugContext:
    """可配置的调试上下文，替代全局 init"""
    def __init__(self, trace_enabled: bool = False, logging_level: str = "INFO"):
        self.trace_enabled = trace_enabled
        self.logging_level = logging_level
        self._token = None

    def __enter__(self):
        self._token = _debug_context.set(self)
        return self

    def __exit__(self, *args):
        _debug_context.reset(self._token)

    @staticmethod
    def get() -> "DebugContext | None":
        return _debug_context.get(None)

# 使用方式
async def test_agent():
    with DebugContext(trace_enabled=True, logging_level="DEBUG"):
        agent = ReActAgent(...)
        result = await agent(msg)
        # 全局状态干净，不影响其他测试
```

