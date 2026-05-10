# 第十八章：可观测性与持久化——当 Agent 跑了十分钟，你需要知道它卡在哪

**难度**：中等

> 你部署了一个 ReAct Agent，让它处理一批复杂任务。十分钟后你发现它没有返回结果。日志只有一行 "calling model..."。你需要知道：它在等 LLM 响应？在执行工具？还是在某个循环里打转？这一章拆解 AgentScope 如何通过 OpenTelemetry 追踪和 Session 持久化让 Agent 的运行状态变得透明。

---

## 1. 开场场景

一个典型的生产问题：

```python
agent = ReActAgent(
    name="researcher",
    model=model,
    toolkit=toolkit,
)

# 这个调用可能运行几分钟
result = await agent(msg)
```

当这行代码运行超过预期时间，你面临三个问题：

1. **不可见**：不知道 Agent 当前在哪个步骤
2. **不可恢复**：如果进程崩溃，所有中间状态丢失
3. **不可诊断**：没有历史数据判断瓶颈在 LLM 调用还是工具执行

AgentScope 用两条路径解决：**OpenTelemetry 追踪**让运行过程可见，**Session 管理**让状态可恢复。

---

## 2. 设计模式概览

AgentScope 的可观测性分为两层：

```
                     用户调用
                        |
                        v
               +------------------+
               |   Agent.reply()  |  <-- @trace_reply
               +------------------+
                  /            \
                 v              v
    +------------------+  +------------------+
    |  Formatter.call()|  | Model.__call__() |  <-- @trace_format / @trace_llm
    +------------------+  +------------------+
                               |
                               v
                    +------------------+
                    | Toolkit.call()   |  <-- @trace_toolkit
                    +------------------+
                               |
                               v
                    +------------------+
                    | EmbeddingModel   |  <-- @trace_embedding
                    +------------------+
```

每个 `@trace_*` 装饰器在方法执行时创建一个 OpenTelemetry Span。Span 之间通过调用链自动形成父子关系。配合 Session 模块，`StateModule.state_dict()` 将 Agent 的内部状态序列化到外部存储。

关键源码位置：

```
src/agentscope/tracing/
├── _setup.py          -- Tracer 初始化（第 11 行）
├── _trace.py          -- 6 个 trace 装饰器（第 192-647 行）
├── _attributes.py     -- Span 属性定义（第 8-184 行）
├── _extractor.py      -- 属性提取器（第 52-893 行）
└── _converter.py      -- ContentBlock 转换（第 57-125 行）

src/agentscope/session/
├── _session_base.py   -- SessionBase 抽象类（第 8 行）
├── _json_session.py   -- JSON 文件存储（第 12 行）
├── _redis_session.py  -- Redis 存储（第 17 行）
└── _tablestore_session.py  -- Tablestore 存储（第 12 行）

src/agentscope/module/_state_module.py  -- StateModule 基类（第 20 行）
```

---

## 3. 源码分析

### 3.1 追踪的开关：`trace_enabled`

追踪默认是关闭的。启用它需要调用 `agentscope.init()` 时传入 `tracing_url` 参数：

`src/agentscope/__init__.py` 第 147-156 行：

```python
if endpoint:
    from .tracing import setup_tracing

    setup_tracing(endpoint=endpoint)
    _config.trace_enabled = True
```

`_config.trace_enabled` 是一个 `ContextVar[bool]`，初始值为 `False`（`__init__.py` 第 37-40 行）。每个 `@trace` 装饰器的第一件事就是检查这个开关——如果未启用，直接跳过所有追踪逻辑，零开销：

`_trace.py` 第 69-77 行：

```python
def _check_tracing_enabled() -> bool:
    """Check if the OpenTelemetry tracer is initialized."""
    return _config.trace_enabled
```

这个设计确保追踪在生产环境中可以选择性开启，不会因为追踪逻辑本身影响性能。

### 3.2 Tracer 初始化：`setup_tracing`

`_setup.py` 第 11-38 行展示了初始化过程：

```python
def setup_tracing(endpoint: str) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)

    tracer_provider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        tracer_provider.add_span_processor(span_processor)
    else:
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)
```

三个要点：

