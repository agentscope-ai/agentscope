# 《AgentScope Agent开发实战》书籍结构

> **致敬经典**：本书借鉴《网络是怎样连接的》(户根勤 著) 的写作风格
> **目标读者**：有编程基础的开发者（尤其是Java开发者）
> **学习路径**：从"Hello World"到"生产级Agent系统"
> **核心原则**：所有内容必须以源码为唯一事实来源

---

## 书籍目标

完成本书学习后，你将：

1. **掌握AgentScope核心概念**：Msg、Pipeline、MsgHub、ReActAgent
2. **理解AgentScope架构**：Agent、Model、Formatter、Toolkit、Memory 的协作关系
3. **开发多Agent协作系统**：发布-订阅、管道编排
4. **部署Agent到生产环境**：Runtime、Docker
5. **完成5个实战项目**：天气、客服、辩论、研究、语音
6. **成为真实的Contributor**：能提交高质量PR

---

## 书籍结构

```
teaching/book/
├── README.md                         # 主入口
├── BOOK_INDEX.md                     # 本文件 — 详细目录
├── MASTER_PLAN.md                    # 重构总体规划
├── PROGRESS.md                       # 重构进度追踪
│
├── PHASE0 前置文档                    # 学习前的必读基础
│   ├── repository-map.md           # 仓库结构地图
│   ├── tech-stack.md               # 技术栈分析
│   ├── system-entrypoints.md        # 架构入口点
│   └── teaching-audit.md            # 教学审计报告
│
├── PHASE1 架构文档                    # 模块边界、生命周期、数据流
│   └── architecture.md             # 模块边界图、生命周期图、数据流图、调用链索引
│
├── PHASE2 课程大纲                    # 学习路径、依赖关系
│   └── curriculum.md               # Level 1-9 学习路径、章节依赖关系、Contributor 成长路线
│
├── 00-architecture-overview/         # 架构总览
│   ├── 00-overview.md               # AgentScope 是什么
│   ├── 00-source-map.md             # 源码地图
│   └── 00-data-flow.md              # 核心数据流
│
├── 01-getting-started/              # 入门
│   ├── 01-installation.md          # 环境搭建
│   ├── 01-first-agent.md            # 第一个 Agent
│   └── 01-concepts.md               # 核心概念速览
│
├── 02-message-system/               # 消息系统
│   ├── 02-msg-basics.md             # Msg 结构
│   ├── 02-content-blocks.md         # ContentBlock
│   └── 02-message-lifecycle.md      # 消息生命周期
│
├── 03-pipeline/                     # Pipeline 系统
│   ├── 03-pipeline-basics.md       # SequentialPipeline
│   ├── 03-msghub.md                # MsgHub 发布-订阅
│   └── 03-pipeline-advanced.md     # FanoutPipeline
│
├── 04-agent-architecture/           # Agent 架构
│   ├── 04-agent-base.md             # AgentBase 源码
│   ├── 04-react-agent.md            # ReActAgent 完整调用链
│   ├── 04-user-agent.md             # UserAgent 人工参与
│   └── 04-a2a-agent.md             # A2A 协议 Agent
│
├── 05-model-formatter/              # 模型与格式化
│   ├── 05-model-interface.md       # ChatModelBase 统一接口
│   ├── 05-openai-model.md          # OpenAI 模型适配
│   ├── 05-formatter-system.md       # Formatter 系统
│   └── 05-other-models.md          # 其他模型
│
├── 06-tool-system/                  # 工具系统
│   ├── 06-toolkit-core.md          # Toolkit 核心
│   ├── 06-tool-registration.md     # 工具注册机制
│   ├── 06-tool-execution.md        # 工具调用执行
│   └── 06-mcp-integration.md       # MCP 协议集成
│
├── 07-memory-rag/                   # 记忆与知识库
│   ├── 07-memory-architecture.md   # 记忆系统总体设计
│   ├── 07-working-memory.md         # 工作记忆
│   ├── 07-long-term-memory.md      # 长期记忆
│   └── 07-rag-knowledge.md         # RAG 知识库
│
├── 08-multi-agent/                  # 多 Agent 系统
│   ├── 08-multi-agent-patterns.md  # 多 Agent 协作模式
│   ├── 08-msghub-patterns.md      # MsgHub 高级模式
│   ├── 08-a2a-protocol.md         # A2A 协议详解
│   └── 08-tracing-debugging.md    # Tracing 追踪与调试
│
├── 09-advanced-modules/            # 高级模块
│   ├── 09-plan-module.md          # Plan 规划模块
│   ├── 09-session-management.md    # Session 会话管理
│   ├── 09-realtime-agent.md       # Realtime 实时语音
│   ├── 09-tts-system.md          # TTS 语音合成
│   ├── 09-evaluate-system.md       # Evaluate 评估系统
│   └── 09-tuner-system.md         # Tuner 调优系统
│
├── 10-deployment/                   # 部署
│   ├── 10-runtime.md               # Runtime 服务化
│   └── 10-docker-production.md     # Docker 生产部署
│
├── 11-projects/                     # 项目实战
│   ├── 11-weather-agent.md        # 天气 Agent
│   ├── 11-customer-service.md     # 智能客服
│   ├── 11-multi-agent-debate.md    # 多 Agent 辩论
│   ├── 11-deep-research.md        # 深度研究助手
│   └── 11-voice-assistant.md      # 语音助手
│
├── 12-contributing/                 # Contributor 成长
│   ├── 12-how-to-contribute.md   # PR 流程、代码规范
│   ├── 12-codebase-navigation.md  # 源码导航地图
│   ├── 12-debugging-guide.md     # 调试指南
│   └── 12-architecture-decisions.md # 架构决策记录
│
├── appendices/                      # 附录
│   ├── appendix-a.md              # 术语表
│   ├── appendix-b.md              # Python 语法速查
│   ├── appendix-c.md              # 故障排除
│   ├── appendix-d.md              # API 快速参考
│   ├── appendix-e.md              # 配置参考
│   └── troubleshooting.md         # 常见问题
│
└── reference/                       # 深度参考
    ├── module_agent_deep.md       # Agent 系统深度解析
    ├── module_model_deep.md       # Model 系统深度解析
    ├── module_message_deep.md     # 消息系统深度解析
    ├── module_memory_rag_deep.md  # 记忆 RAG 深度解析
    ├── module_pipeline_infra_deep.md # Pipeline 深度解析
    └── ... (更多深度参考文档)
```

