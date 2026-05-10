# 附录 B：术语表

双语术语对照表。英文 | 中文 | 简要定义。

---

## 核心架构术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Agent | 智能体 | 具备推理、工具调用和记忆能力的自主实体，继承自 `AgentBase` |
| Model | 模型 | 大语言模型适配器，封装不同 API 提供商的调用接口，继承自 `ChatModelBase` |
| Formatter | 格式化器 | 将 `Msg` 消息转换为特定模型 API 所需的请求格式，继承自 `FormatterBase` |
| Tool | 工具 | Agent 可调用的外部函数，注册到 Toolkit 中供模型使用 |
| Toolkit | 工具箱 | 工具函数的注册与管理容器，继承自 `StateModule`，支持中间件模式 |
| Memory | 记忆 | 存储对话历史的组件，分为工作记忆和长期记忆两大类 |
| Pipeline | 流水线 | 多 Agent 协作的编排模式，包括顺序、扇出等拓扑 |
| StateModule | 状态模块 | 提供嵌套状态序列化/反序列化的基类，通过 `__setattr__` 自动追踪子模块 |

---

## ReAct 循环术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| ReAct | 推理-行动循环 | Reasoning + Acting 的迭代模式：推理决定行动，行动结果反馈推理 |
| Reasoning | 推理 | ReAct 循环中分析当前状态、决定下一步行动的阶段 |
| Acting | 行动 | ReAct 循环中执行工具调用、获取结果的阶段 |
| Summarizing | 摘要压缩 | 将冗长的对话历史压缩为简洁摘要，以节省 Token |
| Plan | 计划 | 将复杂目标分解为有序子任务的结构化计划，使用 Pydantic BaseModel |
| PlanNotebook | 计划笔记本 | 管理和追踪计划执行状态的组件，继承自 `StateModule` |
| SubTask | 子任务 | Plan 中的最小执行单元，包含名称、描述和状态 |
| DefaultPlanToHint | 计划转提示词 | 将 Plan 结构转换为 LLM 可理解的提示词的转换器 |

---

## 记忆与存储术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Working Memory | 工作记忆 | 短期对话上下文存储，实现包括 `InMemoryMemory`、`RedisMemory` 等 |
| Long-term Memory | 长期记忆 | 跨会话的持久化知识存储，实现包括 `Mem0LongTermMemory`、`ReMe` 等 |
| InMemoryMemory | 内存记忆 | 基于列表的内存存储实现，最简单的工作记忆方案 |
| RedisMemory | Redis 记忆 | 基于 Redis 的持久化工作记忆，支持跨进程共享 |
| AsyncSQLAlchemyMemory | 异步数据库记忆 | 基于 SQLAlchemy 的异步数据库存储，适合大规模持久化 |
| Session | 会话 | 管理对话持久化存储的组件，支持 JSON、Redis、Tablestore 等后端 |
| state_dict | 状态字典 | 将模块状态序列化为字典的方法，支持嵌套递归，类似 PyTorch 的 state_dict |
| Serialization | 序列化 | 将运行时对象转换为可存储/传输格式（如 JSON）的过程 |
| Persistence | 持久化 | 将运行时状态保存到外部存储（Redis、SQLAlchemy 等），使其跨会话存活 |

---

## 消息与内容块术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Msg | 消息 | AgentScope 中的核心消息类型，包含角色、内容和元数据 |
| ContentBlock | 内容块 | 消息中结构化内容的联合类型，包括文本、图片、工具调用等 |
| TextBlock | 文本块 | 纯文本内容块，最基本的 ContentBlock 类型 |
| ThinkingBlock | 思维块 | 记录模型内部推理过程的内容块（如 Claude 的 extended thinking） |
| ToolUseBlock | 工具调用块 | 表示模型发起的工具调用请求，包含工具名和参数 |
| ToolResultBlock | 工具结果块 | 表示工具执行后返回的结果，关联对应的工具调用 ID |
| ImageBlock | 图片块 | 包含图片数据的内容块，支持 URL 和 Base64 两种来源 |
| AudioBlock | 音频块 | 包含音频数据的内容块，用于语音交互场景 |
| VideoBlock | 视频块 | 包含视频数据的内容块，用于多模态输入 |
| Base64Source | Base64 来源 | 表示以 Base64 编码内嵌的多媒体数据来源，含 media_type 和 data 字段 |
| URLSource | URL 来源 | 表示通过 URL 引用的多媒体数据来源，仅含 url 字段 |
| ChatResponse | 聊天响应 | 模型返回的响应对象，继承 `DictMixin`，支持属性式访问 |
| ChatUsage | 聊天用量 | Token 消耗统计对象，记录输入/输出 Token 数 |
| ToolResponse | 工具响应 | 工具函数返回值的封装对象 |

