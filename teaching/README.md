# AgentScope 学习指南

欢迎来到 AgentScope 学习中心！本指南专为有 Java 背景的开发者设计，帮助你快速掌握这个 Python 多智能体框架。

## 学习路径

### 编程基础（Python 语法速成）

专为 Java 开发者设计的 Python 语法教程，使用 AgentScope 源码作为示例。

| 章节 | 内容 | Java 对照 |
|------|------|-----------|
| [P1 - 类与对象](../python/01_class_object.md) | 定义、构造器、属性、@property | class vs Java class |
| [P2 - 异步编程](../python/02_async_await.md) | async/await、协程、EventLoop | CompletableFuture |
| [P3 - 装饰器](../python/03_decorator.md) | @装饰器、闭包、@wraps | AOP/拦截器 |
| [P4 - 类型提示](../python/04_type_hints.md) | 类型注解、泛型、Protocol | Java Generics |
| [P5 - 数据类](../python/05_dataclass.md) | @dataclass、field、defaults | Lombok @Data |
| [P6 - 上下文管理器](../python/06_context_manager.md) | with、__enter__、async with | try-with-resources |
| [P7 - 继承与多态](../python/07_inheritance.md) | 继承、方法覆盖、super() | extends/implements |
| [P8 - 元类](../python/08_metaclass.md) | type()、__new__、metaclass | 注解处理器 |

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

| 章节 | 内容 | 预计时间 | 文档 |
|------|------|----------|------|
| [A1 - Agent 模块深度分析](module_agent_deep.md) | Agent 基类、Hook 机制、设计模式 | 45 分钟 | ⭐ 核心 |
| [A2 - Model 模块深度分析](module_model_deep.md) | 模型适配器、Token 计数、Embedding | 40 分钟 | ⭐ 核心 |
| [A3 - Tool 与 MCP 模块深度分析](module_tool_mcp_deep.md) | 工具系统、MCP 协议、自定义工具 | 35 分钟 | |
| [A4 - Memory 与 RAG 模块深度分析](module_memory_rag_deep.md) | 记忆系统、RAG 架构、向量存储 | 40 分钟 | |
| [A5 - Pipeline 与基础设施深度分析](module_pipeline_infra_deep.md) | 工作流编排、实时交互、追踪系统 | 35 分钟 | |

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
- Python 基础（若不熟悉，请先学习 Python 基础）

## 你能学到什么

1. **多智能体系统基础** - 如何编排多个 AI 智能体协同工作
2. **LLM 应用开发** - 集成 OpenAI、Anthropic 等大模型
3. **工具调用** - 让 AI 智能体调用外部工具
4. **记忆管理** - 实现短期/长期记忆
5. **RAG 检索增强** - 知识库问答
6. **实时语音交互** - 构建语音对话应用
7. **生产级部署** - 从开发到生产的完整流程

## 学习建议

### 新手路线（2-3天）
1. 快速入门 → 核心概念 → 架构设计
2. 完成 quickstart 示例
3. 阅读 Agent 模块深度分析

### 进阶路线（1周）
1. 完成新手路线
2. 深入阅读所有深度模块
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
