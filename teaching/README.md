# AgentScope 学习指南

欢迎来到 AgentScope 学习中心！本指南专为有 Java 背景的开发者设计，帮助你快速掌握这个 Python 多智能体框架。

## 学习路径

### 编程基础（Python 语法速成）

专为 Java 开发者设计的 Python 语法教程，使用 AgentScope 源码作为示例。

| 章节 | 内容 | Java 对照 |
|------|------|-----------|
| [P1 - 类与对象](./python/01_class_object.md) | 定义、构造器、属性、@property | class vs Java class |
| [P2 - 异步编程](./python/02_async_await.md) | async/await、协程、EventLoop | CompletableFuture |
| [P3 - 装饰器](./python/03_decorator.md) | @装饰器、闭包、@wraps | AOP/拦截器 |
| [P4 - 类型提示](./python/04_type_hints.md) | 类型注解、泛型、Protocol | Java Generics |
| [P5 - 数据类](./python/05_dataclass.md) | @dataclass、field、defaults | Lombok @Data |
| [P6 - 上下文管理器](./python/06_context_manager.md) | with、__enter__、async with | try-with-resources |
| [P7 - 继承与多态](./python/07_inheritance.md) | 继承、方法覆盖、super() | extends/implements |
| [P8 - 元类](./python/08_metaclass.md) | type()、__new__、metaclass | 注解处理器 |

### 基础模块（入门必读）

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [01 - 项目概述](01_project_overview.md) | AgentScope 是什么，能做什么 | 10 分钟 | |
| [02 - 环境搭建](02_installation.md) | 安装 Python 环境、项目依赖 | 15 分钟 | |
| [03 - 快速入门](03_quickstart.md) | 5 分钟构建你的第一个智能体 | 20 分钟 | |
| [04 - 核心概念](04_core_concepts.md) | Agent、Model、Tool、Memory | 30 分钟 | |
| [05 - 架构设计](05_architecture.md) | 模块设计、代码组织 | 25 分钟 | |
| [06 - 开发指南](06_development_guide.md) | 代码规范、调试技巧 | 20 分钟 | |
| [07 - Java 开发者视角](07_java_comparison.md) | 与 Java/Spring 对比学习 | 15 分钟 | |

### 深度模块（进阶深入）

**核心层**

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [A1 - Agent 模块深度分析](module_agent_deep.md) | Agent 基类、Hook 机制、设计模式 | 45 分钟 | ⭐ 核心 |
| [A2 - Model 模块深度分析](module_model_deep.md) | 模型适配器、Token 计数、Embedding | 40 分钟 | ⭐ 核心 |
| [A3 - Tool 与 MCP 模块深度分析](module_tool_mcp_deep.md) | 工具系统、MCP 协议、自定义工具 | 35 分钟 | |
| [A4 - Memory 与 RAG 模块深度分析](module_memory_rag_deep.md) | 记忆系统、RAG 架构、向量存储 | 40 分钟 | |
| [A5 - Pipeline 与基础设施深度分析](module_pipeline_infra_deep.md) | 工作流编排、实时交互、追踪系统 | 35 分钟 | |

**基础设施层**

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [A6 - Config 配置系统深度分析](module_config_deep.md) | ContextVar 配置、线程安全、运行时初始化 | 25 分钟 | |
| [A7 - Dispatcher 调度器深度分析](module_dispatcher_deep.md) | MsgHub 消息中心、发布订阅、消息路由 | 35 分钟 | |
| [A8 - Message 消息系统深度分析](module_message_deep.md) | Msg 类、ContentBlock 类型体系、序列化 | 30 分钟 | |
| [A9 - Runtime 运行时深度分析](module_runtime_deep.md) | SequentialPipeline、FanoutPipeline、流式处理 | 35 分钟 | |

**支撑层**

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [A10 - File 文件操作深度分析](module_file_deep.md) | 文件工具、Base64 编解码、MCP 集成 | 25 分钟 | |
| [A11 - Utils 工具模块深度分析](module_utils_deep.md) | 日志系统、JSON 修复、DictMixin | 30 分钟 | |

