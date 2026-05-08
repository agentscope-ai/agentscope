# AgentScope 官方文档参考手册

> 本文档收集 AgentScope 官方文档的核心内容、API 参考要点、竞品对比分析以及推荐阅读清单。所有内容均来自实际访问的官方资料，并标注了原始来源链接。

## 学习目标

- 理解 AgentScope 框架的核心概念：状态、消息、工具、智能体、提示词格式化
- 掌握 AgentScope 的 API 体系：模型 API、记忆 API、工具 API、消息 API
- 了解 AgentScope 与 LangChain/CrewAI/AutoGen/MetaGPT 等框架的对比优势
- 建立对多智能体系统、ReAct 范式等学术背景的基本认知

---

## 一、AgentScope 官方文档精华摘要

### 1.1 官方文档概述

AgentScope 是一个生产级、易于使用的智能体框架，具有以下核心特性：

- **简单但强大**：使用内置的 ReAct 智能体、工具、技能、人工介入控制、记忆、规划、实时语音、评估、模型微调等功能，只需 5 分钟即可开始构建智能体应用
- **可扩展**：大量生态系统集成用于工具、记忆和可观测性；内置支持 MCP、A2A 等协议和智能体技能；消息中心用于灵活的多智能体编排和工作流
- **生产级**：可本地部署、作为无服务器云部署或 K8s 集群部署；内置 OTel 支持和多语言支持

**官方文档入口**：
- 主文档：https://docs.agentscope.io/
- 中文文档：https://doc.agentscope.io/zh_CN/
- GitHub 仓库：https://github.com/agentscope-ai/agentscope
- Java 版本：https://java.agentscope.io/

### 1.2 核心概念

AgentScope 的核心概念从工程实践角度出发，阐明框架的设计理念。

#### 1.2.1 状态（State）

状态管理是 AgentScope 框架构建的基础。状态表示对象运行时某一时刻数据的快照。

AgentScope 将对象的"初始化"与"状态管理"分离，对象在初始化后通过 `load_state_dict` 和 `state_dict` 方法恢复到不同的状态，或导出当前的状态。

在 AgentScope 中，智能体（Agent）、记忆（memory）、长期记忆（Long-term memory）和工具模块（toolkit）都是有状态的对象。AgentScope 通过支持嵌套式的状态管理，将这些对象的状态管理联系起来。

```python
# 状态管理示例
agent = ReActAgent(...)
state = agent.state_dict()  # 导出状态
agent.load_state_dict(state)  # 恢复状态
```

#### 1.2.2 消息（Message）

消息是 AgentScope 最核心的数据结构，用于：

- 在智能体之间交换信息
- 在用户交互界面显示信息
- 在记忆中存储信息
- 作为 AgentScope 与不同 LLM API 之间的统一媒介

消息结构包含 `name`（名称）、`role`（角色）和 `content`（内容），以及可选的 `url` 字段用于多模态数据传输。

#### 1.2.3 工具（Tool）

AgentScope 中的"工具"指的是可调用的 Python 对象，包括：

- 函数
- 偏函数（Partial function）
- 实例方法
- 类方法
- 静态方法
- 带有 `__call__` 方法的可调用实例

可调用对象可以是异步或同步调用的，流式或非流式返回结果的。

```python
from agentscope.tool import Toolkit, ToolResponse

def get_weather(location: str) -> ToolResponse:
    return ToolResponse(result=f"{location}今日天气：晴，25-32℃")

toolkit = Toolkit()
toolkit.register_tool_function(get_weather, group_name="weather")
```

#### 1.2.4 智能体（Agent）

在 AgentScope 中，智能体行为被抽象为 `AgentBase` 类中的三个核心函数：

| 函数 | 描述 |
|------|------|
| `reply` | 处理传入的消息并生成响应消息 |
| `observe` | 接收来自环境或其它智能体的消息，但不返回响应 |
| `print` | 将消息显示到目标输出（例如终端、Web 界面） |

为了支持用户实时介入（Realtime Steering），AgentScope 提供了额外的 `handle_interrupt` 函数来处理智能体回复过程中的用户中断。

