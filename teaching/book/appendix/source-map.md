# 附录 C：源码地图

AgentScope 核心源文件导航索引。

---

## 消息

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `message/_message_base.py` | `Msg` | 消息类 |
| `message/_message_block.py` | `TextBlock`, `ToolUseBlock`, ... | 7 种内容块 |

## Agent

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `agent/_agent_base.py` | `AgentBase` | Agent 基类 |
| `agent/_agent_meta.py` | `_AgentMeta` | 元类 Hook |
| `agent/_react_agent_base.py` | `ReActAgentBase` | ReAct 抽象 |
| `agent/_react_agent.py` | `ReActAgent` | ReAct 实现 |

## Model

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `model/_model_base.py` | `ChatModelBase` | 模型基类 |
| `model/_openai_model.py` | `OpenAIChatModel` | OpenAI 实现 |
| `model/_model_response.py` | `ChatResponse` | 统一响应 |
| `model/_model_usage.py` | `ChatUsage` | Token 统计 |

## Formatter

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `formatter/_formatter_base.py` | `FormatterBase` | 格式化基类 |
| `formatter/_truncated_formatter_base.py` | `TruncatedFormatterBase` | 截断逻辑 |
| `formatter/_openai_formatter.py` | `OpenAIChatFormatter` | OpenAI 格式 |

## Tool

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `tool/_toolkit.py` | `Toolkit` | 工具管理 |
| `tool/_response.py` | `ToolResponse` | 工具结果 |
| `tool/_async_wrapper.py` | — | 同步/异步包装 |

## Memory

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `memory/_working_memory/_base.py` | `MemoryBase` | 记忆基类 |
| `memory/_working_memory/_in_memory_memory.py` | `InMemoryMemory` | 内存实现 |

## 配置

| 文件 | 类/函数 | 用途 |
|------|--------|------|
| `__init__.py` | `init()` | 框架初始化 |
| `_run_config.py` | `_ConfigCls` | 全局配置 |