---

## 学习路径（Level 1-9）

### 前置必读：PHASE0 文档

**重要**：在开始学习主章节之前，建议先阅读以下 PHASE0 文档，建立整体认知：

| 文档 | 目标 | 时长 | 为什么需要 |
|------|------|------|------------|
| [repository-map.md](./repository-map.md) | 理解仓库结构 | 20分钟 | 知道源码在哪里 |
| [tech-stack.md](./tech-stack.md) | 理解技术选型 | 15分钟 | 知道用了哪些技术 |
| [system-entrypoints.md](./system-entrypoints.md) | 追踪架构入口 | 30分钟 | 知道关键调用链 |
| [teaching-audit.md](./teaching-audit.md) | 了解内容覆盖 | 10分钟 | 知道学什么 |

**建议阅读顺序**：
1. repository-map.md → tech-stack.md → system-entrypoints.md → teaching-audit.md
2. 然后按 Level 1-9 顺序学习主章节

### Level 0: 前置基础 ★
**目标**：建立整体认知，理解架构入口

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [repository-map.md](./repository-map.md) | 20分钟 | 仓库结构、模块职责 |
| [tech-stack.md](./tech-stack.md) | 15分钟 | 技术选型、依赖版本 |
| [system-entrypoints.md](./system-entrypoints.md) | 30分钟 | 调用链追踪、入口函数 |
| [architecture.md](./architecture.md) | 30分钟 | 模块边界、数据流图 |
| [curriculum.md](./curriculum.md) | 15分钟 | 学习路径、成长路线 |
| [teaching-audit.md](./teaching-audit.md) | 10分钟 | 内容覆盖、质量评估 |