ReAct 智能体是 AgentScope 中最重要的智能体，该智能体的回复过程分为两个阶段：

1. **推理（Reasoning）**：通过调用 LLM 进行推理并生成工具调用
2. **行动（Acting）**：执行工具函数

#### 1.2.5 提示词格式化（Prompt Formatter）

提示词格式化器是 AgentScope 中保证 LLM 兼容性的核心组件，负责将消息对象转换为 LLM API 所需的格式。

此外，诸如提示工程、截断和消息验证等附加功能也可以在格式化器中实现。

在格式化器中，"多智能体"（或"多实体"）概念与常见的多智能体编排概念不同。它专注于给定消息中包含多个身份实体的场景，因此 LLM API 中常用的 `role` 字段（通常取值为 "user"、"assistant" 或 "system"）无法区分它们。

AgentScope 提供 `MultiAgentFormatter` 来处理这种场景，通常用于游戏、多人聊天和社交仿真。

#### 1.2.6 长期记忆

AgentScope 为短期记忆和长期记忆提供了不同的基类，但并没有严格区分它们的作用。只要开发者的需求得到了很好的满足，完全可以只使用一个强大的记忆系统。

AgentScope 为长期记忆提供了两种运行和管理方式：
- **agent_control 模式**：允许智能体自己主动管理长期记忆
- **static_control 模式**：传统的由开发者管理的长期记忆模式

### 1.3 智能体类层次结构

| 类 | 抽象方法 | 支持的钩子函数 | 描述 |
|------|---------|--------------|------|
| `AgentBase` | `reply`, `observe`, `print`, `handle_interrupt` | pre_/post_reply, pre_/post_observe, pre_/post_print | 所有智能体的基类，提供基本接口和钩子 |
| `ReActAgentBase` | `reply`, `observe`, `print`, `handle_interrupt`, `_reasoning`, `_acting` | pre_/post_reply, pre_/post_observe, pre_/post_print, pre_/post_reasoning, pre_/post_acting | ReAct 类智能体的抽象类 |
| `ReActAgent` | 继承自 ReActAgentBase | 同上 | ReActAgentBase 的实现，默认使用的智能体类型 |
| `UserAgent` | `reply`, `observe`, `print` | pre_/post_reply, pre_/post_observe, pre_/post_print | 用于与远程 A2A 代理通信的智能体 |

---

## 二、API 参考要点

### 2.1 模型 API

#### 2.1.1 支持的模型

AgentScope 支持多种模型 API 和提供商：

| 模型提供商 | 模型类 | 工具调用 | 流式返回 | 嵌入模型 | 推理模型 |
|-----------|--------|---------|---------|---------|---------|
| OpenAI | `OpenAIChatModel` | ✅ | ✅ | ✅ | ✅ |
| DashScope (阿里云) | `DashScopeChatModel` | ✅ | ✅ | ✅ | ✅ |
| Anthropic | `AnthropicChatModel` | ✅ | ✅ | ✅ | ✅ |
| Gemini | `GeminiChatModel` | ✅ | ✅ | ✅ | ✅ |
| Ollama | `OllamaChatModel` | ✅ | ✅ | ✅ | ✅ |

#### 2.1.2 模型调用接口

所有模型类均提供统一的接口：

```python
model = DashScopeChatModel(
    model_name="qwen-plus",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    generate_kwargs={"temperature": 0.0}
)

# 调用接口
response = model(
    messages,      # 输入消息
    tools,         # 工具函数 JSON schema
    tool_choice    # 工具选择模式
)
```

#### 2.1.3 工具调用

AgentScope 通过以下方式解决不同模型提供商在工具 API 方面的差异：

- 提供统一的工具调用结构 `ToolUseBlock`
- 提供统一的工具响应结构 `ToolResultBlock`
- 在模型类的 `__call__` 方法中提供统一的工具接口

#### 2.1.4 流式返回

启用流式返回：

```python
model = DashScopeChatModel(
    model_name="qwen-plus",
    stream=True  # 启用流式返回
)

# 流式返回为异步生成器
async for chunk in model(messages, tools, tool_choice):
    print(chunk)
```

