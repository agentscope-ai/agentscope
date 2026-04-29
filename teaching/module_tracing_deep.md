# Tracing 追踪模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [目录结构](#2-目录结构)
3. [源码解读](#3-源码解读)
   - [setup_tracing 初始化](#31-setup_tracing-初始化)
   - [Span 属性体系](#32-span-属性体系)
   - [通用 trace 装饰器](#33-通用-trace-装饰器)
   - [trace_llm LLM 调用追踪](#34-trace_llm-llm-调用追踪)
   - [trace_reply Agent 回复追踪](#35-trace_reply-agent-回复追踪)
   - [trace_toolkit 工具调用追踪](#36-trace_toolkit-工具调用追踪)
   - [trace_embedding 嵌入调用追踪](#37-trace_embedding-嵌入调用追踪)
   - [trace_format 格式化追踪](#38-trace_format-格式化追踪)
   - [生成器追踪机制](#39-生成器追踪机制)
4. [设计模式总结](#4-设计模式总结)
5. [代码示例](#5-代码示例)
6. [练习题](#6-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 6 个追踪装饰器及其装饰目标 | 列举、识别 |
| 理解 | 解释 OpenTelemetry Span 属性体系与 GenAI 语义约定 | 解释、描述 |
| 应用 | 使用 setup_tracing 配置追踪并接入 OTLP 后端 | 实现、配置 |
| 分析 | 分析同步/异步生成器追踪的包装机制 | 分析、追踪 |
| 评价 | 评价各追踪装饰器捕获的属性是否足够完整 | 评价、推荐 |
| 创造 | 设计一个自定义 Span 属性提取器用于业务追踪 | 设计、构建 |

## 先修检查

- [ ] OpenTelemetry 基础概念（Tracer、Span、Attribute）
- [ ] Python 装饰器原理（闭包、`functools.wraps`）
- [ ] Python 生成器（Generator）和异步生成器（AsyncGenerator）
- [ ] `inspect.iscoroutinefunction` 类型检查

## Java 开发者对照

| AgentScope 概念 | Java 对应 | 说明 |
|----------------|-----------|------|
| `setup_tracing()` | `OpenTelemetrySdk.builder()` | 初始化追踪基础设施 |
| `@trace_llm` | `@WithSpan("chat")` | 自动创建 Span |
| `SpanAttributes` | `SemanticAttributes` | 语义约定属性常量 |
| `OTLPSpanExporter` | `OtlpGrpcSpanExporter` | 导出到 OTLP 后端 |
| `BatchSpanProcessor` | `BatchSpanProcessor` | 批量导出优化 |
| `_trace_async_generator_wrapper` | Reactor `tap()` operator | 流式数据追踪 |

---

## 1. 模块概述

> **交叉引用**: Tracing 模块为 AgentScope 提供 OpenTelemetry 可观测性。它追踪 Model 层的 LLM 调用（`trace_llm`，详见 [Model 模块](module_model_deep.md)）、Agent 的 reply 调用（`trace_reply`，详见 [Agent 模块](module_agent_deep.md)）、工具执行（`trace_toolkit`，详见 [Tool 模块](module_tool_mcp_deep.md)）、嵌入调用（`trace_embedding`，详见 [Embedding 模块](module_embedding_token_deep.md)）和格式化调用（`trace_format`，详见 [Formatter 模块](module_formatter_deep.md)）。

Tracing 模块基于 OpenTelemetry 标准实现了 AgentScope 的可观测性层，通过装饰器模式无侵入地追踪框架核心操作。它遵循 OpenTelemetry GenAI 语义约定，确保追踪数据可以被 Jaeger、Zipkin、Grafana Tempo 等标准后端接收和可视化。

**核心能力**：

1. **自动追踪**：通过装饰器自动追踪 LLM 调用、Agent 回复、工具执行等
2. **流式支持**：支持同步和异步生成器的流式追踪
3. **语义约定**：遵循 OpenTelemetry GenAI Semantic Conventions
4. **可配置**：通过 Config 启用/禁用追踪，支持自定义 OTLP 端点

**源码位置**: `src/agentscope/tracing/`（~1,973 行，7 个文件）

---

## 2. 目录结构

```
tracing/
├── __init__.py                    # 导出接口（setup + 5 装饰器）
├── _setup.py                      # setup_tracing 初始化（49 行）
├── _trace.py                      # 所有追踪装饰器（646 行）
├── _attributes.py                 # Span 属性常量定义（183 行）
├── _extractor.py                  # 属性提取函数（892 行）
├── _converter.py                  # 数据转换工具（125 行）
└── _utils.py                      # 序列化工具（78 行）
```

---

## 3. 源码解读

### 3.1 setup_tracing 初始化

```python
def setup_tracing(endpoint: str) -> None:
    # 1. 创建 OTLP HTTP 导出器
    exporter = OTLPSpanExporter(endpoint=endpoint)

    # 2. 创建批处理处理器
    processor = BatchSpanProcessor(exporter)

    # 3. 获取或创建 TracerProvider
    provider = get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(processor)
    else:
        set_tracer_provider(TracerProvider([processor]))

# 内部获取 Tracer
def _get_tracer() -> Tracer:
    return get_tracer("agentscope", __version__)
```

**初始化流程**：

```
setup_tracing("http://localhost:4318/v1/traces")
    ↓
OTLPSpanExporter → BatchSpanProcessor → TracerProvider
    ↓
所有装饰器共享同一个 Tracer（"agentscope"）
```

### 3.2 Span 属性体系

`_attributes.py` 定义了三层属性常量：

**GenAI 标准属性（遵循 OpenTelemetry 语义约定）**：

| 分组 | 属性 | 说明 |
|------|------|------|
| 请求 | `gen_ai.request.model` | 请求的模型名称 |
| 请求 | `gen_ai.request.temperature` | 采样温度 |
| 请求 | `gen_ai.request.max_tokens` | 最大生成 Token |
| 响应 | `gen_ai.response.id` | 响应 ID |
| 响应 | `gen_ai.response.finish_reasons` | 结束原因 |
| 用量 | `gen_ai.usage.input_tokens` | 输入 Token 数 |
| 用量 | `gen_ai.usage.output_tokens` | 输出 Token 数 |
| 消息 | `gen_ai.input_messages` | 输入消息 |
| 消息 | `gen_ai.output_messages` | 输出消息 |
| 工具 | `gen_ai.tool.call_id` | 工具调用 ID |
| 工具 | `gen_ai.tool.name` | 工具名称 |
| 嵌入 | `gen_ai.embeddings.dimension_count` | 嵌入维度 |

**AgentScope 扩展属性**：

| 属性 | 说明 |
|------|------|
| `agentscope.format.target` | 格式化目标 |
| `agentscope.format.count` | 格式化消息数 |
| `agentscope.function.name` | 通用函数名 |
| `agentscope.function.input` | 函数输入 |
| `agentscope.function.output` | 函数输出 |

**操作名称枚举**：

| 值 | 用于 |
|----|------|
| `"chat"` | LLM 调用 |
| `"invoke_agent"` | Agent 回复 |
| `"execute_tool"` | 工具执行 |
| `"embeddings"` | 嵌入调用 |
| `"format"` | 格式化调用 |

### 3.3 通用 trace 装饰器

```python
def trace(name: str | None = None) -> Callable:
    """通用追踪装饰器，支持同步和异步函数"""
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            async def wrapper(*args, **kwargs):
                if not _check_tracing_enabled():
                    return await func(*args, **kwargs)
                # 构建 Span 属性 → 启动 Span → 执行 → 记录结果
                span_name = name or func.__name__
                with tracer.start_as_current_span(span_name, end_on_exit=False) as span:
                    try:
                        result = await func(*args, **kwargs)
                        # 处理 AsyncGenerator
                        if isinstance(result, AsyncGenerator):
                            return _trace_async_generator_wrapper(result, span)
                        _set_span_success_status(span)
                        return result
                    except Exception as e:
                        _set_span_error_status(span, e)
                        raise
        else:
            # 同步版本，逻辑相同但不含 await
            ...
        return wrapper
    return decorator
```

**装饰器的通用模式**：

```
1. 检查追踪是否启用 → 未启用则直接调用原函数
2. 构建请求属性
3. 启动 Span（end_on_exit=False，手动控制结束）
4. try: 调用原函数
   ├── 正常返回 → 设置响应属性 → 标记成功 → 结束 Span
   └── 异常 → 记录异常 → 标记错误 → 结束 Span → 重新抛出
```

### 3.4 trace_llm LLM 调用追踪

```python
def trace_llm(func) -> Callable:
    """装饰 ChatModelBase.__call__"""
    async def wrapper(self, *args, **kwargs):
        # 验证 self 是 ChatModelBase 实例
        if not isinstance(self, ChatModelBase):
            logger.warning(...)
            return await func(self, *args, **kwargs)
        # 提取 LLM 请求属性（model, messages, tools, ...）
        # 支持 AsyncGenerator（流式响应）
```

**捕获的属性**：

| 阶段 | 属性 |
|------|------|
| 请求 | model, temperature, top_p, max_tokens, tools, messages |
| 响应 | response_id, finish_reasons, input_tokens, output_tokens |

**流式响应追踪**：LLM 返回 `AsyncGenerator[ChatResponse, None]` 时，使用 `_trace_async_generator_wrapper` 捕获最后一个 chunk 作为响应属性。

### 3.5 trace_reply Agent 回复追踪

```python
def trace_reply(func) -> Callable:
    """装饰 AgentBase.reply"""
    async def wrapper(self, *args, **kwargs):
        if not isinstance(self, AgentBase):
            return await func(self, *args, **kwargs)
        # 提取 Agent 属性（name, description, system instructions）
```

**捕获的属性**：

| 阶段 | 属性 |
|------|------|
| 请求 | agent_id, agent_name, agent_description, system_instructions |
| 响应 | 输出消息内容 |

### 3.6 trace_toolkit 工具调用追踪

```python
def trace_toolkit(func) -> Callable:
    """装饰 Toolkit.call_tool_function"""
    # 工具调用返回 AsyncGenerator[ToolResponse, None]
```

**捕获的属性**：

| 阶段 | 属性 |
|------|------|
| 请求 | tool_call_id, tool_name, tool_call_arguments |
| 响应 | tool_call_result |

### 3.7 trace_embedding 嵌入调用追踪

```python
def trace_embedding(func) -> Callable:
    """装饰 EmbeddingModelBase.__call__"""
```

**捕获的属性**：

| 阶段 | 属性 |
|------|------|
| 请求 | model, embeddings_dimension_count |
| 响应 | 嵌入结果摘要 |

### 3.8 trace_format 格式化追踪

```python
def trace_format(func) -> Callable:
    """装饰 FormatterBase.format"""
    # 返回 list[dict]（非生成器）
```

**捕获的属性**：

| 阶段 | 属性 |
|------|------|
| 请求 | format_target |
| 响应 | format_count（格式化后的消息数） |

### 3.9 生成器追踪机制

框架需要同时追踪同步生成器和异步生成器（流式响应）：

```python
def _trace_sync_generator_wrapper(res, span):
    """包装同步生成器"""
    last_chunk = None
    try:
        for chunk in res:
            last_chunk = chunk
            yield chunk
        # 生成结束后，用最后一个 chunk 设置响应属性
        _set_span_success_status(span)
    except Exception as e:
        _set_span_error_status(span, e)
        raise

async def _trace_async_generator_wrapper(res, span):
    """包装异步生成器"""
    last_chunk = None
    try:
        async for chunk in res:
            last_chunk = chunk
            yield chunk
        # 根据操作类型提取不同属性
        if span.name == "chat":
            # 提取 LLM 响应属性
        elif span.name == "execute_tool":
            # 提取工具响应属性
        _set_span_success_status(span)
    except Exception as e:
        _set_span_error_status(span, e)
        raise
```

**关键设计**：Span 的生命周期覆盖整个生成器的迭代过程，直到最后一个 chunk 被消费后才结束。

---

## 4. 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Decorator（装饰器）** | 所有 trace_* 函数 | 无侵入式追踪 |
| **Strategy（策略）** | 属性提取函数 | 不同操作类型的属性提取策略 |
| **Wrapper（包装器）** | 生成器追踪 | 包装生成器以追踪流式数据 |
| **Template Method** | 装饰器通用模式 | 检查启用 → 构建属性 → 启动 Span → 处理结果 |
| **Lazy Initialization** | _get_tracer() | 按需获取 Tracer 实例 |

---

## 5. 代码示例

### 5.1 基本追踪配置

```python
from agentscope.tracing import setup_tracing

# 配置 OTLP 端点（如 Jaeger、Grafana Tempo）
setup_tracing(endpoint="http://localhost:4318/v1/traces")

# 之后所有 LLM 调用、Agent 回复、工具执行都会自动被追踪
```

### 5.2 查看追踪数据（Jaeger UI）

配置后，在 Jaeger UI 中可以看到如下 Span 树：

```
[invoke_agent: analyst]
  ├── [chat: gpt-4]          # LLM 调用
  │     gen_ai.request.model = "gpt-4"
  │     gen_ai.usage.input_tokens = 156
  │     gen_ai.usage.output_tokens = 42
  ├── [execute_tool: search]  # 工具调用
  │     gen_ai.tool.name = "web_search"
  │     gen_ai.tool.call_id = "call_abc123"
  └── [chat: gpt-4]          # 第二次 LLM 调用
```

### 5.3 使用通用 trace 装饰器

```python
from agentscope.tracing import trace

@trace(name="custom_analysis")
async def analyze_data(data: dict) -> dict:
    # 这个函数的执行会被追踪
    return {"result": "analysis_complete"}

# 在 Jaeger 中可以看到名为 "custom_analysis" 的 Span
```

---

## 6. 练习题

### 基础题

**Q1**: 为什么所有装饰器在开始时都检查 `_check_tracing_enabled()`？如果不检查会怎样？

**Q2**: `_trace_async_generator_wrapper` 为什么在最后一个 chunk 消费后才结束 Span？

### 中级题

**Q3**: 对比 `trace_llm` 和 `trace_reply` 的装饰目标。它们各自的 `self` 类型验证有什么意义？

**Q4**: 分析 `SpanAttributes` 中哪些属性遵循 OpenTelemetry GenAI 语义约定，哪些是 AgentScope 扩展。为什么需要扩展属性？

### 挑战题

**Q5**: 设计一个追踪数据聚合器，定期从 Span 属性中提取 Token 用量，计算每个 Agent 的累计成本。需要考虑哪些设计问题？

---

### 参考答案

**A1**: 如果不检查，每次调用都会创建 Span，即使在生产环境中不需要追踪。这会带来性能开销（Span 创建、属性序列化）和内存占用。通过全局开关控制，可以零成本禁用追踪。

**A2**: 生成器的特点是可以被惰性消费——消费者可能只取部分 chunk 就停止。Span 必须覆盖整个操作的生命周期。如果提前结束 Span，后续 chunk 的追踪数据会丢失。最后一个 chunk 通常包含完整的使用统计（Token 数量、finish_reason），必须等它被消费后才能完整记录。

**A3**: `trace_llm` 装饰 `ChatModelBase.__call__`，验证 `self` 是 `ChatModelBase` 实例确保能正确提取 LLM 特定属性（model_name, temperature 等）。`trace_reply` 装饰 `AgentBase.reply`，验证 `self` 是 `AgentBase` 实例确保能提取 Agent 属性（name, description）。类型验证防止装饰器被意外应用到错误的方法上。

**A4**: 标准属性（如 `gen_ai.request.model`, `gen_ai.usage.input_tokens`）遵循 OpenTelemetry GenAI 语义约定，确保跨框架兼容——任何支持 OpenTelemetry 的后端都能正确解析。扩展属性（`agentscope.format.target` 等）覆盖 AgentScope 特有的概念（格式化、通用函数），这些在标准语义约定中不存在。扩展属性使用 `agentscope.` 前缀避免命名冲突。

**A5**: 关键设计问题：(1) 采集方式——通过 SpanProcessor 拦截还是定期查询后端 API；(2) 聚合粒度——按 Agent、按会话、按时间窗口；(3) 成本模型——不同模型的价格不同，需要按 model_name 区分；(4) 持久化——聚合数据存储在哪里（Redis/数据库）；(5) 实时性——流式响应的 Token 计数可能延迟。

---

## 模块小结

| 概念 | 要点 |
|------|------|
| setup_tracing | OTLP HTTP 导出器 + BatchSpanProcessor |
| trace_llm | 追踪 LLM 调用，支持流式 |
| trace_reply | 追踪 Agent 回复 |
| trace_toolkit | 追踪工具调用 |
| trace_embedding | 追踪嵌入调用 |
| trace_format | 追踪格式化调用 |
| 生成器追踪 | 包装生成器，最后一个 chunk 后结束 Span |
| Span 属性 | GenAI 语义约定 + AgentScope 扩展 |

## 章节关联

| 相关模块 | 关联点 |
|----------|--------|
| [Model 模块](module_model_deep.md) | trace_llm 追踪 Model 调用 |
| [Agent 模块](module_agent_deep.md) | trace_reply 追踪 Agent 回复 |
| [Tool 模块](module_tool_mcp_deep.md) | trace_toolkit 追踪工具执行 |
| [Embedding 模块](module_embedding_token_deep.md) | trace_embedding 追踪嵌入调用 |
| [Formatter 模块](module_formatter_deep.md) | trace_format 追踪格式化 |
| [Config 模块](module_config_deep.md) | trace_enabled 配置项控制追踪开关 |

**版本参考**: AgentScope >= 1.0.0 | 源码 `tracing/`