**扩展层**

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [A12 - StateModule 状态管理深度分析](module_state_deep.md) | 状态序列化、嵌套模块、自定义序列化钩子 | 20 分钟 | ⭐ 基础 |
| [A13 - Formatter 消息格式化深度分析](module_formatter_deep.md) | 多模型适配、消息截断、工具结果格式化 | 35 分钟 | |
| [A14 - Embedding 与 Token 计数深度分析](module_embedding_token_deep.md) | 向量嵌入模型、缓存策略、Token 估算 | 30 分钟 | |
| [A15 - Plan 计划系统深度分析](module_plan_deep.md) | 任务分解、SubTask 状态机、计划提示、历史恢复 | 40 分钟 | |
| [A16 - Session 会话持久化深度分析](module_session_deep.md) | JSON/Redis/Tablestore 会话管理 | 25 分钟 | |
| [A17 - Tracing 链路追踪深度分析](module_tracing_deep.md) | OpenTelemetry、Span 装饰器、可观测性 | 35 分钟 | |
| [A18 - Evaluate 评估框架深度分析](module_evaluate_deep.md) | Benchmark 基准、ACE 评估、评估存储 | 25 分钟 | |
| [A19 - Tuner 智能体调优深度分析](module_tuner_deep.md) | RL/SFT 训练、Prompt 调优、模型选择 | 30 分钟 | |

### 参考资料

| 章节 | 内容 | 文档 |
|------|------|------|
| [R1 - 官方文档与参考资料](reference_official_docs.md) | 官方文档精华、竞品对比 | 📚 必读 |
| [R2 - 最佳实践](reference_best_practices.md) | 设计模式、Prompt 工程、生产部署 | 📚 必读 |

### 补充材料

| 章节 | 内容 | 文档 |
|------|------|------|
| [最佳实践](best_practices.md) | 开发最佳实践汇总 | |
| [案例研究](case_studies.md) | 实际项目案例分析 | |
| [故障排除](troubleshooting.md) | 常见问题与解决方案 | |
| [学习报告](research_report.md) | 调研报告 | |

## 前置知识

- 了解 Java 面向对象编程
- 熟悉设计模式（工厂、策略、模板方法）
- 最好有 REST API 或微服务开发经验
- Python 基础（若不熟悉，请先学习 Python 基础模块 P1-P8）

## 你能学到什么

1. **多智能体系统基础** - 如何编排多个 AI 智能体协同工作
2. **LLM 应用开发** - 集成 OpenAI、Anthropic 等大模型
3. **工具调用** - 通过 Toolkit 注册和调用外部工具
4. **记忆管理** - 实现短期/长期记忆
5. **RAG 检索增强** - 知识库问答
6. **实时语音交互** - 构建语音对话应用
7. **生产级部署** - 从开发到生产的完整流程

## 知识图谱

