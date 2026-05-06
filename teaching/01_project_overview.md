# 第一章：AgentScope 项目概述

## 学习目标

> 学完本节，你将能够：
> - [L1 记忆] 列举 AgentScope 的核心特性和 Agent 类型
> - [L2 理解] 解释 AgentScope 与 Java/Spring 生态的对应关系
> - [L3 应用] 使用正确的参数创建 ReActAgent

**预计时间**：10 分钟
**先修要求**：了解 Java 面向对象编程

## 1.1 什么是 AgentScope

AgentScope 是一个**生产级的多智能体框架（Multi-Agent Framework）**，用于构建基于大语言模型（LLM）的应用程序。当前版本 **v1.0.19**，2.0 版本正在开发中。

```
┌─────────────────────────────────────────────────────────────┐
│                      AgentScope                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │
│  │ Agent 1│  │ Agent 2│  │ Agent 3│  │ Agent N│  ...    │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘        │
│       │            │            │            │              │
│       └────────────┴─────┬──────┴────────────┘              │
│                          │                                   │
│                    ┌─────▼─────┐                            │
│                    │  MsgHub   │  (消息中心)                 │
│                    └─────┬─────┘                            │
│                          │                                   │
│              ┌───────────┼───────────┐                      │
│              │           │           │                       │
│        ┌─────▼───┐ ┌─────▼───┐ ┌─────▼───┐                  │
│        │  Model  │ │  Tools  │ │ Memory  │                  │
│        │ (LLM)   │ │ (函数)  │ │ (记忆)  │                  │
│        └─────────┘ └─────────┘ └─────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### 核心特性

| 特性 | 说明 |
|------|------|
| **多智能体编排** | 支持 SequentialPipeline、FanoutPipeline、MsgHub 等协同模式 |
| **多模型支持** | OpenAI、Anthropic、Gemini、DashScope、Ollama、DeepSeek |
| **工具调用** | Python 执行、Shell 命令、MCP 协议、Function Calling |
| **记忆系统** | 短期记忆（InMemory/Redis/SQL）、长期记忆（Mem0/ReMe） |
| **RAG** | PDF/Word/Excel 文档解析，向量存储（Qdrant/Milvus） |
| **实时语音** | TTS 集成（DashScope/OpenAI/德国 TTS），WebSocket 实时对话 |
| **可观测性** | OpenTelemetry 链路追踪，AgentScope Studio 可视化调试 |
| **深度研究** | ReActAgent 配合搜索工具实现研究模式（DeepResearchAgent 见 examples/） |
| **A2A 通信** | Agent-to-Agent 协议支持多智能体间通信 |

### Agent 类型扩展

```
Agent 类型
├── ReActAgent         # 核心推理 Agent（不配 toolkit 时为纯对话模式）
├── UserAgent          # 用户代理（终端/Studio 交互）
├── A2AAgent           # Agent-to-Agent 协议通信
└── RealtimeAgent      # 实时语音/视频 Agent
```

## 1.2 应用场景

```
场景示例                    对比 Java 世界
─────────────────────────────────────────────────────
┌─────────────────┐        ┌─────────────────┐
│ 智能客服机器人   │   →    │ Spring Bot      │
├─────────────────┤        ├─────────────────┤
│ 代码审查助手     │   →    │ Jenkins Plugin  │
├─────────────────┤        ├─────────────────┤
│ 数据分析助手     │   →    │ ETL Pipeline    │
├─────────────────┤        ├─────────────────┤
│ 会议记录助手     │   →    │ Documentum      │
├─────────────────┤        ├─────────────────┤
│ 智能搜索问答     │   →    │ Elasticsearch   │
├─────────────────┤        ├─────────────────┤
│ 深度研究报告生成 │   →    │ Report Engine   │
├─────────────────┤        ├─────────────────┤
│ 实时语音助手     │   →    │ WebSocket 语音  │
└─────────────────┘        └─────────────────┘
```

### 热门应用场景详解

**1. 智能客服与对话系统**
- 多 Agent 协作处理复杂查询
- Routing 自动分类路由到不同专家 Agent
- 记忆系统保持对话上下文

**2. 深度研究助手**
- ReActAgent 配合搜索工具（如 Tavily）自动搜索、提取、整合信息
- DeepResearchAgent 是 examples/ 中的示例模式，基于 ReActAgent + 搜索工具实现
- 生成结构化研究报告

**3. 实时语音交互**
- TTS 集成实现语音输出
- 支持流式响应降低延迟
- 适用于语音助手和实时对话场景

**4. 代码开发助手**
- Python/Shell 代码执行
- 多 Agent 辩论验证代码逻辑
- RAG 整合项目文档

## 1.3 与竞品对比

| 特性 | AgentScope | LangChain | AutoGen | crewai |
|------|------------|-----------|---------|--------|
| **多智能体模式** | Sequential/Fanout Pipeline | Pipeline | GroupChat | Crew |
| **语音支持** | 原生 TTS 集成 | 第三方 | 第三方 | 第三方 |
| **记忆系统** | HybridMemory (短+长期) | Memory | AgentChat | Memory |
| **RAG** | 内置支持 | 第三方 | 第三方 | 第三方 |
| **Studio 可视化** | 原生支持 | LangSmith | Playwright | 社区工具 |
| **Java 开发者友好** | 专门的中文对比文档 | 英文为主 | 英文为主 | 英文为主 |
| **最新版本** | v1.0.19 | v0.3.x | v0.4.x | v0.28+ |

### AgentScope 优势

1. **开箱即用的多智能体编排** - 无需复杂配置即可实现 Routing、Supervisor 等模式
2. **Voice Agent 优先** - 2.0 全力投入语音交互，是少有的语音优先框架
3. **中文友好** - 文档完善，适合 Java 开发者快速上手
4. **可观测性** - Studio 提供直观的调试和监控界面

## 1.4 技术栈

### 语言 & 运行时
- **Python 3.10+** ← 主要开发语言
- Java 开发者需要熟悉 Python 语法

### 核心依赖

| 类别 | 库 | Java 类比 |
|------|-----|-----------|
| **LLM API** | openai, anthropic | HTTP Client (OkHttp/RestTemplate) |
| **实时通信** | python-socketio | WebSocket (Spring WebFlux) |
| **协议** | mcp (Model Context Protocol) | gRPC / Protobuf |
| **链路追踪** | opentelemetry-sdk | Micrometer/Jaeger |
| **数据库** | sqlalchemy, redis | JPA/Hibernate, Redis |
| **向量数据库** | qdrant-client, pymilvus | Lucene / Elasticsearch |

### 项目结构

```
agentscope/
├── src/agentscope/          # ← 核心源码 (类似 src/main/java)
│   ├── agent/               # Agent 实现 (类似 @Service)
│   ├── model/               # LLM 模型封装 (类似 @Repository)
│   ├── tool/                # 工具函数 (类似 Util)
│   ├── memory/              # 记忆存储 (类似 Cache)
│   ├── pipeline/            # 流程编排 (类似 Orchestration)
│   └── ...
├── examples/                # ← 示例代码 (类似 spring-boot-sample)
├── tests/                   # ← 单元测试 (类似 src/test/java)
└── docs/                    # 官方文档
```

## 1.5 与 Java 生态对比

| AgentScope 概念 | Java/Spring 等价物 | 备注 |
|----------------|-------------------|------|
| `agentscope.init()` | `@SpringBootApplication` | 框架初始化 |
| `ReActAgent` | `@Service` + 业务逻辑 | Agent 是带 LLM 能力的 Service |
| `Model` | `Repository` | 数据访问抽象 |
| `Toolkit` | `@Bean` / Utility | 可复用的工具组件 |
| `MsgHub` | EventBus / Kafka | 消息分发中心 |
| `Memory` | Cache (Redis/Caffeine) | 临时状态存储 |
| `Pipeline` | Workflow Engine | 流程编排 |
| `SequentialPipeline` | Router / Dispatcher | 顺序执行流程 |
| `FanoutPipeline` | Parallel Execution | 并行执行分发 |

## 1.6 快速概念验证

AgentScope 的核心使用模式：

```python showLineNumbers
# Step 1: 初始化 (类似 Spring @PostConstruct)
import agentscope