---

## 多 Agent 编排术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| MsgHub | 消息枢纽 | 发布-订阅模式的消息广播中心，实现多 Agent 间的消息共享 |
| Broadcast | 广播 | MsgHub 将消息推送给所有订阅者的机制 |
| Publish-Subscribe | 发布-订阅 | 消息生产者与消费者解耦的通信模式，MsgHub 的核心架构 |
| ChatRoom | 聊天室 | 基于 MsgHub 的多 Agent 交互空间，Agent 可自由广播和接收消息 |
| SequentialPipeline | 顺序流水线 | 按顺序将消息依次传递给一系列 Agent 的编排模式 |
| FanoutPipeline | 扇出流水线 | 将同一消息并发发送给多个 Agent，收集所有结果 |

---

## Token 与截断术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Token Truncation | Token 截断 | 当消息超出模型上下文窗口时，按策略截断历史消息 |
| Token Counting | Token 计数 | 估算消息消耗的 Token 数量，用于截断决策和成本控制 |
| TokenCounterBase | Token 计数器基类 | 定义 Token 计数接口的抽象基类 |
| TruncatedFormatterBase | 截断格式化器基类 | 在格式化基础上增加 Token 截断能力的格式化器基类 |

---

## 可观测性与追踪术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Tracing | 链路追踪 | 基于 OpenTelemetry 的分布式追踪，记录请求在各组件间的流转 |
| Span | 追踪跨度 | 一次操作的时间跨度记录，包含开始时间、属性和状态 |
| Observability | 可观测性 | 通过 Tracing、Metrics、Logging 了解系统运行状态的能力 |
| Streaming | 流式输出 | 模型响应逐步返回而非一次性返回的模式，使用 AsyncGenerator 实现 |
| AsyncGenerator | 异步生成器 | 使用 `async def` + `yield` 定义的异步迭代器，用于流式场景 |
| SpanAttributes | 追踪属性 | Span 中结构化的键值对属性，记录操作细节（模型名、Token 数等） |

---

## 协议与集成术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| MCP | 模型上下文协议 | Model Context Protocol，标准化工具和资源提供给模型的方式 |
| A2A | 智能体间协议 | Agent-to-Agent protocol，标准化不同 Agent 框架间互操作的协议 |
| AgentCardResolver | Agent 卡片解析器 | A2A 协议中解析 Agent 元数据（Agent Card）的组件 |
| MCPClientBase | MCP 客户端基类 | MCP 客户端的抽象接口 |
| StatefulClientBase | 有状态客户端基类 | 维护连接状态的 MCP 客户端基类 |
| MCPToolFunction | MCP 工具函数 | MCP 协议中工具的函数封装 |
| AgentSkill | Agent 技能 | A2A 协议中描述 Agent 能力的 TypedDict |

---

## RAG 与嵌入术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| RAG | 检索增强生成 | Retrieval-Augmented Generation，通过检索外部知识增强模型回答质量 |
| Embedding | 嵌入/向量化 | 将文本或其他数据转换为高维向量表示，用于语义检索和相似度计算 |
| KnowledgeBase | 知识库 | RAG 模块中管理文档和向量检索的高层接口 |
| VDBStoreBase | 向量数据库基类 | RAG 模块中向量存储的抽象接口 |
| Document | 文档 | RAG 中的文档对象，包含内容和元数据 |
| DocMetadata | 文档元数据 | 文档的元信息，继承 `DictMixin` |

---

## Hook 与元类术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| Hook | 钩子 | 在 Agent 方法执行前后自动触发的回调函数，由元类 `_AgentMeta` 管理 |
| Middleware | 中间件 | 包装工具函数的可组合处理器链，用于日志、追踪、权限等横切关注点 |
| Metaclass | 元类 | 控制类创建过程的机制，AgentScope 用 `_AgentMeta` 自动注入 Hook |
| AgentHookTypes | Agent 钩子类型 | 定义 Agent 可用 Hook 点的字面量联合类型：pre_reply、post_reply 等 |
| ReActAgentHookTypes | ReAct 钩子类型 | 在 AgentHookTypes 基础上增加 pre_reasoning、pre_acting 等 |

---

## 工具系统术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| RegisteredToolFunction | 已注册工具函数 | Toolkit 中注册的工具函数元数据封装 |
| ToolGroup | 工具组 | 按功能分组管理工具的容器，支持工具的按需加载 |
| ToolFunction | 工具函数类型 | 工具函数签名的联合类型定义，支持同步/异步/生成器多种返回形式 |

---

