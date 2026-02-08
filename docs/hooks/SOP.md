# SOP：src/agentscope/hooks 模块

## 一、功能定义（Scope/非目标）
### 1. 设计思路和逻辑
- 提供内置 Hook，尤其是与 AgentScope Studio 集成的消息转发 Hook，遵循 Agent Hook 协议（`pre_*`/`post_*`）在特定时刻执行副作用。
- 将网络交互与 Hook 注册逻辑集中管理，避免在业务代码中重复实现。

### 2. 架构设计
```mermaid
graph TD
    AgentBase -- register_class_hook --> StudioHook
    StudioHook -- HTTP POST --> AgentScope Studio
```

### 3. 核心组件逻辑
- `as_studio_forward_message_pre_print_hook(self, kwargs, studio_url, run_id)`：在 `AgentBase.print` 前被触发，将消息序列化后通过 HTTP POST 推送到 Studio；失败时最多重试 3 次。
- `_equip_as_studio_hooks(studio_url)`：在调用 `agentscope.init(studio_url=...)` 时执行，给 `AgentBase` 注册 `pre_print` Hook，使用 `functools.partial` 注入 `studio_url`、`run_id`。

### 4. 关键设计模式
- **Hook 链**：利用 `AgentBase.register_class_hook` 将函数插入 Hook 链，保持执行顺序。
- **依赖注入**：通过 `partial` 将运行时参数（Studio URL、run_id）注入 Hook，而不改变函数签名。
- **有限重试**：网络请求失败时最多重试 3 次，避免无限循环。

### 5. 其他组件的交互
- **Agent/AgentBase**：当 Studio 集成启用后，每次 `print` 输出都会触发 Hook，将消息推送至前端。
- **Studio 服务**：接收 `pushMessage` 请求（`runId`、`replyId`、消息内容等），用于实时 UI 展示。
- **责任边界**：Hook 仅负责转发消息；认证、网络配置、错误处理策略由调用方决定（例如捕获异常并决定是否忽略）。

## 二、文件/类/函数/成员变量映射到 src 路径
- `src/agentscope/hooks/_studio_hooks.py`
  - `as_studio_forward_message_pre_print_hook`：核心转发逻辑。
- `src/agentscope/hooks/__init__.py`
  - `_equip_as_studio_hooks(studio_url: str)`：注册 Hook；导出 `as_studio_forward_message_pre_print_hook`。

## 三、关键数据结构与对外接口（含类型/返回约束）
- Hook 签名遵循 Agent 约定：`hook(self, kwargs: dict, **extra)`；`kwargs["msg"]` 为当前输出消息。
- `_equip_as_studio_hooks(studio_url: str) -> None`：内部调用 `AgentBase.register_class_hook`，不返回值；重复调用会覆盖同名 Hook。

## 四、与其他模块交互（调用链与责任边界）
- `agentscope.init(studio_url=...)` → `_equip_as_studio_hooks` 注册 Hook → Agent `print` 时触发 Hook → Studio 接收消息。
- 如需扩展其他 Hook（例如日志、监控），可参考该模块实现并使用 `register_class_hook`/`register_instance_hook`。

## 五、变更流程（与 AGENTS.md 对齐）
- **文档先行**：新增 Hook 或调整现有 Hook 行为时，更新本 SOP，并在 PR 描述中说明触发时机与副作用。
- **todo 规划**：在 `todo.md` 中列出验证项，例如：
  1. 注册 Hook 并触发一次 `print`，确认 Studio 接收到消息。
  2. 模拟网络失败，验证重试策略和异常路径。
  3. 若新增 Hook，确认与现有 Hook 顺序无冲突。
- **验收要求**：相关单测（`tests/hook_test.py`）应覆盖 Hook 注册与执行；运行 `pytest`、`ruff check src`。
- **回滚预案**：若 Hook 行为调整影响现有集成，需要提供关闭/回滚方案，例如允许禁用 Studio Hook。
- **知识同步**：更新 Studio 文档、示例，说明如何启用/禁用 Hook 及调试方法。
