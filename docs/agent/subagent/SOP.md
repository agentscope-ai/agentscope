# SOP：src/agentscope/agent.subagent 子模块

## 一、功能定义（Scope/非目标）

### 1. 设计思路和逻辑
- 目标：提供“Agent 作为工具（agent-as-tool）”的轻量骨架，统一子代理（SubAgent）的输入契约、上下文注入、执行与结果折叠，避免与主 Agent 的对外交互逻辑耦合。
- SubAgent 仍是完整 Agent：`SubAgentBase` 直接继承 `AgentBase`，天然具备 `reply/observe/print/interrupt`、Hook、Memory、Toolkit 等能力。
  - `export_agent(...)` 会复制 Host 的共享资源（日志、Tracing、`FileDomainService`、Session、Long-term Memory、安全限制）并为子代理构建独立的 Toolkit/Memory。
  - 因此，子代理在 `reply(...)` 中可以像普通 Agent 一样调用工具、执行规划，甚至再次调度其他子代理。
- `delegate` 只是桥梁：其职责是读取 Host 生成的 `DelegationContext` → 调用子类实现的 `reply(input_obj)` → 将返回的 `Msg` 折叠为 `ToolResponse`，以便 Host 通过统一工具通道消费结果。
  - `delegate` 不限制 `reply` 的具体实现，`reply` 可以运行完整的推理链或 ReAct 循环，最终返回 `Msg`。
- 输入契约：子代理必须以 Pydantic `BaseModel` 子类 `InputModel` 定义输入；注册期与运行期均以此模型生成/校验 JSON Schema 与参数。
- 生命周期：每次调用前构造全新实例（默认短期内存隔离），执行 `reply(input_obj)` 后折叠为单个 `ToolResponse(is_last=True)` 返回。
- 资源注入：子代理不得自行扩大权限或创建独立策略，必须使用 `export_agent` 注入的共享资源。
- 静默执行：子代理默认关闭控制台与 MsgHub 外泄，避免中间态噪声；Host 负责广播。

### 2. 架构设计
为了凸显“子代理仍是完整 Agent”，架构可以理解为三层：
1. **封装层**：Host 通过 `make_subagent_tool` 生成包装函数，负责输入校验与上下文压缩。
2. **执行层**：`SubAgentBase.export_agent` 创建全新的子代理实例，复制/共享 Host 的资源，使其具备完整 Agent 能力。
3. **折叠层**：`delegate` 调用子代理的 `reply`，并把 `Msg` 折叠为 `ToolResponse` 供 Host 消费。

```mermaid
flowchart TD
    Host[Host Agent] --> TK[Host Toolkit]
    TK --> SubSpec[SubAgentSpec]
    TK --> Wrapper[subagent_tool]
    Wrapper -->|export_agent| SA["SubAgent · 完整 AgentBase"]
    SA -->|reply(InputModel)| MsgOut[Msg 回复]
    MsgOut -->|delegate| TR[ToolResponse 返回给 Host]
```

### 3. 核心组件逻辑
- `SubAgentBase`
  - `InputModel`: Pydantic 输入契约（必填）。
  - `export_agent(...)`: 复制共享句柄、装配自有 Toolkit/Memory，返回子代理实例（不做健康检查动作）。
  - `delegate(input_obj, *, delegation_context)`: 写入压缩上下文 → 调用 `reply(input_obj)` → 将 `Msg` 折叠为 `ToolResponse`；异常统一为 `metadata.unavailable=True`。
- `make_subagent_tool`
  - 注册期：解析 `InputModel` 生成 JSON Schema，并执行一次“构造探测”（确保可实例化与最小环境可用），失败则拒绝注册并标记 `SubAgentUnavailable`。
  - 运行期：校验来参 → 压缩父上下文 → `export_agent(...).delegate(...)` → 返回 `ToolResponse`。

### 4. 关键设计模式
- 适配器：`make_subagent_tool` 将子代理封装为 Toolkit 工具（函数）。
- 策略：工具白名单（allowlist）通过 `spec.tools` 装配到子代理自有 Toolkit；不复制 Host 的工具集合。
- 资源隔离：短期 Memory、Toolkit、Hook 均为子代理私有；仅共享受控资源束（PermissionBundle）。

