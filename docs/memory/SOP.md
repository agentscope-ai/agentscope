# SOP：src/agentscope/memory 模块

## 一、功能定义（Scope/非目标）
### 1. 设计思路和逻辑
- 为 Agent 提供统一的短期/长期记忆接口，分别覆盖对话上下文的快速访问与跨轮次、跨会话的持久回忆。
- 将“开发者驱动”和“Agent 自主”两种记忆控制模式结合：短期记忆由 Agent 自动维护，长期记忆既可静态注入也可通过工具函数让 LLM 主动读写。
- 保持记忆层与业务行为解耦：模块负责存储、检索、序列化，不对记忆内容做语义判断。

### 2. 架构设计
```mermaid
graph TD
    subgraph Short-term
        MB[MemoryBase]
        IM[InMemoryMemory]
    end
    subgraph Long-term
        LTB[LongTermMemoryBase]
        MEM0[Mem0LongTermMemory]
    end
    Agent --> MB
    Agent --> LTB
    LTB -->|tool functions| Agent
    MEM0 --> EmbeddingModel
    MEM0 --> ChatModel
    MEM0 --> VectorStore
    MB --> StateModule
    LTB --> StateModule
```

### 3. 核心组件逻辑
- **MemoryBase**：定义 `add/delete/retrieve/get_memory/clear/size/state_dict/load_state_dict` 等抽象方法；继承 `StateModule` 以便会话持久化。
- **InMemoryMemory**：用列表保存 `Msg`；`add` 支持去重；`delete` 校验索引；`state_dict`/`load_state_dict` 将 `Msg` 序列化/反序列化。
- **LongTermMemoryBase**：定义开发者调用的 `record`/`retrieve`，以及暴露给 Agent 的工具 `record_to_memory`/`retrieve_from_memory`；具体实现决定是否提供这些工具。
- **Mem0LongTermMemory**：使用 mem0 库整合 LLM、Embedding、向量存储；支持配置 `agent_name/user_name/run_name` 元数据；根据参数构建 mem0 `MemoryConfig`，并通过工具函数读写；同时提供静态 `record`/`retrieve` 以配合 Agent 自动模式。
- **辅助函数**：`_mem0_utils` 定义适配 mem0 所需的 `AgentScopeEmbedding/AgentScopeLLM`。模块外部可扩展其他长期记忆实现。

### 4. 关键设计模式
- **模板方法**：`MemoryBase`、`LongTermMemoryBase` 规定接口，具体实现定义存储策略。
- **策略模式**：长期记忆读写可切换为静态（开发者调用 `record/retrieve`）或 Agent 控制（工具函数暴露）。
- **适配器**：`Mem0LongTermMemory` 适配 mem0 配置体系、向量存储与 AgentScope 的模型/嵌入抽象。

### 5. 其他组件的交互
- **Agent**：ReActAgent 在 `_reasoning` 前调用短期 `memory.add` 记录输入，必要时使用 `long_term_memory.retrieve` 注入提示；回复完成后根据模式调用 `record` 或暴露的工具供 LLM 自主写入。
- **Plan/RAG/Tool**：长期记忆的工具函数通过 Toolkit 注册，与其他工具并存。
- **Session**：由于继承 `StateModule`，记忆内容可被 `JSONSession` 等会话管理器保存/恢复。
- **Embedding/Model**：`Mem0LongTermMemory` 可注入 AgentScope 的模型与向量嵌入，实现统一配置。
- **责任边界**：记忆模块不裁剪内容、不执行 RAG 检索排名（除 mem0 集成逻辑），异常向上抛由调用方决定重试与提示。

## 二、文件/类/函数/成员变量映射到 src 路径
- `src/agentscope/memory/_memory_base.py`
  - `MemoryBase`：抽象基类；声明 `add/delete/retrieve/size/clear/get_memory/state_dict/load_state_dict`。
- `src/agentscope/memory/_in_memory_memory.py`
  - `InMemoryMemory`：列表存储实现；属性 `content: list[Msg]`；实现所有抽象方法。