流式返回结果为累加式，每个 chunk 中的内容包含所有之前的内容加上新生成的内容。

### 2.2 记忆 API

#### 2.2.1 短期记忆

```python
from agentscope.memory import InMemoryMemory

memory = InMemoryMemory()
memory.add(Msg("user", "Hello", "user"))
memory.get()
```

#### 2.2.2 长期记忆

```python
from agentscope.memory import LongTermMemory

# agent_control 模式：智能体自己管理
memory = LongTermMemory(control_mode="agent_control")

# static_control 模式：开发者管理
memory = LongTermMemory(control_mode="static_control")
```

### 2.3 工具 API

#### 2.3.1 定义工具

```python
from agentscope.tool import Toolkit

toolkit = Toolkit()

def get_weather(location: str) -> str:
    """获取天气信息"""
    return f"{location}今日天气：晴"

# 使用 Toolkit.register_tool_function() 注册工具
toolkit.register_tool_function(get_weather)
```

#### 2.3.2 工具包

```python
from agentscope.tool import Toolkit

toolkit = Toolkit(
    [get_weather, search_tool, calculate_tool],
    name="my_tools"
)
```

### 2.4 提示词格式化 API

```python
from agentscope.formatter import DashScopeChatFormatter

formatter = DashScopeChatFormatter(
    system_prompt="你是一个有用的助手",
    role_name="assistant"
)
```

### 2.5 消息 API

```python
from agentscope.message import Msg, TextBlock

# 简单文本消息
msg = Msg("assistant", "Hello, how can I help you?", "assistant")

# 带结构的内容
content = [
    TextBlock(text="Hello"),
    TextBlock(text="World")
]
msg = Msg("assistant", content, "assistant")
```

---

## 三、竞品对比分析

### 3.1 主流智能体框架对比

当前主流的智能体框架包括：AgentScope、LangChain/LangGraph、CrewAI、AutoGen、MetaGPT。下面对这些框架进行详细对比。

#### 3.1.1 功能特性对比

| 特性 | AgentScope | LangChain/LangGraph | CrewAI | AutoGen | MetaGPT |
|------|------------|---------------------|--------|---------|---------|
| 多智能体优先 | ✅ 原生支持 | ⚠️ 通过 LangGraph | ✅ 原生支持 | ⚠️ 需定制 | ✅ 原生支持 |
| 状态管理 | ✅ 内置 | ⚠️ 需自行实现 | ❌ 无 | ❌ 无 | ❌ 无 |
| 实时介入 | ✅ 支持 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| 多模态支持 | ✅ 文本/图像/音频 | ⚠️ 需集成 | ⚠️ 需集成 | ⚠️ 需集成 | ⚠️ 需集成 |
| 分布式部署 | ✅ 原生支持 | ❌ 无 | ❌ 无 | ❌ 无 | ❌ 无 |
| 可观测性 | ✅ OTel 集成 | ⚠️ 基础日志 | ⚠️ 有限日志 | ⚠️ 基础追踪 | ⚠️ 流程日志 |
| 容错能力 | ✅ 自动重试+格式校验 | ⚠️ 需手动实现 | ⚠️ 基础错误捕获 | ⚠️ 简单重试 | ❌ 无 |
| 上手难度 | 中 | 高 | 中 | 中 | 低 |
| 适用场景 | 企业级生产环境 | 复杂流程建模 | 代码生成 | 编程协作 | 快速原型 |

#### 3.1.2 架构设计对比

**AgentScope 架构特点**：
- 三层架构：核心框架层、Runtime 层、Studio 层
- 消息驱动架构，MsgHub 实现智能体间通信路由
- 基于 Actor 模型的分布式模式
- 内置安全沙箱机制

**LangChain/LangGraph 架构特点**：
- 链式调用为核心
- 状态机模式（LangGraph）
- 需要开发者自行设计智能体逻辑
- 适合复杂流程建模和学术研究

**CrewAI 架构特点**：
- 角色扮演为核心
- 固定模板
- 适合快速原型和简单协作

