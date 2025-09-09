# AgentScope 智能体开发核心准则 (Coding Rule)

## 总览

本文档旨在为 AI Coder 提供一套标准化的 AgentScope 智能体（Agent）开发框架。所有智能体的设计与实现都应严格遵循本准则，以确保代码质量、一致性及可扩展性。准则的核心思想是：**理论源于官方文档 `docs/tutorial/en`，实践参照官方示例 `Examples`**。

## 1. 核心原则 (Core Principles)

### 1.1. 继承原则：以 `ReActAgent` 为标准，以 `AgentBase` 为基础

所有智能体都必须继承自 AgentScope 的基础类。

* **标准实践**: 对于需要执行工具调用、进行多步推理的任务，**应优先继承 `ReActAgent`**。这是框架中最成熟、功能最丰富的智能体实现，内置了“思考-行动”循环、工具调用、即时控制等多项核心功能。
    * **实践示例**: `examples/react_agent/main.py` 展示了一个标准的 `ReActAgent` 实例化过程。

* **自定义实践**: 仅在需要构建完全自定义、非 ReAct 模式的简单智能体时，才直接继承 `AgentBase`。在这种情况下，您必须自行实现 `reply` 方法的核心逻辑。
    * **实践示例**: `docs/tutorial/en/src/quickstart_agent.py` 中的 `MyAgent` 类可作为参考。

### 1.2. 模块化与依赖注入原则

一个智能体应该被视为一个由核心组件构成的系统。在设计时，应将 **模型 (Model)**、**记忆 (Memory)**、**工具包 (Toolkit)** 和 **格式器 (Formatter)** 作为独立模块，并在智能体初始化时注入。这确保了智能体的灵活性和可测试性。

* **理论基础**: `docs/tutorial/en/src/quickstart_key_concept.py` 强调了这些核心组件的重要性。
* **实践示例**: `examples/react_agent/main.py` 中的 `ReActAgent` 初始化过程完美地诠释了此原则，所有核心依赖项都作为参数传入。

### 1.3. 示例驱动原则

在遇到设计或实现上的疑问时，**应首先查阅 `examples` 目录中的官方示例**。这些示例是经过验证的最佳实践。

* **基础场景**: 参考 `examples/react_agent/main.py`。
* **多智能体协作**: 参考 `examples/workflows/multiagent_debate/main.py`。
* **复杂任务规划**: 参考 `examples/meta_planner_agent/`。

## 2. `ReActAgent` 作为标准实现

除非有特殊理由，否则所有新智能体都应基于 `ReActAgent` 构建。

### 2.1. 初始化与组件配置

`ReActAgent` 的初始化必须清晰、完整，并遵循以下规则：

* **`model` 与 `formatter` 的一致性**: 必须选择与模型 API 相匹配的格式器（Formatter）。例如，使用 `DashScopeChatModel` 时，应搭配 `DashScopeChatFormatter`。在多智能体对话场景中，必须使用 `MultiAgentFormatter` 系列的格式器。

* **`toolkit` 的构建**:
    1.  应实例化一个 `Toolkit` 对象。
    2.  使用 `toolkit.register_tool_function()` 方法注册所有该智能体需要使用的工具。

* **`memory` 的配置**:
    1.  对于需要存储对话历史的智能体，必须配置 `memory`。
    2.  `InMemoryMemory` 是最常用的短期记忆实现。
    3.  若需跨会话记忆，应使用长期记忆方案，如 `Mem0LongTermMemory`。

### 2.2. 系统提示词 (System Prompt) 的设计

系统提示词是智能体行为的“宪法”，必须精心设计。

* **结构化**: 应将提示词结构化，明确定义智能体的 **身份 (Identity)**、**核心使命 (Core Mission)**、**操作范式 (Operation Paradigm)**、**重要约束 (Important Constraints)** 等。
* **模块化**: 对于复杂的提示词，应将其分解为多个部分（如工具使用规则、报告格式等），并从外部文件加载。
* **最佳实践**: `examples/agent_deep_research/built_in_prompt/` 目录下的 Markdown 文件是生产级提示词工程的典范，应作为主要参考。

## 3. 关键能力实现 (Implementation of Key Capabilities)

### 3.1. 结构化输出 (Structured Output)

当需要智能体的回复严格遵守特定 JSON 格式时，必须使用 `structured_model` 参数。

* **实现方式**:
    1.  使用 Pydantic 的 `BaseModel` 定义所需的输出数据结构。
    2.  在调用智能体的 `reply` 方法时，将该 Pydantic 模型作为 `structured_model` 参数传入。
    3.  从回复消息的 `metadata` 字段中获取经过验证的结构化数据。
* **理论基础**: `docs/tutorial/en/src/task_agent.py` 中的 "Structured Output" 章节。

### 3.2. 状态持久化与恢复

对于需要长时间运行的任务，必须考虑智能体状态的持久化，以便在中断后能够恢复。

* **核心机制**: 所有智能体都继承自 `StateModule`，支持通过 `state_dict()` 和 `load_state_dict()` 进行状态的序列化与反序列化。
* **实现方式**: 使用 `agentscope.session` 中的会话管理器（如 `JSONSession` 或自定义的 `SqliteSession`）来统一保存和加载一个或多个智能体的状态。
* **实践示例**: `examples/functionality/session_with_sqlite/main.py` 展示了如何实现和使用自定义的 `SqliteSession` 来持久化智能体的记忆。

## 4. 多智能体架构模式 (Multi-Agent Architecture Patterns)

### 4.1. 广播式通信 (Broadcast Communication)

适用于多个智能体需要共享上下文进行协作的场景（如辩论、游戏）。

* **核心组件**: `MsgHub`。它是一个异步上下文管理器，能自动将参与者 (`participants`) 的回复广播给所有其他成员。
* **关键配置**: 必须使用 `MultiAgentFormatter` 系列的格式器，以区分不同发言者。
* **实践示例**: `examples/workflows/multiagent_conversation/main.py` 和 `examples/game/werewolves/main.py` 均使用 `MsgHub` 来管理群组对话。

### 4.2. 分层式编排 (Hierarchical Orchestration)

适用于需要将一个宏大任务分解为多个子任务，并由不同智能体分工完成的超复杂场景。

* **架构模式**: **规划者-执行者 (Planner-Worker)** 模式。
    * **规划者 (Planner)**: 一个高阶智能体（如 `MetaPlanner`），其核心职责是 **任务分解** 和 **智能体编排**。它不亲自执行具体任务。
    * **执行者 (Worker)**: 根据规划者的指令创建，被赋予特定的工具和目标来完成某个子任务。
* **核心实现**: 规划者智能体将“创建和执行其他智能体”视为自身的工具 (`tool`)。
* **实践示例**: `examples/meta_planner_agent/` 是此高级模式的完整实现。
    * `_meta_planner.py` 中的 `MetaPlanner` 智能体。
    * `_planning_tools/_worker_manager.py` 中的 `create_worker` 和 `execute_worker` 方法是此模式的关键，它们被注册为规划者的工具。