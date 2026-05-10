# 附录 C：源码地图

AgentScope 源码按模块组织，本附录为每个模块提供文件清单、核心类/函数索引。

源码根目录：`src/agentscope/`

---

## 顶层文件

| 文件 | 说明 | 关键内容 |
|------|------|----------|
| `__init__.py` | 包入口，重导出所有子模块 | `ContextVar` 定义、子模块 import |
| `_logging.py` | 日志配置 | `setup_logger()` |
| `_run_config.py` | 运行配置管理 | `_ConfigCls` 配置类工厂 |
| `_version.py` | 版本号 | `__version__` |

---

## agent/ — 智能体

Agent 核心实现，包含基类、元类、具体 Agent 类型。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_agent_base.py` | Agent 抽象基类 | `AgentBase(StateModule)` — 定义 `reply()`、`observe()`、`print()` |
| `_agent_meta.py` | Agent 元类 | `_AgentMeta(type)` — 自动注入 Hook；`_ReActAgentMeta` — 扩展至 reasoning/acting；`_wrap_with_hooks()` — Hook 包装器 |
| `_react_agent_base.py` | ReAct Agent 抽象基类 | `ReActAgentBase(AgentBase)` — 定义 ReAct 循环骨架 |
| `_react_agent.py` | ReAct Agent 实现 | `ReActAgent(ReActAgentBase)` — 完整的 ReAct 推理循环；`_QueryRewriteModel`、`SummarySchema` — Pydantic 辅助模型；`_MemoryMark` — 枚举标记 |
| `_user_agent.py` | 用户代理 | `UserAgent(AgentBase)` — 处理人工输入 |
| `_a2a_agent.py` | A2A 协议代理 | `A2AAgent(AgentBase)` — Agent-to-Agent 协议交互 |
| `_realtime_agent.py` | 实时语音代理 | `RealtimeAgent(StateModule)` — 语音交互 |
| `_user_input.py` | 用户输入工具 | 用户输入获取辅助函数 |
| `_utils.py` | 工具函数 | `_AsyncNullContext` — 异步空上下文管理器 |

---

## model/ — 模型适配器

统一封装不同 LLM 提供商的 API 调用。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_model_base.py` | 模型抽象基类 | `ChatModelBase` — 定义 `__call__()`、`format()` 接口 |
| `_openai_model.py` | OpenAI 适配器 | `OpenAIChatModel(ChatModelBase)` — 支持 OpenAI 及兼容 API |
| `_anthropic_model.py` | Anthropic 适配器 | `AnthropicChatModel(ChatModelBase)` — Claude 系列模型 |
| `_dashscope_model.py` | DashScope 适配器 | `DashScopeChatModel(ChatModelBase)` — 阿里云通义系列 |
| `_gemini_model.py` | Gemini 适配器 | `GeminiChatModel(ChatModelBase)` — Google Gemini 系列 |
| `_ollama_model.py` | Ollama 适配器 | `OllamaChatModel(ChatModelBase)` — 本地 Ollama 模型 |
| `_trinity_model.py` | Trinity 适配器 | `TrinityChatModel(OpenAIChatModel)` — 基于 OpenAI 兼容协议 |
| `_model_response.py` | 模型响应 | `ChatResponse(DictMixin)` — 模型返回的响应对象 |
| `_model_usage.py` | Token 用量 | `ChatUsage(DictMixin)` — Token 消耗统计 |

---

## formatter/ — 消息格式化

将 `Msg` 转换为各模型 API 所需的请求格式。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_formatter_base.py` | 格式化器基类 | `FormatterBase` — 定义 `format()` 抽象接口 |
| `_truncated_formatter_base.py` | 截断格式化器基类 | `TruncatedFormatterBase(FormatterBase, ABC)` — 增加 Token 截断能力 |
| `_openai_formatter.py` | OpenAI 格式化 | `OpenAIChatFormatter`、`OpenAIMultiAgentFormatter` — 标准 + 多 Agent 格式 |
| `_anthropic_formatter.py` | Anthropic 格式化 | `AnthropicChatFormatter`、`AnthropicMultiAgentFormatter` |
| `_dashscope_formatter.py` | DashScope 格式化 | `DashScopeChatFormatter`、`DashScopeMultiAgentFormatter` |
| `_gemini_formatter.py` | Gemini 格式化 | `GeminiChatFormatter`、`GeminiMultiAgentFormatter` |
| `_ollama_formatter.py` | Ollama 格式化 | `OllamaChatFormatter`、`OllamaMultiAgentFormatter` |
| `_deepseek_formatter.py` | DeepSeek 格式化 | `DeepSeekChatFormatter`、`DeepSeekMultiAgentFormatter` |
| `_a2a_formatter.py` | A2A 格式化 | `A2AChatFormatter(FormatterBase)` — A2A 协议消息格式 |

---

## message/ — 消息类型

定义核心消息结构和内容块类型。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_message_base.py` | 消息基类 | `Msg` — 核心消息类，包含 `name`、`role`、`content`、`url`、`metadata` |
| `_message_block.py` | 内容块定义 | `TextBlock`、`ThinkingBlock`、`ImageBlock`、`AudioBlock`、`VideoBlock`、`ToolUseBlock`、`ToolResultBlock`、`Base64Source`、`URLSource`；联合类型 `ContentBlock` |