### 5. 其他组件的交互
- Host ReAct 流程：`_reasoning` 产出 tool_use → Toolkit 调用子代理包装函数 → 子代理返回 `ToolResponse` → Host `generate_response/print`。
- Filesystem：所有文件读写经 Host 注入的 `FileDomainService`；前缀/授权由 Host 决定，子代理不得扩大。

## 二、文件/类/函数/成员变量映射到 src 路径

- `src/agentscope/agent/_subagent_base.py`
  - 类：`SubAgentBase`、`PermissionBundle`、`ContextBundle`、`DelegationContext`
  - 方法：
    - `get_input_model()`: 读取并校验 `InputModel` 是否声明。
    - `export_agent(...)`: 构造实例并注入共享资源。
    - `delegate(input_obj, *, delegation_context)`: 统一入口，折叠为 `ToolResponse`。
    - `_pre_context_compress(parent_context, input_obj)`: 默认压缩上游上下文；子类可覆盖。
- `src/agentscope/agent/_subagent_tool.py`
  - 数据类：`SubAgentSpec(name, tools=None)`
  - 工厂：`make_subagent_tool(cls, spec, *, host, tool_name=None, ephemeral_memory=True)` → `(ToolFunction, register_kwargs)`
  - 关键 helper：`_build_context_bundle`、`_build_permissions`、`_build_model_schema`、`_build_sample_input`

## 三、关键数据结构与对外接口（含类型/返回约束）

- 输入模型：`class InputModel(BaseModel)`（子类定义）。注册与运行时均以 `model_json_schema()` 生成工具参数面；`preset_kwargs` 不出现在 JSON Schema。
- 工具包装函数签名（概念）：`async def <subagent_tool>(**input_fields) -> ToolResponse`
  - JSON Schema：严格等价于 `InputModel` 字段集合与 `required`；不得隐藏注入字段。
- `ToolResponse`
  - `content`: `TextBlock|ImageBlock|AudioBlock` 列表；业务结果写入此处。
  - `metadata`（保留域）：由骨架写入 `{subagent, supervisor, delegation_context, response_metadata?}`；不得新增业务字段。
- 失败语义：
  - 入参校验失败 → `ToolResponse(metadata.unavailable=True, content=['Subagent input validation failed...'])`
  - 运行异常 → `ToolResponse(metadata.unavailable=True, content=['Subagent execution unavailable...'])`

## 四、与其他模块交互（调用链与责任边界）

典型调用链：
`ReActAgent._acting → Toolkit.call_tool_function(<subagent_tool>) → InputModel.model_validate(...) → SubAgentBase.export_agent(..., input_obj) → SubAgentBase.delegate(...) → ToolResponse → ReActAgent.generate_response/print`

责任边界：
- Host 决策/编排、广播与可观察性；子代理只聚焦单次任务与工具编排。
- 文件系统权限由 Host 注入并强制；子代理仅消费，不声明策略。
- 子代理不可读写 Host 的短期 memory 与 MsgHub；仅通过 `ToolResponse` 交换。
- 子代理同样可以作为普通 Agent 直接调用：
  ```python
  toolkit = build_toolkit()
  agent = SearchSubAgent(permissions=..., spec_name="search", tools=...)
  msg = Msg("user", query, "user")
  reply = await agent(msg)
  text = reply.get_text_content() or ""
  ```
  当需要通过 Toolkit 对外暴露时，再由骨架的 `delegate` 将 `Msg` 包装为 `ToolResponse`，以履行工具注册契约。

## 五、测试文件

- 绑定测试（覆盖要点）：
  - `tests/agent/test_subagent_lifecycle.py`（注册探测/构造）
  - `tests/agent/test_subagent_context_compress.py`（上下文压缩调用次数）
  - `tests/agent/test_subagent_error_propagation.py`（异常封装为 unavailable）
  - `tests/agent/test_subagent_permissions.py`（权限束注入）
  - `tests/agent/test_subagent_fs_namespace.py`、`test_subagent_fs_auto_inherit.py`（命名空间与授权）
  - `tests/agent/test_subagent_parallel.py`（并行隔离与顺序）
  - `tests/agent/test_subagent_allowlist_schema.py`（工具白名单/Schema 暴露）

未覆盖建议：
- 统一输入契约的“字段集合等价”属性测试（当有新增子代理时作为模板复用）。