**AutoGen 架构特点**：
- 对话驱动
- 多智能体协作
- 适合代码生成和编程协作

**MetaGPT 架构特点**：
- 软件开发工作流
- 标准操作程序（SOP）
- 适合代码生成工作流

### 3.2 AgentScope 优势分析

#### 3.2.1 多智能体优先设计

AgentScope 从设计之初就将多智能体作为核心概念，而不是像其他框架那样通过扩展或插件来实现。这使得：

- 多智能体通信更自然
- 状态管理更统一
- 协作模式更丰富

#### 3.2.2 完善的状态管理

AgentScope 提供了内置的嵌套式状态管理机制，使得：

- 智能体可以随时保存和恢复状态
- 支持实时介入和中断处理
- 便于构建可恢复的长时任务

#### 3.2.3 消息驱动架构

基于 MsgHub 的消息驱动架构实现了：

- 智能体间松耦合通信
- 多模态数据统一传输
- 全链路可追溯

#### 3.2.4 生产级支持

AgentScope 提供了完整的企业级功能：

- 安全沙箱隔离
- 分布式部署
- OTel 可观测性
- 多语言 SDK（Python、Java）

### 3.3 选型建议

| 场景 | 推荐框架 |
|------|---------|
| 企业级生产环境、复杂任务自动化 | AgentScope |
| 复杂流程建模、学术研究 | LangGraph |
| 代码生成、编程协作 | AutoGen |
| 单智能体工具扩展 | LangChain |
| 快速原型、简单协作 | CrewAI |
| 开发流程自动化 | MetaGPT |

---

## 四、学术背景资料

### 4.1 多智能体系统基础

多智能体系统（Multi-Agent System, MAS）是指由多个相互作用的智能体组成的系统，这些智能体能够：

- 自主决策
- 相互通信
- 协作完成复杂任务

#### 4.1.1 关键概念

- **智能体（Agent）**：能够感知环境、做出决策并执行动作的实体
- **通信协议**：智能体之间交换信息的规则和格式
- **协调机制**：管理智能体之间协作的策略
- **任务分解**：将复杂任务划分为可并行执行的子任务

#### 4.1.2 多智能体架构模式

| 模式 | 描述 | 适用场景 |
|------|------|---------|
| 层次式 | 明确的指挥链 | 复杂决策树 |
| 平等式 | 智能体平等协作 | 协作问题解决 |
| 混合式 | 结合层次和平等 | 复杂动态环境 |

### 4.2 LLM 智能体技术

基于大语言模型的智能体（LLM-based Agent）将 LLM 作为智能体的"大脑"，实现：

- 自然语言理解
- 推理和规划
- 工具调用
- 记忆管理

#### 4.2.1 ReAct 范式

ReAct（Reasoning and Acting）是最常用的 LLM 智能体范式，将推理和行动交替进行：

```
Thought → Action → Observation → Thought → ...
```

这种范式的优势：
- 可解释性强
- 错误可追溯
- 适合复杂任务

#### 4.2.2 Plan-and-Execute 范式

Plan-and-Execute 采用"先规划，后执行"的策略：

1. **规划阶段**：LLM 生成完整的任务计划
2. **执行阶段**：按计划逐步执行任务

优势：
- 全局视野
- 适合长时任务
- 便于人工检查

### 4.3 AgentScope 学术贡献

AgentScope 在学术论文中提出了以下创新：

#### 4.3.1 论文引用

```
@article{agentscope,
  title={AgentScope: A Flexible yet Robust Multi-Agent Platform},
  author={Dawei Gao, Zitao Li, Xuchen Pan, et al.},
  journal={CoRR},
  volume={abs/2402.14034},
  year={2024}
}

@article{agentscope_v1,
  title={AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications},
  author={Dawei Gao, Zitao Li, et al.},
  journal={CoRR},
  volume={abs/2508.16279},
  year={2025}
}
```

#### 4.3.2 核心创新点

1. **程序化消息交换机制**：简化多智能体编程
2. **零代码拖拽编程工作站**：降低使用门槛
3. **自动提示词调优机制**：优化智能体性能
4. **容错设计**：优雅处理应用错误

---