agentscope.init(project="my-agent")  # 注意: 参数名是 project, 不是 project_name

# Step 2: 创建 Agent (类似 new Service())
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

toolkit = Toolkit()
# toolkit.register_tool_function(my_tool)  # 按需注册工具

agent = ReActAgent(
    name="助手",
    sys_prompt="你是一个有帮助的助手。",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
)

# Step 3: 运行 (类似调用 Service 方法)
response = await agent("你好，请帮我写一段 Python 代码")
print(response)
```

### 多智能体协作示例

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit
from agentscope.pipeline import SequentialPipeline

researcher = ReActAgent(
    name="研究员",
    sys_prompt="你是一个研究助手。",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
)

writer = ReActAgent(
    name="作家",
    sys_prompt="你是一个技术写作助手。",
    model=OpenAIChatModel(model_name="gpt-4o-mini"),
    formatter=OpenAIChatFormatter(),
)

# 使用 SequentialPipeline 顺序执行
seq = SequentialPipeline(agents=[researcher, writer])
result = await seq("研究 Transformer 架构")
```

## 1.7 版本说明与路线图

### 当前版本 v1.0.19
- thinking blocks 支持
- Formatter 本地文件 base64 编码
- 多智能体模式完善（SequentialPipeline/FanoutPipeline/MsgHub）
- DeepResearchAgent 示例（位于 examples/，非核心 API）