### Level 1: 入门级
**目标**：知道项目是干什么的

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [00-overview](./00-architecture-overview/00-overview.md) | 30分钟 | AgentScope 是什么 |
| [01-installation](./01-getting-started/01-installation.md) | 30分钟 | 环境搭建、API Key 配置 |

### Level 2: 基础级
**目标**：能运行项目

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [01-first-agent](./01-getting-started/01-first-agent.md) | 1小时 | 运行第一个 Agent |
| [02-msg-basics](./02-message-system/02-msg-basics.md) | 1小时 | Msg 的 name/content/role |

### Level 3: 模块边界
**目标**：理解模块边界

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [03-pipeline-basics](./03-pipeline/03-pipeline-basics.md) | 2小时 | SequentialPipeline、FanoutPipeline |
| [03-msghub](./03-pipeline/03-msghub.md) | 1小时 | 发布-订阅模式 |
| [04-agent-base](./04-agent-architecture/04-agent-base.md) | 2小时 | AgentBase 抽象 |

### Level 4: 核心数据流
**目标**：理解核心数据流

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [04-react-agent](./04-agent-architecture/04-react-agent.md) | 4小时 | Reasoning-Acting 循环 |
| [05-model-interface](./05-model-formatter/05-model-interface.md) | 2小时 | ChatModelBase 统一接口 |

### Level 5: 源码调用链
**目标**：能跟踪源码调用链

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [05-formatter-system](./05-model-formatter/05-formatter-system.md) | 2小时 | Formatter 系统分析 |
| [06-toolkit-core](./06-tool-system/06-toolkit-core.md) | 3小时 | 工具注册、函数调用 |
| [07-memory-architecture](./07-memory-rag/07-memory-architecture.md) | 2小时 | 记忆系统总体设计 |

### Level 6: 修改小功能
**目标**：能修改小功能

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [08-multi-agent-patterns](./08-multi-agent/08-multi-agent-patterns.md) | 2小时 | 多 Agent 协作模式 |
| [08-a2a-protocol](./08-multi-agent/08-a2a-protocol.md) | 1小时 | A2A 协议详解 |
| [08-tracing-debugging](./08-multi-agent/08-tracing-debugging.md) | 2小时 | OpenTelemetry 追踪 |

### Level 7: 独立开发模块
**目标**：能独立开发模块

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [09-plan-module](./09-advanced-modules/09-plan-module.md) | 3小时 | Plan 规划模块 |
| [09-session-management](./09-advanced-modules/09-session-management.md) | 2小时 | Session 会话管理 |
| [09-realtime-agent](./09-advanced-modules/09-realtime-agent.md) | 3小时 | Realtime 实时语音 |

### Level 8: 提交高质量PR
**目标**：能提交高质量PR

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [11-weather-agent](./11-projects/11-weather-agent.md) | 4小时 | 第一个完整项目 |
| [12-how-to-contribute](./12-contributing/12-how-to-contribute.md) | 3小时 | PR 流程、代码规范 |
| [12-debugging-guide](./12-contributing/12-debugging-guide.md) | 2小时 | 调试指南 |

### Level 9: 参与架构讨论
**目标**：能参与架构讨论

| 内容 | 时长 | 核心技能 |
|------|------|----------|
| [12-architecture-decisions](./12-contributing/12-architecture-decisions.md) | 3小时 | 架构决策记录 |
| [reference/](./reference/) | 10小时+ | 模块深度分析 |

---

## 源码映射索引

### 核心模块源码位置

