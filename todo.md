# TODO：SubAgent 委派落地

## 执行步骤清单
- [x] 对齐 `docs/agent/SOP.md`，完成“实战示例”与资源继承表述（commit 0485093，feature/subagent-doc-example）。
- [x] 实现 `SubAgentBase`：提供 `export_agent`、`delegate`、`_pre_context_compress`、`healthcheck`，行为与 SOP 一致。
  - `export_agent`：复制 PermissionBundle 共享句柄，仅执行 `healthcheck()`；不做上下文压缩；失败抛 `SubAgentUnavailable`。
  - `delegate`：基于 Host 提供的 `delegation_context` 初始化短期 memory → 运行 → 聚合单个 `ToolResponse(is_last=True)`；异常封装 `metadata.unavailable=True`。
  - `_pre_context_compress`：产出五段结构（`task_summary`、`recent_events≤4`、`long_term_refs`、`workspace_pointers`、`safety_flags`）。
  - `healthcheck`：仅用于注册阶段可执行性检查。
- [x] 落地 `make_subagent_tool` 等工厂函数，负责将任意 `SubAgentBase` 子类包装为 Toolkit 工具，保证骨架在注册/调用阶段的职责分离。
  - 注册阶段执行一次性 `export_agent(...).healthcheck()`，失败时记录 warning 并跳过工具注册。
  - 运行阶段每次调用均重新实例化子代理，注入 `preset_kwargs` 传递 `permissions`、`parent_context`、`task` 等。
  - 返回的工具函数对外仅暴露必要入参（如 `task_summary`），其余通过封装传递，输出单个 `ToolResponse`。
  - wrapper 负责把 Host `delegation_context` 注入 `load_delegation_context`，并在异常时写入 `metadata["subagent"]`、`metadata["supervisor"]` 方便审计。
- [x] 资源注入与上下文传递：复制 Logger/Tracing/FileSystem/Session/Long-term Memory 等堆资源；短期 memory/Hook/MsgHub 独立。`_acting` 中执行 `_pre_context_compress`，将结果写入“最新的 user 消息” `Msg.metadata[delegation_context]`。
- [x] 在 `tests/agent` 下实现与下表一一对应的测试用例，覆盖调用链、上下文压缩、异常封装、健康检查、并行、白名单、FS 命名空间、广播约束、权限复制与元数据契约。
  