### 2.0 路线图（正在开发）

```
Phase 1: TTS (Text-to-Speech) Models ──── 已实现
    └── TTS 模型基类基础设施

Phase 2: Multimodal Models (Non-Realtime)
    └── ReAct agents 多模态支持

Phase 3: Real-time Multimodal Models
    └── 实时语音交互
```

### 升级注意事项

| 旧 API | 新 API | 备注 |
|--------|--------|------|
| `OpenAIChatGPTModel` | `OpenAIChatModel` | 模型类名更新 |
| `AnthropicClaudeModel` | `AnthropicChatModel` | Anthropic 模型类名更新 |
| `agentscope.init(project_name=...)` | `agentscope.init(project=...)` | 参数名变更 |
| `agentscope.init(api_key=...)` | 不再需要 | API key 通过环境变量管理 |

## 1.8 下一步

- [第二章：环境搭建](02_installation.md) - 安装 Python 和 AgentScope
- [第三章：快速入门](03_quickstart.md) - 构建你的第一个 Agent
- [第四章：核心概念](04_core_concepts.md) - 深入理解 Agent、Model、Memory

## 总结

- AgentScope 是生产级多智能体框架，支持顺序/并行/广播等多种协作模式
- 核心 Agent 类型：ReActAgent（通用）、UserAgent（用户交互）、A2AAgent（跨服务）、RealtimeAgent（语音）
- ReActAgent 构造需要四个必填参数：name, sys_prompt, model, formatter
- 工具通过 Toolkit 注册，不是通过 tools 列表

## 练习题

### 练习 1.1: 核心特性认知 [基础]

**题目**：
请列举 AgentScope 的三个核心特性，并说明每个特性解决什么问题。

**验证方式**：
对比文档中的核心特性表格，检查是否包含：多智能体编排、多模型支持、工具调用、记忆系统中的任意三个。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

AgentScope 核心特性举例：

1. **多智能体编排** - 解决多个 Agent 协同工作的问题，支持 SequentialPipeline（顺序）、FanoutPipeline（并行）等多种模式
2. **多模型支持** - 解决统一调用不同 LLM API 的问题，支持 OpenAI、Anthropic、Gemini、DashScope、Ollama、DeepSeek 等
3. **工具调用** - 解决 Agent 无法执行外部操作的问题，支持 Python 执行、Shell 命令、MCP 协议、Function Calling
4. **记忆系统** - 解决 Agent 无法记住对话历史的问题，支持短期记忆（InMemory/Redis/SQL）和长期记忆（Mem0/ReMe）
5. **RAG** - 解决知识库问答问题，支持 PDF/Word/Excel 文档解析和向量存储（Qdrant/Milvus）
6. **可观测性** - 解决调试困难的问题，AgentScope Studio 提供可视化调试和 OpenTelemetry 链路追踪

任选其三作答即可。
</details>

---

### 练习 1.2: Agent 类型选择 [基础]

**题目**：
某公司需要构建一个智能客服系统，系统需要：
- 根据用户问题自动选择不同领域的专家 Agent
- 支持用户通过语音进行咨询
- 需要与其他公司的 AI 系统对接

请选择合适的 Agent 类型并说明理由。

**验证方式**：
检查答案是否正确识别每种场景对应的 Agent 类型。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

| 场景 | 推荐 Agent 类型 | 理由 |
|------|----------------|------|
| 自动选择专家 Agent | `ReActAgent` | 通用推理 Agent，可通过 Pipeline 实现路由 |
| 语音咨询 | `RealtimeAgent` | 专为实时语音交互设计，支持 TTS |
| 与其他公司 AI 系统对接 | `A2AAgent` | 实现 Agent-to-Agent 协议，支持跨服务通信 |