- `src/agentscope/memory/_long_term_memory_base.py`
  - `LongTermMemoryBase`：定义 `record/retrieve`（开发者 API）与 `record_to_memory/retrieve_from_memory`（工具函数）。
- `src/agentscope/memory/_mem0_long_term_memory.py`
  - `Mem0LongTermMemory`：基于 mem0，实现长期记忆；构造函数支持 `agent_name/user_name/run_name/model/embedding_model/vector_store_config/mem0_config`；实现 `record`/`retrieve` 和工具函数。
  - 内部 `_create_agentscope_config_classes` 适配 mem0 的 LLM/Embedding 配置。
- `src/agentscope/memory/_mem0_utils.py`
  - 提供 `AgentScopeLLM`、`AgentScopeEmbedding`，作为 mem0 provider 的适配器。
- `src/agentscope/memory/__init__.py`
  - 导出 `InMemoryMemory`、`Mem0LongTermMemory`、基类等。
- `src/agentscope/memory/CLAUDE.md`
  - 记录调用链，文档更新需同步。

## 三、关键数据结构与对外接口（含类型/返回约束）
- `MemoryBase` 抽象方法
  - `async add(memories: Msg | list[Msg] | None, allow_duplicates: bool = False) -> None`
  - `async delete(index: Iterable[int] | int) -> None`
  - `async retrieve(*args, **kwargs) -> None`（默认未实现）
  - `async get_memory() -> list[Msg]`
  - `async clear() -> None`
  - `async size() -> int`
  - `state_dict() -> dict` / `load_state_dict(state_dict: dict, strict: bool = True) -> None`
- `InMemoryMemory` 具体行为
  - 添加时可去重（基于 `Msg.id`）；`delete` 校验索引；`retrieve` 当前未实现，调用者需扩展。
- `LongTermMemoryBase`
  - `async record(msgs: list[Msg | None], **kwargs) -> None`：开发者调用手动写入；
  - `async retrieve(msg: Msg | list[Msg] | None, **kwargs) -> str`：返回注入系统提示的字符串；
  - `async record_to_memory(thinking: str, content: list[str], **kwargs) -> ToolResponse`：工具形式写入；
  - `async retrieve_from_memory(keywords: list[str], **kwargs) -> ToolResponse`：工具形式检索。
- `Mem0LongTermMemory` 特定接口
  - 构造函数参数详见源码；`record`/`retrieve` 与 mem0 集成；工具函数返回 `ToolResponse(TextBlock)`。
  - 依赖 mem0，自带配置校验；若缺少必要参数将抛 `ValueError` 或 `ImportError`。
- 数据结构
  - 记忆内容为 `Msg`（结构在 `src/agentscope/message/_message_base.py`）；长期记忆工具返回 `ToolResponse`（参见工具模块）。

## 四、与其他模块交互（调用链与责任边界）
- **短期记忆链路**：Agent `reply` → `memory.add` 记录输入 → 推理结束后 `memory.add` 输出 → 外部调用可通过 `get_memory` 获取历史。
- **长期记忆链路**：
  - 静态模式：Agent 在 `reply` 前调用 `long_term_memory.retrieve` 并把字符串嵌入提示；结束后调用 `record`。
  - Agent 控制模式：`long_term_memory.record_to_memory` / `retrieve_from_memory` 以工具形式注册给 Toolkit，让 LLM 自主操作。
- **外部依赖**：`Mem0LongTermMemory` 使用 `EmbeddingModelBase`、`ChatModelBase`、向量存储配置；这些依赖需由调用方提供。
- **责任边界**：模块不保证检索结果质量、不做消息摘要；开发者应在使用前明确提示策略；外部存储异常需在上层捕获并提示用户。

## 五、测试文件
- 绑定文件：间接：`tests/react_agent_test.py`
- 覆盖点：长期记忆检索/记录错误路径、静态/代理控制模式差异.