所有内容块均使用 `TypedDict` 定义，`total=False` + `Required[]` 标记必需字段。

---

## tool/ — 工具系统

工具注册、管理和执行。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_toolkit.py` | 工具箱 | `Toolkit(StateModule)` — 核心容器；`_apply_middlewares()` — 中间件链组装 |
| `_types.py` | 工具类型 | `RegisteredToolFunction` — 工具函数元数据；`ToolGroup` — 工具分组；`AgentSkill(TypedDict)` — A2A 技能描述 |
| `_response.py` | 工具响应 | `ToolResponse` — 工具函数返回值封装 |
| `_async_wrapper.py` | 异步包装器 | `_postprocess_tool_response()`、`_sync_generator_wrapper()`、`_async_generator_wrapper()` — 统一同步/异步工具调用 |

### tool/_coding/ — 代码执行工具

| 文件 | 说明 | 关键函数 |
|------|------|----------|
| `_shell.py` | Shell 命令执行 | `execute_shell_command()` |
| `_python.py` | Python 代码执行 | `execute_python_code()` |

### tool/_text_file/ — 文本文件工具

| 文件 | 说明 | 关键函数 |
|------|------|----------|
| `_view_text_file.py` | 文件查看 | `view_text_file()` |
| `_write_text_file.py` | 文件写入 | `write_text_file()`、`insert_text_file()` |

### tool/_multi_modality/ — 多模态工具

| 文件 | 说明 | 关键函数 |
|------|------|----------|
| `_openai_tools.py` | OpenAI 多模态 | `openai_text_to_image()`、`openai_image_to_text()`、`openai_text_to_audio()`、`openai_audio_to_text()`、`openai_edit_image()`、`openai_create_image_variation()` |
| `_dashscope_tools.py` | DashScope 多模态 | `dashscope_text_to_image()`、`dashscope_image_to_text()`、`dashscope_text_to_audio()` |

---

## memory/ — 记忆系统

### memory/_working_memory/ — 工作记忆

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_base.py` | 记忆基类 | `MemoryBase(StateModule)` — 定义 `add()`、`get()`、`clear()` 接口 |
| `_in_memory_memory.py` | 内存记忆 | `InMemoryMemory(MemoryBase)` — 基于列表的内存存储 |
| `_redis_memory.py` | Redis 记忆 | `RedisMemory(MemoryBase)` — 基于 Redis 的持久化存储 |
| `_sqlalchemy_memory.py` | SQLAlchemy 记忆 | `AsyncSQLAlchemyMemory(MemoryBase)` — 基于数据库的异步存储 |
| `_tablestore_memory.py` | Tablestore 记忆 | `TablestoreMemory(MemoryBase)` — 阿里云 Tablestore 存储 |

### memory/_long_term_memory/ — 长期记忆

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_long_term_memory_base.py` | 长期记忆基类 | `LongTermMemoryBase(StateModule)` |
| `_mem0/_mem0_long_term_memory.py` | Mem0 长期记忆 | `Mem0LongTermMemory` — 基于 Mem0 的长期记忆实现 |
| `_reme/_reme_long_term_memory_base.py` | ReMe 基类 | `ReMeLongTermMemoryBase(LongTermMemoryBase, ABC)` — ReMe 抽象基类 |
| `_reme/_reme_personal_long_term_memory.py` | ReMe 个人记忆 | `ReMePersonalLongTermMemory` — 个人偏好长期记忆 |
| `_reme/_reme_task_long_term_memory.py` | ReMe 任务记忆 | `ReMeTaskLongTermMemory` — 任务相关长期记忆 |
| `_reme/_reme_tool_long_term_memory.py` | ReMe 工具记忆 | `ReMeToolLongTermMemory` — 工具使用经验长期记忆 |

---

## pipeline/ — 多 Agent 编排

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_class.py` | 流水线类 | `SequentialPipeline` — 顺序执行；`FanoutPipeline` — 并发执行 |
| `_functional.py` | 函数式流水线 | `sequential_pipeline()`、`fanout_pipeline()`、`stream_printing_messages()` — 异步函数式接口 |
| `_msghub.py` | 消息枢纽 | `MsgHub` — 发布-订阅消息广播中心 |
| `_chat_room.py` | 聊天室 | `ChatRoom` — 基于 MsgHub 的多 Agent 交互空间 |