**补充说明**：
- 智能客服通常用 `ReActAgent` + `Toolkit`（挂载知识库检索工具）实现
- 多 Agent 路由可用 `SequentialPipeline` 或 `FanoutPipeline` + 聚合逻辑
</details>

---

### 练习 1.3: Java 概念映射 [中级]

**题目**：
作为 Java 开发者，小王需要将 Spring Boot 项目迁移到 AgentScope。请帮他将以下 Java 概念翻译成 AgentScope 中的对应实现：

| Java 概念 | AgentScope 对应 |
|-----------|----------------|
| `@SpringBootApplication` | ? |
| `@Service` + 业务逻辑 | ? |
| `@Repository` | ? |
| `@Bean` / Utility | ? |
| `EventBus` / Kafka | ? |
| `Cache` (Redis/Caffeine) | ? |

**验证方式**：
对照文档中的 Java 生态对比表进行验证。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

| Java 概念 | AgentScope 对应 |
|-----------|----------------|
| `@SpringBootApplication` | `agentscope.init()` |
| `@Service` + 业务逻辑 | `ReActAgent` |
| `@Repository` | `Model` (如 `OpenAIChatModel`) |
| `@Bean` / Utility | `Toolkit.register_tool_function()` |
| `EventBus` / Kafka | `MsgHub` |
| `Cache` (Redis/Caffeine) | `Memory` (InMemory/Redis/SQLAlchemy) |

**关键映射关系**：
- `agentscope.init()` 是框架入口，类似 Spring Boot 启动类
- ReActAgent 是带 LLM 能力的 Service，每个 Agent 封装了业务逻辑
- Model 封装了 LLM API 调用，类似 Repository 封装数据访问
- 工具通过 Toolkit 统一管理，类似 @Bean 管理可复用组件
</details>

---

### 练习 1.4: 多智能体架构设计 [挑战]

**题目**：
某企业需要构建一个"AI 研究团队"系统，包含：研究员（负责搜索和阅读文献）、分析师（负责数据处理和统计）、作家（负责撰写报告）。用户输入一个研究主题，系统自动输出完整报告。

**请设计**：
1. 选择合适的 Pipeline 类型实现这个工作流
2. 画出简单的数据流图（文字描述即可）
3. 给出核心代码结构

**验证方式**：
检查是否正确选择 SequentialPipeline 并能描述基本的数据流动。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**1. Pipeline 选择：SequentialPipeline（顺序管道）**

原因：研究员 → 分析师 → 作家是典型的流水线模式，上一个 Agent 的输出作为下一个 Agent 的输入。

**2. 数据流图**：
```
用户输入研究主题
       ↓
[研究员 Agent] → 搜索文献、提取关键信息
       ↓
[分析师 Agent] → 数据处理、统计分析
       ↓
[作家 Agent] → 整合信息、生成报告
       ↓
返回最终报告给用户
```

**3. 核心代码结构**：

```python showLineNumbers
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.pipeline import SequentialPipeline
from agentscope.tool import Toolkit

# 创建 Toolkit 并注册工具
toolkit = Toolkit()
toolkit.register_tool_function(web_search_tool)
toolkit.register_tool_function(data_analysis_tool)

# 创建三个 Agent
researcher = ReActAgent(
    name="研究员",
    sys_prompt="你是一个研究助手，负责搜索和阅读文献。",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
)

analyst = ReActAgent(
    name="分析师",
    sys_prompt="你是一个数据分析师，负责处理和统计分析。",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
)

writer = ReActAgent(
    name="作家",
    sys_prompt="你是一个技术写作助手，负责撰写报告。",
    model=OpenAIChatModel(model_name="gpt-4o-mini"),
    formatter=OpenAIChatFormatter(),
    toolkit=Toolkit(),
)

# 构建流水线
research_pipeline = SequentialPipeline(agents=[researcher, analyst, writer])

# 执行
result = await research_pipeline("研究 AI 在医疗领域的应用")
```

**关键点**：
- SequentialPipeline 将三个 Agent 串联起来
- 每个 Agent 的输出自动传递给下一个 Agent
- 可以使用不同规模的模型（如作家用 gpt-4o-mini 节省成本）
</details>

## 下一章

→ [第二章：环境搭建](02_installation.md) - 安装 Python 和 AgentScope