## 五、推荐阅读清单

### 5.1 官方文档

| 资源 | 链接 | 描述 |
|------|------|------|
| AgentScope 主文档 | https://docs.agentscope.io/ | 英文官方文档 |
| AgentScope 中文文档 | https://doc.agentscope.io/zh_CN/ | 中文官方文档 |
| GitHub 仓库 | https://github.com/agentscope-ai/agentscope | 源代码和示例 |
| AgentScope Samples | https://github.com/agentscope-ai/agentscope-samples | 示例代码集合 |
| AgentScope Java | https://java.agentscope.io/ | Java 版本文档 |

### 5.2 教程资源

| 资源 | 链接 | 描述 |
|------|------|------|
| 快速开始 | https://doc.agentscope.io/zh_CN/tutorial/quickstart.html | 5 分钟上手教程 |
| 核心概念 | https://doc.agentscope.io/zh_CN/tutorial/quickstart_key_concept.html | 核心概念详解 |
| 智能体教程 | https://doc.agentscope.io/zh_CN/tutorial/task_agent.html | 智能体开发教程 |
| 模型教程 | https://doc.agentscope.io/zh_CN/tutorial/task_model.html | 模型配置教程 |

### 5.3 学术论文

| 论文 | 链接 | 描述 |
|------|------|------|
| AgentScope 论文 | https://arxiv.org/abs/2402.14034 | 原始论文 |
| LLM-based Multi-Agent Survey | https://github.com/taichengguo/LLM_MultiAgents_Survey_Papers | 多智能体综述 |

### 5.4 社区资源

| 资源 | 链接 | 描述 |
|------|------|------|
| GitHub Issues | https://github.com/agentscope-ai/agentscope/issues | 问题反馈 |
| Discussion | https://github.com/modelscope/agentscope/discussions | 社区讨论 |

---

## 六、来源链接

本文档所有内容均来自以下实际访问的网页资源：

1. AgentScope 官方文档 - https://docs.agentscope.io/
2. AgentScope 中文文档 - https://doc.agentscope.io/zh_CN/
3. AgentScope 核心概念 - https://doc.agentscope.io/zh_CN/tutorial/quickstart_key_concept.html
4. AgentScope 智能体教程 - https://doc.agentscope.io/zh_CN/tutorial/task_agent.html
5. AgentScope 模型教程 - https://doc.agentscope.io/zh_CN/tutorial/task_model.html
6. AgentScope GitHub - https://github.com/agentscope-ai/agentscope
7. AgentScope Samples - https://github.com/agentscope-ai/agentscope-samples
8. AgentScope Java 文档 - https://java.agentscope.io/
9. AgentScope Java Multi-Agent - https://java.agentscope.io/en/multi-agent/overview.html
10. AgentScope 论文 - https://arxiv.org/pdf/2402.14034
11. Arsum Agentic AI 框架对比 - https://arsum.com/blog/posts/agentic-ai-frameworks-comparison/
12. instinctools AutoGen 对比 - https://www.instinctools.com/blog/autogen-vs-langchain-vs-crewai/
13. 博客园 AgentScope 1.0 深度解析 - https://www.cnblogs.com/tlnshuju/p/19332287
14. Towards Dev AgentScope 教程 - https://medium.com/towardsdev/agentscope-building-real-world-ai-agents-that-actually-work-2ace602ab387

---

## 七、总结

本文档涵盖了 AgentScope 官方文档的核心内容，包括：

1. **核心概念**：状态管理、消息驱动、工具系统、智能体抽象、提示词格式化、长期记忆
2. **API 参考**：统一的模型接口（OpenAI/DashScope/Anthropic/Gemini/Ollama）、记忆 API、工具 API、消息 API
3. **竞品对比**：AgentScope 在多智能体优先、状态管理、实时介入、分布式部署方面具有优势
4. **学术背景**：多智能体系统基础、ReAct 范式、Plan-and-Execute 范式

建议结合 [reference_best_practices.md](reference_best_practices.md) 阅读设计模式和生产实践部分。

---

*文档版本：2026年4月*
*最后更新：2026年4月30日*