## 验收清单（规范→不变量→测试→示例→代码）
| 规范 | 不变量 | 验证测试/脚本 | 示例/E2E 见证 | 代码落点 |
| --- | --- | --- | --- | --- |
| 子代理以工具形态暴露，调用路径固定为 `_acting` → `make_subagent_tool` → `export_agent` → `delegate` | Host 自身不直接使用工具；所有工具结果经子代理聚合为单个 ToolResponse | `tests/agent/test_subagent_tool.py::test_host_without_direct_tools` | 文档示例链路 | `src/agentscope/agent/_subagent_tool.py` |
| 共享堆资源（Logger/Tracing/FileSystem/Session/Long-term Memory），短期 memory/Hook/MsgHub 独立 | Host 进入子代理前 `_pre_context_compress` → 写入“最新的 user 消息” metadata；`export_agent` 不做压缩 | `tests/agent/test_subagent_memory_isolation.py` | 文档示例步骤 | `src/agentscope/agent/_react_agent.py::_acting` |
| 子代理以工具形态暴露，调用路径固定为 `_acting` → `export_agent` → `delegate` | 骨架默认实现只做上下文传递与 ToolResponse 汇聚，不夹带业务逻辑 | `tests/agent/test_subagent_tool.py::test_host_without_direct_tools` | 骨架最小子类 + Mock Toolkit | `src/agentscope/agent/_subagent_tool.py` |
| 共享堆资源（Logger/Tracing/FileSystem/Session/Long-term Memory）与短期 memory 隔离 | Host 在进入子代理前执行 `_pre_context_compress`，子代理仅注入共享句柄 | `tests/agent/test_subagent_memory_isolation.py::test_subagent_memory_isolation` | 文档示例步骤 | `src/agentscope/agent/_react_agent.py::_acting` |
| 工具白名单 & JSON Schema | allowlist 工具被克隆，`Toolkit.get_json_schemas()` 含子代理条目 | `tests/agent/test_subagent_allowlist_schema.py::test_subagent_allowlist_schema` | Allowlist 场景 | `src/agentscope/agent/_subagent_base.py::_hydrate_toolkit` |
| 文件系统命名空间限制 | 仅允许 `/workspace/subagents/<name>/` 前缀写入，越权抛 `AccessDeniedError` | `tests/agent/test_subagent_fs_namespace.py::test_subagent_filesystem_namespace` | in-memory FS 验证 | `src/agentscope/agent/_subagent_base.py::__init__` |
| 并行调用互不污染 | 并行执行时短期 memory/顺序与 `asyncio.gather` 结果一致 | `tests/agent/test_subagent_parallel.py::test_subagent_parallel_calls` | 并行子任务场景 | `src/agentscope/agent/_subagent_tool.py::_invoke_subagent` |
| 异常封装为 `ToolResponse(metadata.unavailable=True)` | 超时/异常封装为不可用响应，不冒泡到 Host | `tests/agent/test_subagent_error_propagation.py::test_subagent_error_propagation` | 文档示例 | `src/agentscope/agent/_subagent_base.py::delegate` |
| 健康检查仅注册阶段一次 | 注册失败即跳过工具；运行期不再重复 healthcheck | `tests/agent/test_subagent_lifecycle.py::test_subagent_lifecycle_healthcheck` | 骨架示例 | `src/agentscope/agent/_subagent_tool.py::make_subagent_tool` |
| 上下文压缩只由 Host 触发一次 | `_pre_context_compress` 在进入子代理前唯一执行 | `tests/agent/test_subagent_context_compress.py::test_context_compress_called_once` | 计数桩 | `src/agentscope/agent/_subagent_tool.py::_invoke_subagent` |
| 禁止对外流式输出 & MsgHub 泄漏 | 子代理执行期关闭 console/MsgHub，metadata 含 `subagent`/`supervisor` 且 `is_last=True` | `tests/agent/test_subagent_tool.py::test_host_without_direct_tools` | 骨架最小子类 | `src/agentscope/agent/_subagent_base.py::delegate` |

## 目录树（相关范围）
```
docs/
  agent/SOP.md
  tool/SOP.md
  memory/SOP.md
src/agentscope/
  agent/
    _agent_base.py
    _react_agent.py
    _subagent_base.py        # planned
    _subagent_tool.py        # planned
    CLAUDE.md
  tool/
    __init__.py
    _toolkit.py
  memory/
    __init__.py
    _memory_base.py
  session/
    __init__.py
    _state_module.py
  filesystem/
    __init__.py
    _filesystem_base.py
  tracing/
    __init__.py
    _tracing.py
tests/
  agent/
    test_subagent_tool.py
    test_subagent_memory_isolation.py
    test_subagent_error_propagation.py
    test_subagent_lifecycle.py
    test_subagent_allowlist_schema.py
    test_subagent_fs_namespace.py
    test_subagent_parallel.py
    test_subagent_context_compress.py
    test_subagent_permissions.py
```

## 交互模块与依赖
- Agent 核心：`AgentBase`、`ReActAgent` 负责 `_acting` 分发与 ToolResponse 汇总。
- Toolkit：注册子代理工具，统一执行回调协议。
- Memory：进入子代理前由 Host 触发 `_pre_context_compress` 并写入“最新的 user 消息” metadata，子代理 `load_delegation_context` 重建短期 memory；长期记忆共享但需 namespace 隔离。
- Session/StateModule：共享持久化句柄，子代理使用独立 namespace。
- FileSystem：复用父代理句柄，限定写入路径（共享堆）。
- Tracing/Logger：沿用父代理 pipeline，记录子代理 span 与错误。
- Pipeline/MsgHub：子代理默认禁用对外广播与对父级的流式输出。
- External Tools：`search_web`、`http_request`（限子代理 allowlist，可扩展）。