---

## plan/ — 计划系统

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_plan_model.py` | 计划数据模型 | `SubTask(BaseModel)` — 子任务；`Plan(BaseModel)` — 计划（含子任务列表） |
| `_plan_notebook.py` | 计划笔记本 | `PlanNotebook(StateModule)` — 计划管理与追踪；`DefaultPlanToHint` — 计划到提示词的转换器 |
| `_storage_base.py` | 存储基类 | `PlanStorageBase(StateModule)` — 计划持久化接口 |
| `_in_memory_storage.py` | 内存存储 | `InMemoryPlanStorage(PlanStorageBase)` — 基于内存的计划存储 |

---

## module/ — 状态模块

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_state_module.py` | 状态模块基类 | `StateModule` — 嵌套状态序列化/反序列化核心；`_JSONSerializeFunction` — 序列化函数对 |

`StateModule` 是 AgentBase、Toolkit、PlanNotebook 等核心类的共同基类，
提供 `state_dict()` / `load_state_dict()` / `register_state()` 接口。

---

## embedding/ — 嵌入模型

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_embedding_base.py` | 嵌入基类 | `EmbeddingModelBase` — 定义嵌入接口 |
| `_openai_embedding.py` | OpenAI 嵌入 | `OpenAITextEmbedding` |
| `_dashscope_embedding.py` | DashScope 嵌入 | `DashScopeTextEmbedding` |
| `_dashscope_multimodal_embedding.py` | DashScope 多模态嵌入 | `DashScopeMultiModalEmbedding` |
| `_gemini_embedding.py` | Gemini 嵌入 | `GeminiTextEmbedding` |
| `_ollama_embedding.py` | Ollama 嵌入 | `OllamaTextEmbedding` |
| `_cache_base.py` | 缓存基类 | `EmbeddingCacheBase` |
| `_file_cache.py` | 文件缓存 | `FileEmbeddingCache` |
| `_embedding_response.py` | 嵌入响应 | `EmbeddingResponse(DictMixin)` |
| `_embedding_usage.py` | 嵌入用量 | `EmbeddingUsage(DictMixin)` |

---

## token/ — Token 计数

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_token_base.py` | 计数器基类 | `TokenCounterBase` — 定义 `count_tokens()` 接口 |
| `_openai_token_counter.py` | OpenAI 计数 | `OpenAITokenCounter` — 含图片/工具 Token 估算 |
| `_anthropic_token_counter.py` | Anthropic 计数 | `AnthropicTokenCounter` |
| `_gemini_token_counter.py` | Gemini 计数 | `GeminiTokenCounter` |
| `_huggingface_token_counter.py` | HuggingFace 计数 | `HuggingFaceTokenCounter` — 基于 tokenizer 的精确计数 |
| `_char_token_counter.py` | 字符计数 | `CharTokenCounter` — 基于字符数的粗略估算 |

---

## session/ — 会话管理

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_session_base.py` | 会话基类 | `SessionBase` — 定义 `save()`、`load()` 接口 |
| `_json_session.py` | JSON 会话 | `JSONSession(SessionBase)` — 本地 JSON 文件存储 |
| `_redis_session.py` | Redis 会话 | `RedisSession(SessionBase)` — Redis 键值存储 |
| `_tablestore_session.py` | Tablestore 会话 | `TablestoreSession(SessionBase)` — 阿里云 Tablestore |

---

## rag/ — 检索增强生成

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_knowledge_base.py` | 知识库基类 | `KnowledgeBase` — 管理文档和检索的高层接口 |
| `_simple_knowledge.py` | 简单知识库 | `SimpleKnowledge(KnowledgeBase)` |
| `_document.py` | 文档类型 | `Document` — 文档对象；`DocMetadata(DictMixin)` — 文档元数据 |

