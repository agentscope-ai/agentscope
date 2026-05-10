# 《AgentScope Agent开发实战》

> **目标读者**：有编程基础的开发者
> **学习路径**：从"Hello World"到"生产级Agent系统" → "Contributor实践"
> **核心原则**：所有内容以源码为唯一事实来源

---

## 这本书能让你学到什么

1. **掌握 AgentScope 核心概念**：Msg、Pipeline、MsgHub、ReActAgent
2. **理解 Agent 架构**：AgentBase → ReActAgent → Hook系统 → 元类驱动的包装机制
3. **开发多 Agent 协作系统**：Pipeline 串行/并行、MsgHub 发布-订阅、A2A 协议
4. **掌握工具和记忆系统**：Toolkit 工具注册/执行、Memory 工作记忆/长期记忆、RAG 知识库
5. **部署到生产环境**：Runtime 服务化、Docker 容器化
6. **完成 5 个实战项目**：天气、客服、辩论、研究、语音
7. **成为真实的 Contributor**：PR 流程、代码规范、调试指南、架构决策

---

## 书籍结构

```
teaching/book/
├── README.md                         # 本文件 — 快速入口
├── BOOK_INDEX.md                     # 详细目录 + 学习路径
│
├── PHASE0-2 前置文档                  # 学习前的必读基础
│   ├── repository-map.md            # 仓库结构地图
│   ├── tech-stack.md                # 技术栈分析
│   ├── system-entrypoints.md         # 架构入口点
│   ├── teaching-audit.md             # 教学审计报告
│   ├── architecture.md              # 模块边界图、生命周期图、数据流图、调用链索引
│   └── curriculum.md                # Level 1-9 学习路径、Contributor 成长路线
│
├── 00-architecture-overview/         # 架构总览
│   ├── 00-overview.md               # AgentScope 是什么
│   ├── 00-source-map.md             # 源码地图
│   └── 00-data-flow.md              # 核心数据流
│
├── 01-getting-started/              # 入门
│   ├── 01-installation.md           # 环境搭建
│   ├── 01-first-agent.md            # 第一个 Agent
│   └── 01-concepts.md               # 核心概念速览
│
├── 02-message-system/               # 消息系统
├── 03-pipeline/                     # Pipeline 编排
├── 04-agent-architecture/           # Agent 架构
├── 05-model-formatter/              # 模型与格式化
├── 06-tool-system/                  # 工具系统
├── 07-memory-rag/                   # 记忆与 RAG
├── 08-multi-agent/                  # 多 Agent 系统
├── 09-advanced-modules/             # 高级模块 (Plan, Session, Realtime, TTS, Evaluate, Tuner)
├── 10-deployment/                   # 部署
├── 11-projects/                     # 项目实战 (5个项目)
├── 12-contributing/                 # Contributor 成长
│
├── appendices/                      # 附录 (术语表/Python速查/代码模板/故障排除)
├── reference/                       # 深度参考 (16个模块深度解析)
└── python/                          # Python 语法教程 (前置内容, 9章)
```

---

## 快速开始

### 推荐学习路径

| 路径 | 适合人群 | 时间 | 内容 |
|------|---------|------|------|
| **快速入门** | 有 Agent 开发经验 | 1周 | 00-architecture → 01-getting-started → 11-weather-agent |
| **系统学习** | 一般开发者 | 8周 | Level 0→9 完整路径 |
| **深入精通** | 全栈工程师 | 12周 | 全部章节 + reference/ 深度参考 + 源码贡献 |

### 环境准备

```bash
pip install -e "agentscope[full]"
python -c "from agentscope.agent import ReActAgent; print('OK')"
```

---

## 学习路径 (Level 1-9)

| Level | 目标 | 核心章节 | 预计时间 |
|-------|------|----------|----------|
| **1** | 知道项目是什么 | 00-overview, 01-installation | 1小时 |
| **2** | 能运行项目 | 01-first-agent, 02-msg-basics | 2小时 |
| **3** | 理解模块边界 | 03-pipeline, 04-agent-base | 4小时 |
| **4** | 理解核心数据流 | 04-react-agent, 05-model-interface | 5小时 |
| **5** | 能跟踪源码调用链 | 05-formatter, 06-toolkit-core, 07-memory | 6小时 |
| **6** | 能修改小功能 | 08-multi-agent, 08-tracing-debugging | 6小时 |
| **7** | 能独立开发模块 | 09-advanced-modules | 8小时 |
| **8** | 能提交高质量 PR | 11-projects, 12-contributing | 10小时 |
| **9** | 能参与架构讨论 | reference/ + 12-architecture-decisions | 持续 |

详细学习路径和章节依赖关系见 [BOOK_INDEX.md](./BOOK_INDEX.md) 和 [curriculum.md](./curriculum.md)。

---

## 源码映射

| 模块 | 源码路径 | 对应章节 |
|------|----------|----------|
| **Msg** | `src/agentscope/message/_message_base.py` | 02-message-system |
| **ContentBlock** | `src/agentscope/message/_message_block.py` | 02-content-blocks |
| **Pipeline** | `src/agentscope/pipeline/_class.py`, `_functional.py` | 03-pipeline |
| **MsgHub** | `src/agentscope/pipeline/_msghub.py` | 03-msghub |
| **AgentBase** | `src/agentscope/agent/_agent_base.py` | 04-agent-base |
| **ReActAgent** | `src/agentscope/agent/_react_agent.py` | 04-react-agent |
| **Hook 系统** | `src/agentscope/agent/_agent_meta.py` | 04-agent-base |
| **Formatter** | `src/agentscope/formatter/` | 05-formatter-system |
| **Model** | `src/agentscope/model/` | 05-model-interface |
| **Toolkit** | `src/agentscope/tool/_toolkit.py` | 06-tool-system |
| **Memory** | `src/agentscope/memory/` | 07-memory-rag |
| **RAG** | `src/agentscope/rag/` | 07-rag-knowledge |
| **A2A Protocol** | `src/agentscope/a2a/` | 08-a2a-protocol |
| **Tracing** | `src/agentscope/tracing/` | 08-tracing-debugging |
| **Realtime** | `src/agentscope/realtime/` | 09-realtime-agent |
| **Session** | `src/agentscope/session/` | 09-session-management |
| **MCP** | `src/agentscope/mcp/` | 06-mcp-integration |

---

## 配套资源

- [AgentScope 官方仓库](https://github.com/agentscope-ai/agentscope)
- [Python 基础教程](../python/) — Java 开发者 Python 速成
- [模块深度解析](./reference/) — 16 个模块源码深度解读
- [附录：术语对照表](./appendices/appendix_a.md)
- [附录：Python 速查卡](./appendices/appendix_b.md)
- [附录：代码模板](./appendices/appendix_c.md)
- [附录：故障排除](./appendices/troubleshooting.md)

---

## 参与贡献

欢迎提交 Issue 和 Pull Request。详见 [CONTRIBUTING.md](./CONTRIBUTING.md) 和 [12-contributing/](./12-contributing/)。

---

*本书为 AgentScope 学习资料，基于 Apache 2.0 许可证。*
