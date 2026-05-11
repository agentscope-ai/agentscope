# 附录 C：源码地图

全书引用的所有源码文件的索引，按模块分组。

---

## 顶层文件

| 文件 | 行数 | 主要内容 |
|------|------|---------|
| `src/agentscope/__init__.py` | ~160 | 包初始化、子模块导入、`__all__` |
| `src/agentscope/_run_config.py` | ~80 | 全局配置（ContextVar） |
| `src/agentscope/_logging.py` | ~60 | 日志配置 |
| `src/agentscope/_version.py` | ~5 | 版本号 |

---

## message/ — 消息类型

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_message_base.py` | ~120 | `Msg` 类 | ch04 |
| `_message_block.py` | ~100 | 7 种 `ContentBlock` TypedDict | ch04 |

---

## module/ — 基础模块

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_state_module.py` | ~120 | `StateModule`（序列化） | ch14 |

---

## memory/ — 记忆系统

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_working_memory/_base.py` | ~170 | `MemoryBase` 抽象接口 | ch06 |
| `_working_memory/_in_memory_memory.py` | ~300 | `InMemoryMemory` | ch06 |
| `_long_term_memory/_long_term_memory_base.py` | ~200 | `LongTermMemoryBase` | ch07 |

---

## model/ — 模型适配

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_model_base.py` | ~100 | `ChatModelBase` 抽象接口 | ch09 |
| `_openai_model.py` | ~500 | `OpenAIChatModel` | ch09 |
| `_model_response.py` | ~100 | `ChatResponse` | ch09 |
| `_model_usage.py` | ~50 | `ChatUsage` | ch09 |

---

## formatter/ — 格式转换

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_formatter_base.py` | ~130 | `FormatterBase` 抽象接口 | ch08, ch16 |
| `_truncated_formatter_base.py` | ~100 | 带截断的模板方法 | ch08, ch16 |
| `_openai_formatter.py` | ~400 | `OpenAIChatFormatter` | ch08 |
| `_anthropic_formatter.py` | ~400 | `AnthropicChatFormatter` | ch16 |

---

## tool/ — 工具系统

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_toolkit.py` | ~1680 | `Toolkit` 类 | ch10, ch17, ch18 |
| `_response.py` | ~30 | `ToolResponse` | ch10 |
| `_types.py` | ~160 | `RegisteredToolFunction`, `ToolGroup` | ch10, ch17 |

---

## agent/ — Agent 实现

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_agent_base.py` | ~500 | `AgentBase` | ch05, ch14 |
| `_agent_meta.py` | ~170 | `_AgentMeta` 元类 + `_wrap_with_hooks` | ch15 |
| `_react_agent_base.py` | ~120 | `ReActAgentBase` 抽象 | ch14 |
| `_react_agent.py` | ~1100 | `ReActAgent` 完整实现 | ch11 |

---

## pipeline/ — Pipeline 编排

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_msghub.py` | ~160 | `MsgHub` | ch19 |
| `_functional.py` | ~190 | `sequential_pipeline`, `fanout_pipeline` | ch26 |
| `_class.py` | ~100 | `SequentialPipeline`, `FanoutPipeline` | ch26 |

---

## tracing/ — 可观测性

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_trace.py` | ~600 | 5 种 trace 装饰器 | ch20 |
| `_setup.py` | ~50 | `setup_tracing` | ch20 |

---

## _utils/ — 工具函数

| 文件 | 行数 | 主要内容 | 首次引用 |
|------|------|---------|---------|
| `_common.py` | ~200 | `_parse_tool_function` 等 | ch17 |

---

## 其他模块

| 目录 | 首次引用 | 备注 |
|------|---------|------|
| `rag/` | ch07 | RAG 知识库 |
| `embedding/` | ch07 | 向量嵌入 |
| `token/` | ch08 | Token 计数 |
| `session/` | ch20 | 会话管理 |
| `a2a/` | ch14 | A2A 协议 |
| `realtime/` | ch14 | 实时语音 |
| `mcp/` | ch17 | MCP 客户端 |
| `evaluate/` | ch36 | 评估工具 |
| `tts/` | ch11 | 语音合成 |
| `plan/` | ch11 | 规划子系统 |