### rag/_store/ — 向量存储

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_store_base.py` | 存储基类 | `VDBStoreBase` — 向量数据库抽象接口 |
| `_milvuslite_store.py` | Milvus Lite | `MilvusLiteStore` |
| `_qdrant_store.py` | Qdrant | `QdrantStore` |
| `_mongodb_store.py` | MongoDB | `MongoDBStore` |
| `_oceanbase_store.py` | OceanBase | `OceanBaseStore` |
| `_alibabacloud_mysql_store.py` | 阿里云 MySQL | `AlibabaCloudMySQLStore` |

### rag/_reader/ — 文档读取器

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_reader_base.py` | 读取器基类 | `ReaderBase` |
| `_text_reader.py` | 文本读取 | `TextReader` |
| `_pdf_reader.py` | PDF 读取 | `PDFReader` |
| `_word_reader.py` | Word 读取 | `WordReader` |
| `_ppt_reader.py` | PPT 读取 | `PowerPointReader` |
| `_excel_reader.py` | Excel 读取 | `ExcelReader` |
| `_image_reader.py` | 图片读取 | `ImageReader` |
| `_utils.py` | 读取工具 | `_table_to_json()`、`_table_to_markdown()`、`_get_media_type_from_data()` |

---

## a2a/ — Agent-to-Agent 协议

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_base.py` | 解析器基类 | `AgentCardResolverBase` — Agent 卡片解析抽象接口 |
| `_well_known_resolver.py` | Well-Known 解析 | `WellKnownAgentCardResolver` — 通过 `.well-known` 路径解析 |
| `_file_resolver.py` | 文件解析 | `FileAgentCardResolver` — 从本地文件解析 |
| `_nacos_resolver.py` | Nacos 解析 | `NacosAgentCardResolver` — 通过 Nacos 服务发现解析 |

---

## mcp/ — 模型上下文协议

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_client_base.py` | 客户端基类 | `MCPClientBase` — MCP 客户端抽象接口 |
| `_stateful_client_base.py` | 有状态客户端基类 | `StatefulClientBase(MCPClientBase, ABC)` |
| `_http_stateful_client.py` | HTTP 有状态客户端 | `HttpStatefulClient` — HTTP 长连接有状态 MCP 客户端 |
| `_http_stateless_client.py` | HTTP 无状态客户端 | `HttpStatelessClient` — HTTP 请求级无状态 MCP 客户端 |
| `_stdio_stateful_client.py` | Stdio 有状态客户端 | `StdIOStatefulClient` — 标准输入输出 MCP 客户端 |
| `_mcp_function.py` | MCP 工具函数 | `MCPToolFunction` — MCP 工具的函数封装 |

---

## realtime/ — 实时语音交互

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_base.py` | 实时模型基类 | `RealtimeModelBase` — 定义实时语音交互接口 |
| `_openai_realtime_model.py` | OpenAI 实时 | `OpenAIRealtimeModel` |
| `_gemini_realtime_model.py` | Gemini 实时 | `GeminiRealtimeModel` |
| `_dashscope_realtime_model.py` | DashScope 实时 | `DashScopeRealtimeModel` |

### realtime/_events/ — 实时事件

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_client_event.py` | 客户端事件 | `ClientEventType(Enum)`、`ClientEvents` |
| `_server_event.py` | 服务端事件 | `ServerEventType(Enum)`、`ServerEvents` |
| `_model_event.py` | 模型事件 | `ModelEventType(Enum)`、`ModelEvents` |
| `_utils.py` | 事件工具 | `AudioFormat(BaseModel)` |

---

## tracing/ — 链路追踪

基于 OpenTelemetry 的分布式追踪系统。

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_trace.py` | 追踪装饰器 | `trace()` — Agent 追踪；`trace_toolkit()` — 工具追踪；`trace_reply()` — 回复追踪；`trace_llm()` — LLM 追踪；`trace_format()` — 格式化追踪；`trace_embedding()` — 嵌入追踪 |
| `_setup.py` | 追踪配置 | `setup_tracing()` — 初始化 OpenTelemetry；`_get_tracer()` |
| `_attributes.py` | 追踪属性 | `SpanAttributes` — Span 属性常量类；`OperationNameValues`、`ProviderNameValues` |
| `_converter.py` | 内容转换 | `_convert_block_to_part()` — 将 ContentBlock 转为追踪可序列化格式 |
| `_extractor.py` | 属性提取 | 各组件的请求/响应属性提取函数（`_get_llm_request_attributes()` 等） |
| `_utils.py` | 工具函数 | `_to_serializable()`、`_serialize_to_str()` |

---

## tts/ — 语音合成

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_tts_base.py` | TTS 基类 | `TTSModelBase(ABC)` |
| `_openai_tts_model.py` | OpenAI TTS | `OpenAITTSModel` |
| `_gemini_tts_model.py` | Gemini TTS | `GeminiTTSModel` |
| `_dashscope_tts_model.py` | DashScope TTS | `DashScopeTTSModel` |
| `_dashscope_cosyvoice_tts_model.py` | CosyVoice TTS | `DashScopeCosyVoiceTTSModel` |
| `_dashscope_realtime_tts_model.py` | DashScope 实时 TTS | `DashScopeRealtimeTTSModel` |
| `_dashscope_cosyvoice_realtime_tts_model.py` | CosyVoice 实时 TTS | `DashScopeCosyVoiceRealtimeTTSModel` |
| `_tts_response.py` | TTS 响应 | `TTSResponse(DictMixin)`、`TTSUsage(DictMixin)` |
| `_utils.py` | TTS 工具 | `_get_cosyvoice_callback_class()` |