1. **OTLP 协议**：使用 `OTLPSpanExporter`，通过 HTTP 发送到兼容的收集器（Jaeger、Zipkin、Grafana Tempo 等）
2. **批量处理**：`BatchSpanProcessor` 不是每产生一个 Span 就发送，而是批量导出，减少网络开销
3. **防御性初始化**：检查当前 `TracerProvider` 是否已经是 SDK 版本——如果应用已经自行配置了 OpenTelemetry，只追加 processor 而不覆盖

`_get_tracer()` 在 `_setup.py` 第 41-49 行返回一个命名为 `"agentscope"` 的 Tracer，附带框架版本号。

### 3.3 装饰器体系：6 个 trace 函数

AgentScope 定义了 6 个专用追踪装饰器，分别对应框架中不同的可观测层：

| 装饰器 | 被装饰对象 | Span 操作名 | 应用位置 |
|--------|-----------|-------------|---------|
| `trace_reply` | `Agent.reply()` | `invoke_agent` | `_react_agent.py:375` |
| `trace_llm` | `ChatModelBase.__call__()` | `chat` | 各 model 文件 |
| `trace_toolkit` | `Toolkit.call_tool_function()` | `execute_tool` | `_toolkit.py:851` |
| `trace_format` | `FormatterBase.__call__()` | `format` | `_truncated_formatter_base.py:47` |
| `trace_embedding` | `EmbeddingModelBase` | `embeddings` | embedding 模块 |
| `trace` | 通用函数 | `invoke_generic_function` | 任意函数 |

以 `trace_llm` 为例，分析装饰器的工作流程（`_trace.py` 第 567-647 行）：

```python
def trace_llm(func):
    @wraps(func)
    async def async_wrapper(self: ChatModelBase, *args, **kwargs):
        if not _check_tracing_enabled():
            return await func(*args, **kwargs)  # 零开销跳过

        tracer = _get_tracer()

        # 1. 提取请求属性
        request_attributes = _get_llm_request_attributes(self, args, kwargs)
        span_name = _get_llm_span_name(request_attributes)

        # 2. 创建 Span
        with tracer.start_as_current_span(
            name=span_name,
            attributes={**request_attributes, **_get_common_attributes()},
            end_on_exit=False,
        ) as span:
            try:
                res = await func(self, *args, **kwargs)

                # 3. 处理流式响应
                if isinstance(res, AsyncGenerator):
                    return _trace_async_generator_wrapper(res, span)

                # 4. 记录响应属性
                span.set_attributes(_get_llm_response_attributes(res))
                _set_span_success_status(span)
                return res

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None
```

四个步骤形成完整的生命周期：**提取属性 -> 创建 Span -> 执行方法 -> 记录结果**。

### 3.4 流式追踪：Generator 的特殊处理

LLM 响应可能是流式的（`AsyncGenerator`），不能等所有 chunk 产出后才结束 Span。`_trace_async_generator_wrapper`（`_trace.py` 第 134-189 行）用 try/finally 模式解决这个问题：

```python
async def _trace_async_generator_wrapper(res, span):
    has_error = False
    try:
        last_chunk = None
        async for chunk in aioitertools.iter(res):
            last_chunk = chunk
            yield chunk
    except Exception as e:
        has_error = True
        _set_span_error_status(span, e)
        raise e from None
    finally:
        if not has_error:
            # 用最后一个 chunk 的内容作为 Span 输出
            span.set_attributes(response_attributes)
            _set_span_success_status(span)
```

关键设计：追踪只记录**最后一个 chunk**，而不是全部。这避免了海量数据写入 Span 属性，同时保留了最终结果的可见性。`aioitertools.iter` 确保异步迭代器的正确处理。

### 3.5 属性提取：OpenTelemetry GenAI 语义约定

AgentScope 不自创属性命名，而是遵循 OpenTelemetry GenAI 语义约定（Semantic Conventions）。`_attributes.py` 第 8-129 行定义了 `SpanAttributes` 类，其值直接来自 `opentelemetry.semconv` 包：

```python
from opentelemetry.semconv._incubating.attributes import (
    gen_ai_attributes as GenAIAttributes,
)

class SpanAttributes:
    GEN_AI_OPERATION_NAME = GenAIAttributes.GEN_AI_OPERATION_NAME
    GEN_AI_REQUEST_MODEL = GenAIAttributes.GEN_AI_REQUEST_MODEL
    GEN_AI_USAGE_INPUT_TOKENS = GenAIAttributes.GEN_AI_USAGE_INPUT_TOKENS
    # ... 以及 AgentScope 扩展属性
    AGENTSCOPE_FUNCTION_INPUT = "agentscope.function.input"
    AGENTSCOPE_FUNCTION_OUTPUT = "agentscope.function.output"
```