## 语音交互术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| RealtimeAgent | 实时代理 | 支持语音实时交互的 Agent，继承 `StateModule` |
| RealtimeModelBase | 实时模型基类 | 定义实时语音交互接口的抽象基类 |
| TTSModelBase | 语音合成基类 | Text-to-Speech 模型的抽象基类 |
| TTSResponse | TTS 响应 | 语音合成返回的响应对象 |
| ClientEvent | 客户端事件 | 实时交互中客户端发送的事件类型枚举 |
| ServerEvent | 服务端事件 | 实时交互中服务端推送的事件类型枚举 |
| ModelEvent | 模型事件 | 实时交互中模型产生的事件类型枚举 |

---

## 评估术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| BenchmarkBase | 基准测试基类 | 评估模块中基准测试的抽象基类 |
| MetricBase | 指标基类 | 评估指标的抽象基类 |
| MetricResult | 指标结果 | 评估指标的计算结果，继承 `DictMixin` |
| EvaluatorBase | 评估器基类 | 评估执行器的抽象基类 |
| Task | 评估任务 | 评估中的任务定义 |
| SolutionOutput | 解决方案输出 | Agent 解决方案的输出封装 |
| ACEBenchmark | ACE 基准 | Agent Capability Evaluation 基准测试实现 |

---

## Python 模式术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| TypedDict | 类型化字典 | Python 类型提示中的结构化字典类型，运行时仍是 dict，用于消息块定义 |
| ContextVar | 上下文变量 | `contextvars.ContextVar`，异步安全的线程局部存储，每个协程独立副本 |
| DictMixin | 字典混入 | 允许用属性语法（`.`）访问字典键值的混入类 |
| Strategy Pattern | 策略模式 | 运行时选择不同算法的模式，如 TokenCounterBase 的不同计数策略 |
| Factory Pattern | 工厂模式 | 根据配置创建对象的模式，如 `ChatModelBase` 通过配置自动选择模型适配器 |
| Middleware Pattern | 中间件模式 | 将处理逻辑组织为可组合的链，如 Toolkit 的中间件用于包装工具函数 |
| Deepcopy | 深拷贝 | 递归复制所有层级的对象副本，AgentScope 在 Hook 中用于状态隔离 |
| Decorator | 装饰器 | 包装函数以增强行为的模式，如 `@wraps` 保留元信息、`@trace` 注入追踪 |

---

## 模型适配器术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| OpenAIChatModel | OpenAI 模型适配器 | 支持 OpenAI 及其兼容 API 的模型适配器，继承 `ChatModelBase` |
| AnthropicChatModel | Anthropic 模型适配器 | 支持 Claude 系列模型的适配器，处理 extended thinking 等 |
| DashScopeChatModel | DashScope 模型适配器 | 支持阿里云通义系列模型的适配器 |
| GeminiChatModel | Gemini 模型适配器 | 支持 Google Gemini 系列模型的适配器 |
| OllamaChatModel | Ollama 模型适配器 | 支持本地 Ollama 模型的适配器 |
| TrinityChatModel | Trinity 模型适配器 | 基于 OpenAI 兼容协议的模型适配器，继承 `OpenAIChatModel` |

---

## 格式化器术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| FormatterBase | 格式化器基类 | 定义 `format()` 接口的抽象基类，将 Msg 转为模型 API 格式 |
| OpenAIChatFormatter | OpenAI 格式化器 | 将 Msg 转为 OpenAI Chat Completions API 的请求格式 |
| AnthropicChatFormatter | Anthropic 格式化器 | 将 Msg 转为 Anthropic Messages API 的请求格式 |
| DashScopeChatFormatter | DashScope 格式化器 | 将 Msg 转为 DashScope API 的请求格式 |
| GeminiChatFormatter | Gemini 格式化器 | 将 Msg 转为 Gemini API 的请求格式 |
| OllamaChatFormatter | Ollama 格式化器 | 将 Msg 转为 Ollama API 的请求格式 |
| A2AChatFormatter | A2A 格式化器 | 将 Msg 转为 A2A 协议消息格式 |
| MultiAgentFormatter | 多 Agent 格式化器 | 在标准格式基础上增加多 Agent 上下文信息的格式化器变体 |

---

## 异常术语

| 英文 | 中文 | 简要定义 |
|------|------|----------|
| AgentOrientedExceptionBase | Agent 导向异常基类 | AgentScope 所有自定义异常的基类，继承自 `Exception` |
| ToolNotFoundError | 工具未找到异常 | 模型请求调用的工具不在 Toolkit 注册列表中时抛出 |
| ToolInterruptedError | 工具中断异常 | 工具执行被外部中断时抛出 |
| ToolInvalidArgumentsError | 工具参数无效异常 | 工具调用参数不符合函数签名时抛出 |