---

## hooks/ — 钩子

| 文件 | 说明 | 关键函数 |
|------|------|----------|
| `_studio_hooks.py` | Studio 钩子 | `as_studio_forward_message_pre_print_hook()` — Studio UI 消息打印钩子 |

---

## types/ — 类型定义

| 文件 | 说明 | 关键内容 |
|------|------|----------|
| `_hook.py` | Hook 类型 | `AgentHookTypes`、`ReActAgentHookTypes` — Hook 类型字面量联合 |
| `_tool.py` | 工具类型 | `ToolFunction` — 工具函数签名联合类型 |
| `_object.py` | 对象类型 | 通用对象类型别名 |
| `_json.py` | JSON 类型 | JSON 序列化相关类型 |

---

## evaluate/ — 评估与基准

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_benchmark_base.py` | 基准基类 | `BenchmarkBase(ABC)` |
| `_metric_base.py` | 指标基类 | `MetricBase(ABC)`、`MetricResult(DictMixin)`、`MetricType(Enum)` |
| `_task.py` | 评估任务 | `Task` |
| `_solution.py` | 解决方案 | `SolutionOutput(DictMixin)` |
| `_evaluator/_evaluator_base.py` | 评估器基类 | `EvaluatorBase` |
| `_evaluator/_general_evaluator.py` | 通用评估器 | `GeneralEvaluator` |
| `_evaluator/_ray_evaluator.py` | Ray 分布式评估 | `RayEvaluator`、`RayEvaluationActor`、`RaySolutionActor` |
| `_evaluator_storage/_evaluator_storage_base.py` | 存储基类 | `EvaluatorStorageBase` |
| `_evaluator_storage/_file_evaluator_storage.py` | 文件存储 | `FileEvaluatorStorage` |
| `_ace_benchmark/_ace_benchmark.py` | ACE 基准 | `ACEBenchmark(BenchmarkBase)` |
| `_ace_benchmark/_ace_metric.py` | ACE 指标 | `ACEProcessAccuracy`、`ACEAccuracy` |

---

## tuner/ — 模型调优

| 文件 | 说明 | 关键类/函数 |
|------|------|-------------|
| `_tune.py` | 调优入口 | `tune()` |
| `_config.py` | 配置管理 | `_load_config_from_path_or_default()`、`check_workflow_function()`、`check_judge_function()` |
| `_algorithm.py` | 算法配置 | `AlgorithmConfig(BaseModel)` |
| `_dataset.py` | 数据集配置 | `DatasetConfig(BaseModel)` |
| `_model.py` | 模型配置 | `TunerModelConfig(BaseModel)`、`TinkerConfig(BaseModel)` |
| `_judge.py` | 评估函数 | `JudgeOutput(BaseModel)` |
| `_workflow.py` | 工作流 | `WorkflowOutput(BaseModel)` |
| `prompt_tune/_tune_prompt.py` | 提示调优 | `tune_prompt()` |
| `model_selection/_model_selection.py` | 模型选择 | `select_model()` — 异步模型选择 |
| `model_selection/_built_in_judges.py` | 内置评估 | `avg_time_judge()`、`avg_token_consumption_judge()` |

---

## exception/ — 异常定义

| 文件 | 说明 | 关键类 |
|------|------|--------|
| `_exception_base.py` | 异常基类 | `AgentOrientedExceptionBase(Exception)` |
| `_tool.py` | 工具异常 | `ToolNotFoundError`、`ToolInterruptedError`、`ToolInvalidArgumentsError` |

---

## _utils/ — 内部工具

| 文件 | 说明 | 关键函数 |
|------|------|----------|
| `_common.py` | 通用工具 | `_json_loads_with_repair()` — JSON 修复解析；`_parse_streaming_json_dict()` — 流式 JSON 解析；`_is_async_func()` — 异步检测；`_execute_async_or_sync_func()` — 统一执行；`_parse_tool_function()` — 工具函数解析；`_create_tool_from_base_model()` — Pydantic 模型转工具 |
| `_mixin.py` | 混入类 | `DictMixin(dict)` — 属性式字典访问 |