分为两层：
- **标准属性**：`gen_ai.*` 前缀，兼容所有 OpenTelemetry 工具
- **扩展属性**：`agentscope.*` 前缀，记录框架特有的数据

`OperationNameValues`（第 131-149 行）定义了操作名称枚举：`chat`、`invoke_agent`、`execute_tool`、`format`、`embeddings`、`invoke_generic_function`。

### 3.6 Session 持久化：StateModule + SessionBase

追踪解决"看得见"的问题，Session 解决"存得住"的问题。

**StateModule**（`_state_module.py` 第 20-152 行）提供 `state_dict()` / `load_state_dict()` 接口，类似 PyTorch 的状态管理。核心机制是两个 `OrderedDict`：

```python
class StateModule:
    def __init__(self):
        self._module_dict = OrderedDict()    # 嵌套的 StateModule 子模块
        self._attribute_dict = OrderedDict()  # 需要持久化的属性
```

当 `StateModule` 的属性本身也是 `StateModule` 时，`__setattr__` 自动将其注册到 `_module_dict`（第 29-39 行）。对于普通属性，调用 `register_state()` 显式注册：

```python
def register_state(self, attr_name, custom_to_json=None, custom_from_json=None):
    # 检查 JSON 可序列化性
    if custom_to_json is None:
        json.dumps(getattr(self, attr_name))  # 可能抛 TypeError

    self._attribute_dict[attr_name] = _JSONSerializeFunction(
        to_json=custom_to_json,
        load_json=custom_from_json,
    )
```

`state_dict()` 递归收集所有子模块和注册属性的状态，`load_state_dict()` 递归恢复。

**SessionBase**（`_session_base.py` 第 8-48 行）定义了两个抽象方法：

```python
class SessionBase:
    @abstractmethod
    async def save_session_state(self, session_id, user_id="", **state_modules_mapping):
        ...

    @abstractmethod
    async def load_session_state(self, session_id, user_id="", allow_not_exist=True, **state_modules_mapping):
        ...
```

`**state_modules_mapping` 的设计让调用者可以一次性保存多个 `StateModule`：

```python
await session.save_session_state(
    session_id="run_001",
    agent=my_agent,           # StateModule 实例
    memory=my_memory,         # StateModule 实例
)
```

### 3.7 三种 Session 实现

**JSONSession**（`_json_session.py`）最简单——将 `state_dict` 序列化为 JSON 写入文件。文件名规则：`{user_id}_{session_id}.json`。适合单机开发和测试。

**RedisSession**（`_redis_session.py`）使用 Redis 存储会话状态。Key 格式为 `user_id:{user_id}:session:{session_id}:state`（第 20 行）。两个值得注意的设计：

1. **滑动 TTL**：`key_ttl` 参数支持自动过期。`load_session_state` 使用 `GETEX` 命令在读取的同时刷新 TTL（第 151-152 行），实现"活跃会话不过期"
2. **异步上下文管理器**：支持 `async with` 语法自动关闭连接

**TablestoreSession**（`_tablestore_session.py`）面向阿里云 Tablestore，使用惰性初始化（`_ensure_initialized`，第 91-124 行）配合 `asyncio.Lock` 防止并发初始化。状态存储在 session 表的 `metadata["__state__"]` 字段中。

三种实现的 `save_session_state` 都遵循相同模式：

```python
state_dicts = {
    name: state_module.state_dict()
    for name, state_module in state_modules_mapping.items()
}
# 然后写入各自的存储后端
```

---

## 4. 设计一瞥

### 为什么选择 OpenTelemetry 而不是自建追踪？

自建追踪系统的诱惑很大——更简单，更灵活。AgentScope 选择 OpenTelemetry 有三个理由：

1. **生态兼容**：OpenTelemetry 是 CNCF 的可观测性标准。Jaeger、Zipkin、Grafana Tempo、Datadog、New Relic 都支持 OTLP 协议。用户不需要学习新的查询语言或 dashboard 工具。

2. **GenAI 语义约定**：OpenTelemetry 社区已经定义了 `gen_ai.*` 属性标准（`_attributes.py` 中引用的 `GenAIAttributes`）。这意味着 AgentScope 的 Span 可以被任何理解 GenAI 约定的工具正确解析和展示。