学习者可以从任意模块顺着引用链学到所有相关知识。以下是模块间的关联图谱：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           核心层 (Core Layer)                                │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐      │
│  │   A1 Agent      │────▶│   A2 Model      │◀───│   A3 Tool/MCP   │      │
│  │ module_agent    │     │ module_model    │     │ module_tool_mcp │      │
│  │ _deep          │     │ _deep           │     │ _deep           │      │
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘      │
│           │                        │                        │               │
│           │              ┌─────────┴─────────┐             │               │
│           │              │  A13 Formatter   │             │               │
│           │              │ module_formatter │             │               │
│           │              │ _deep           │             │               │
│           │              └─────────┬─────────┘             │               │
│           │                        │                        │               │
│           ▼                        ▼                        ▼               │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │                     A8 Message 消息系统                          │      │
│  │              [module_message_deep.md#3-核心类与函数源码解读]     │      │
│  └─────────────────────────────────────────────────────────────────┘      │
│                                │                                          │
└────────────────────────────────┼──────────────────────────────────────────┘
                                 │
┌────────────────────────────────┼──────────────────────────────────────────┐
│                        基础设施层 (Infrastructure)                         │
│           ┌────────────────────┼────────────────────┐                  │
│           │                    │                    │                    │
│           ▼                    ▼                    ▼                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐          │
│  │  A7 Dispatcher  │  │  A9 Runtime     │  │  A5 Pipeline    │          │
│  │ module_dispatcher│  │ module_runtime  │  │module_pipeline  │          │
│  │ _deep           │  │ _deep          │  │ _infra_deep    │          │
│  └────────┬────────┘  └─────────────────┘  └────────┬────────┘          │
│           │                                              │                 │
│           │           ┌───────────────────────────────┘                 │
│           │           │                                                     │
│           ▼           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │              A6 Config 配置系统                                  │      │
│  │     [module_config_deep.md#3-核心功能源码解读]                  │      │
│  └─────────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          支撑层 (Support Layer)                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │   A10 File      │  │   A11 Utils    │  │   A12 State     │            │
│  │ module_file     │  │ module_utils    │  │ module_state    │            │
│  │ _deep          │  │ _deep          │  │ _deep          │            │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘            │
│           │                    │                    │                     │
│           └────────────────────┼────────────────────┘                     │
│                                │                                            │
│                                ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────┐      │
│  │              A14 Embedding 与 Token                              │      │
│  │      [module_embedding_token_deep.md#8-token-计数机制]           │      │
│  └─────────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          扩展层 (Extension Layer)                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │   A15 Plan      │  │   A16 Session  │  │   A17 Tracing  │            │
│  │ module_plan     │  │ module_session │  │ module_tracing │            │
│  │ _deep          │  │ _deep         │  │ _deep         │            │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘            │
│           │                    │                    │                     │
│           └────────────────────┼────────────────────┘                     │
│                                │                                            │
│           ┌───────────────────┼───────────────────┐                       │
│           │                   │                   │                        │
│           ▼                   ▼                   ▼                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │   A4 Memory/    │  │   A18 Evaluate │  │   A19 Tuner    │            │
│  │   RAG          │  │ module_evaluate│  │ module_tuner   │            │
│  │ module_memory  │  │ _deep         │  │ _deep         │            │
│  │ _rag_deep     │  └─────────────────┘  └─────────────────┘            │
│  └─────────────────┘                                                     │
└──────────────────────────────────────────────────────────────────────────┘
```

### 引用链示例

**示例1：从 Agent 学习完整调用链**
```
Agent (A1) → Model (A2) → Formatter (A13) → Message (A8)
         ↓
       Tool (A3) → Utils (A11) → File (A10)
```

**示例2：理解 Pipeline 协作**
```
Pipeline (A5) → Dispatcher (A7) → Runtime (A9)
       ↓
    Agent (A1) ← Message (A8)
```

**示例3：状态与持久化**
```
StateModule (A12) → Agent (A1) → Session (A16)
                          ↓
                      Memory (A4) → RAG (A4)
```

### 跳转索引

| 起点 | 可跳转模块 | 参考章节 |
|------|-----------|----------|
| Agent (A1) | Model, Tool, Memory, Pipeline, Message, State | [章节关联](module_agent_deep.md#章节关联) |
| Model (A2) | Formatter, Tool, Embedding, Tracing, Message | [章节关联](module_model_deep.md#章节关联) |
| Pipeline (A5) | Agent, Tool, Dispatcher, Session, Tracing | [章节关联](module_pipeline_infra_deep.md#章节关联) |
| StateModule (A12) | Agent, Memory, Plan, Session, Tool | [章节关联](module_state_deep.md#章节关联) |

---

## 核心概念速览

创建 Agent 时请记住四个必填参数：

```python
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="助手",                              # 必填
    sys_prompt="你是一个有帮助的助手。",         # 必填
    model=OpenAIChatModel(model_name="gpt-4o"), # 必填
    formatter=OpenAIChatFormatter(),            # 必填（匹配 Model 提供商）
    toolkit=Toolkit(),                          # 可选（不配则为纯对话模式）
)
```

## 学习建议

### 新手路线（2-3天）
1. Python 基础（P1-P8，如已熟悉可跳过）
2. 快速入门（03） → 核心概念（04） → 架构设计（05）
3. 运行 quickstart 示例，确保能跑通
4. 阅读 Agent 模块深度分析（A1）

### 进阶路线（1周）
1. 完成新手路线
2. 深入阅读所有深度模块（A1-A19）
3. 学习最佳实践资料
4. 尝试自定义 Agent 开发

### 专家路线（2周+）
1. 完成进阶路线
2. 阅读源码并尝试贡献
3. 研究 RAG 和 Pipeline 模块
4. 部署自己的多智能体应用

## 在线资源

- [官方文档](https://doc.agentscope.io/)
- [GitHub 仓库](https://github.com/agentscope-ai/agentscope)
- [示例代码](../examples/)

---

*本教程面向有编程基础的开发者，Java 开发者可重点关注第 7 章的对比学习。*