| 模块 | 源码路径 | 对应章节 |
|------|----------|----------|
| **Msg** | `src/agentscope/message/_message_base.py` | Ch2 |
| **ContentBlock** | `src/agentscope/message/_message_block.py` | Ch2 |
| **Pipeline** | `src/agentscope/pipeline/_class.py` | Ch3 |
| **MsgHub** | `src/agentscope/pipeline/_msghub.py` | Ch3 |
| **ChatRoom** | `src/agentscope/pipeline/_chat_room.py` | Ch3 |
| **AgentBase** | `src/agentscope/agent/_agent_base.py` | Ch4 |
| **ReActAgent** | `src/agentscope/agent/_react_agent.py` | Ch4 |
| **FormatterBase** | `src/agentscope/formatter/` | Ch5 |
| **ChatModelBase** | `src/agentscope/model/` | Ch5 |
| **Toolkit** | `src/agentscope/tool/_toolkit.py` | Ch6 |
| **ToolResponse** | `src/agentscope/tool/_response.py` | Ch6 |
| **MemoryBase** | `src/agentscope/memory/_working_memory/_base.py` | Ch7 |
| **InMemoryMemory** | `src/agentscope/memory/_working_memory/_in_memory_memory.py` | Ch7 |
| **LongTermMemory** | `src/agentscope/memory/_long_term_memory/` | Ch7 |
| **FanoutPipeline** | `src/agentscope/pipeline/_functional.py` | Ch8 |
| **Runtime** | `src/agentscope/runtime/` | Ch10 |
| **A2A Protocol** | `src/agentscope/a2a/` | Ch8 |
| **Realtime** | `src/agentscope/realtime/` | Ch9 |
| **MCP** | `src/agentscope/mcp/` | Ch6 |
| **RAG** | `src/agentscope/rag/` | Ch7 |
| **Tracing** | `src/agentscope/tracing/` | Ch8 |
| **Session** | `src/agentscope/session/` | Ch9 |
| **Plan** | `src/agentscope/plan/` | Ch9 |
| **Evaluate** | `src/agentscope/evaluate/` | Ch9 |
| **Tuner** | `src/agentscope/tuner/` | Ch9 |

---

## 深度文档索引 (Reference)

| 主题 | 深度文档 | 源码模块 |
|------|----------|----------|
| Agent系统 | [module_agent_deep.md](./reference/module_agent_deep.md) | `src/agentscope/agent/` |
| Model系统 | [module_model_deep.md](./reference/module_model_deep.md) | `src/agentscope/model/` |
| 消息系统 | [module_message_deep.md](./reference/module_message_deep.md) | `src/agentscope/message/` |
| 工具系统 | [module_tool_mcp_deep.md](./reference/module_tool_mcp_deep.md) | `src/agentscope/tool/` |
| 记忆系统 | [module_memory_rag_deep.md](./reference/module_memory_rag_deep.md) | `src/agentscope/memory/` |
| Pipeline | [module_pipeline_infra_deep.md](./reference/module_pipeline_infra_deep.md) | `src/agentscope/pipeline/` |
| Formatter | [module_formatter_deep.md](./reference/module_formatter_deep.md) | `src/agentscope/formatter/` |
| Runtime | [module_runtime_deep.md](./reference/module_runtime_deep.md) | `src/agentscope/runtime/` |
| A2A协议 | [module_a2a_deep.md](./reference/module_a2a_deep.md) | `src/agentscope/a2a/` |

---

## 学习路径建议

### 路径A：快速入门（2周）
1. 00-overview → 01-installation → 01-first-agent
2. 11-weather-agent（第一个完整项目）
3. appendix-a（术语表）

### 路径B：系统学习（8周）
1. 00-architecture → 01-getting-started → 02-message-system
2. 03-pipeline → 04-agent-architecture
3. 05-model-formatter → 06-tool-system → 07-memory-rag
4. 11-weather-agent → 11-customer-service

### 路径C：深入精通（12周）
1. 完成路径B全部内容
2. 08-multi-agent → 09-advanced-modules
3. 11-multi-agent-debate → 11-deep-research → 11-voice-assistant
4. 12-contributing（成为 Contributor）
5. reference/（深度文档）→ 深入研究源码

---

## 章节依赖关系

```
01-getting-started
├── 02-message-system
│   └── 03-pipeline
│       ├── 04-agent-architecture
│       │   ├── 05-model-formatter
│       │   └── 06-tool-system
│       │       └── 07-memory-rag
│       │           └── 08-multi-agent
│       │               ├── 09-advanced-modules
│       │               └── 10-deployment
│       └── 12-contributing
└── 11-projects
```

---

*最后更新: 2026-05-10 (Ralph Loop 迭代 #32)*

> **质量状态**: 参见 [QUALITY_MATRIX.md](./QUALITY_MATRIX.md) — 49 章 × 12 项质量指标的完整矩阵