3. **零侵入集成**：`_check_tracing_enabled()` 确保未启用追踪时代码路径零开销。追踪逻辑完全封装在装饰器中，不污染业务代码。

### `end_on_exit=False` 的设计

所有 Span 创建时都传入 `end_on_exit=False`（例如 `_trace.py` 第 249 行）。这意味着 `with` 块退出时不会自动结束 Span，而是由代码显式调用 `_set_span_success_status` 或 `_set_span_error_status` 来结束。原因是为了确保**响应属性在 Span 结束前写入**——如果 `with` 自动结束 Span，后写的属性可能丢失。

---

## 5. 横向对比

| 特性 | AgentScope | LangSmith | Weights & Biases Weave |
|------|-----------|-----------|----------------------|
| 追踪协议 | OpenTelemetry (OTLP) | 自有协议 | 自有协议 |
| 后端兼容 | 任何 OTLP 收集器 | LangSmith SaaS/自部署 | W&B 云端 |
| GenAI 语义约定 | 遵循 OpenTelemetry 标准 | 自定义 schema | 自定义 schema |
| 流式追踪 | 记录最后 chunk | 记录完整流 | 记录完整流 |
| 状态持久化 | StateModule + Session | LangSmith Sessions | W&B Artifacts |
| 存储后端 | JSON/Redis/Tablestore | 自有存储 | 自有存储 |
| 开源 | 是 | 部分 | 是 |

LangSmith 提供更完整的托管体验——trace 自动上传、可视化 dashboard、prompt 管理一体化。但它是**封闭生态**：追踪数据只能存在 LangSmith 中。AgentScope 的 OpenTelemetry 方案牺牲了一些开箱即用的便利，换取了后端自由度和标准兼容性。

---

## 6. 调试实践

### 启用追踪

```python
import agentscope

agentscope.init(tracing_url="http://localhost:4318/v1/traces")
```

这会初始化 OTLP Exporter 并设置 `trace_enabled = True`。你需要一个 OTLP 兼容的收集器运行在指定地址（推荐 Jaeger All-in-One）。

### 追踪一个完整的 Agent 调用

当 ReActAgent 处理一条消息时，追踪系统会自动产生以下 Span 层级：

```
invoke_agent researcher           (trace_reply)
├── format openai                 (trace_format)
├── chat gpt-4                    (trace_llm)
├── execute_tool search           (trace_toolkit)
├── format openai                 (trace_format)
└── chat gpt-4                    (trace_llm)
```

每个 Span 携带的属性让你可以：
- 看到 LLM 调用的 `temperature`、`max_tokens` 等参数
- 看到 `input_tokens` 和 `output_tokens` 的用量
- 看到工具调用的参数和返回值
- 看到每一步的耗时（Span 自带的 `start_time` 和 `end_time`）

### Session 持久化与恢复

```python
from agentscope.session import JSONSession

session = JSONSession(save_dir="./checkpoints")

# 保存状态
await session.save_session_state(
    session_id="task_001",
    agent=my_agent,
    memory=my_memory,
)

# ... 进程重启 ...

# 恢复状态
await session.load_session_state(
    session_id="task_001",
    agent=my_agent,
    memory=my_memory,
)
```

`JSONSession` 将状态写入 `./checkpoints/task_001.json`。文件内容是所有 `state_dict()` 的合并结果。恢复时，`load_state_dict` 将数据写回对应的 StateModule 实例。

---

## 7. 检查点

1. `trace_llm` 装饰器在追踪未启用时的行为是什么？查看 `_trace.py` 第 601 行，确认零开销路径。

2. `_trace_async_generator_wrapper` 为什么只记录最后一个 chunk 而不是全部？考虑 Span 属性的大小限制。

3. `StateModule.__setattr__` 如何区分普通属性和 StateModule 子模块？查看 `_state_module.py` 第 29-39 行。

4. `RedisSession` 的滑动 TTL 机制如何工作？查看 `_redis_session.py` 第 150-154 行，理解 `GETEX` 命令的作用。

5. 如果你想追踪一个自定义函数，应该用哪个装饰器？查看 `trace` 通用装饰器（`_trace.py` 第 192 行）的用法。

---

## 8. 下一章预告

Volume 3 将从设计模式走向工程实践——多 Agent 协作、A2A 协议、RAG 管线，以及生产部署中的真实挑战。
