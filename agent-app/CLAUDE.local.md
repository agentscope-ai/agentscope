# 开发模式

如下是后端的开发约定：

## Python 通用工具链

- 使用 uv (https://github.com/astral-sh/uv) 相关工具 进行 环境依赖管理，而不是pip;
- 使用 `uv init --python-3.13` 进行后端项目初始化；
- 添加依赖，使用 `uv add xxx` 或者 编辑 pyproject.toml 后，使用 `uv sync ` 同步依赖

## Python 相关linter 更新
- 使用 Pydantic V2 语法
- 使用 basedpyright 检测 deprecated 的 语法并修复，如：
    - datetime.utcnow -> datetime.now(timezone.utc)

## 领域开发规约模板（适用于全域，按域复制使用）

用途：本模板定义任何业务域的通用分层与目录约定。每个域可复制本模板，并在域内微调。

### 适用对象
- 任意业务域：`src/<domain>/`
- 目标：提升内聚、统一依赖方向、提升可演化性与可测试性。

## 目录结构（按域）

```
src/<domain>/
  repository/        # 仓储（DAO）：仅与基础设施交互
    __init__.py
    <domain>_repository.py
  service/           # 服务层：编排仓储与横切能力
    __init__.py
    <domain>_service.py
  __init__.py        # 统一对外导出
```

基础设施与通用层：
```
src/storage/         # 基础设施（数据库、缓存等）
src/util/            # 通用工具：与业务无关
```

## 依赖方向

- `<domain>.service → <domain>.repository → storage/util`
- 上层（router/agent/tasks）优先依赖 `<domain>.service`，禁止跨域绕过服务直连仓储。
- 禁止反向依赖（storage/util 不得依赖任何域）。

## 导出与导入

- `__init__.py` 默认留空，导入时默认选择项目路径，即 `src/...` 和 `tests/...` 来导入。

## 命名与接口

- 仓储：`<Domain>Repository`，职责为数据访问；不承载业务编排。
- 服务：`<Domain>Service`，职责为应用编排；可挂接缓存、指标、权限、重试等。
- 接口风格：
  - 异步优先（`async def`），显式类型注解。
  - 服务方法聚焦业务语义；仓储方法聚焦数据语义。
  - 数据结构优先使用 `@dataclass`（或 Pydantic 模型）而非裸字典，
    以获得更好的类型可读性、可维护性与演进性；仅在跨边界序列化/协议层再转换为 `dict`。

### 数据契约（禁止隐式字典）

- 所有跨模块/跨层的数据契约必须在对应域的 `src/<domain>/type.py` 中以 `@dataclass` 或 Pydantic 明确定义，严禁使用“隐式约定的裸字典”。
- 服务/仓储/路由之间传递的数据，统一使用已定义的类型；仅在协议边界（HTTP 入参/出参、持久化 JSON 字段）进行 `to_dict()/from_dict()` 的转换。
- 禁止将 `Dict[str, Any]` 作为长期稳定契约在模块间传递；如确需临时扩展字段，请在类型中新增可选字段（向后兼容），并在域内一次性升级相关调用方。
- 如果存在公用性很强的类型，则放在 `src/types/<entity_name>` 下。
- 依赖约束：`src/types` 不得依赖任何业务域；各业务域可依赖 `src/types`。跨域共享类型必须经由 `src/types` 暴露。
- 归类原则：仅放“跨域复用强”的纯数据结构（dataclass/Enum/TypedDict）。与具体业务语义强绑定的类型仍放各自域的 `type.py`。
- 命名规范：
  - 文件名：`snake_case`，示例 `session_task.py`
  - 类型名：`PascalCase`，示例 `SessionTaskState`
  - 统一在 `src/types/__init__.py` 做最小必要导出，避免一次性通配导出
- 序列化边界：
  - 内部统一 dataclass；HTTP/API 边界使用 Pydantic；数据库 JSONB 使用 dataclass 的 `to_dict()` 映射
  - 字段命名与 JSON key 保持一致；禁止在持久化层做“隐式转换”
- dataclass 边界方法：
  - 强制要求对跨边界的数据类型提供 `to_dict()/from_dict()`，并采用“字段白名单”策略，避免隐式透传
  - `from_dict()` 需处理缺省/错误容错并返回最小安全视图（必要时提供默认值）
- 变更与版本化：
  - 可向后兼容的演进优先：新增可选字段；枚举仅新增值
  - 破坏性变更需版本化（如 `StatusV2`）并在变更日志注明迁移指引
- 测试要求：
  - 为公共类型增加“字典往返”测试（dataclass ↔ dict ↔ dataclass）
  - 关键公共类型建议加 JSON Schema 快照或契约测试，防止非预期破坏
- 目录示例：
  - `src/types/session_task.py` 定义 `SessionTaskStatus/SessionTaskState/InMemoryTaskStatus`
  - `src/types/__init__.py` 仅导出稳定 API：`from .session_task import SessionTaskState, SessionTaskStatus`
  - 提供一个通用模板：`src/types/contract_template.py`（包含 Enum + dataclass + to_dict/from_dict 示例），团队在新增公共类型时可参考
- 演进策略：
  - 优先“新增可选字段”实现向后兼容；
  - 枚举类型只能新增枚举值，删除/改名需版本化（如 `StatusV2` 或语义化版本）；
  - 破坏性变更需在发布说明中标注并提供迁移路径。
- 示例（上下文域）：

```python
# src/context/type.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class SessionTaskStatus(str, Enum):
    RUNNING = "running"
    FINISH = "finish"
    CANCELLED = "cancelled"
    FAILED = "failed"

@dataclass
class SessionTaskState:
    canvas_id: str
    turn_id: str
    thread_id: str
    status: SessionTaskStatus
    instance_id: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
```

- 路由层返回：将 dataclass 转为 `dict` 再作为响应 `data` 字段；仓储层读写 JSONB 等持久化结构时，也统一基于该 dataclass 的字段进行映射。
## 异常与日志

- 仓储：捕获异常、记录日志，返回安全默认值；写操作完成后显式 `commit()`。
- 服务：根据业务场景决定异常上抛或降级；必要时统一重试与熔断。
- 日志分级：`info` 成功、`warning` 预期问题、`error` 异常。

## 迁移与演进

- 旧实现若在 `util` 或其他不当位置，应迁至对应域的 `repository/service`。
- 新增域时，复制本模板结构与约定；在域内文档记录差异化策略。

## 示例（调用方）

```python
from src.<domain>.service import <Domain>Service

svc = <Domain>Service()
result = await svc.some_business_action(params)
```
## 应用生命周期与优雅关停（Lifespan & Graceful Shutdown）

为确保资源一致性与可观测性，采用“分层职责 + 混合模式”的生命周期管理：
这里给出示例（代码库中可能没有完全对应的情况，但是可以参考）

- 核心服务（显式编排，放在 `lifespan`）
  - 由应用直接启动/停止的能力，采用清晰的 start/stop 对称编排：
    - 数据库连接管理：`get_database_manager()` / `close_database_connections()`
    - 调度器：`init_scheduler()`
    - 事件循环监控：`event_loop_monitor.start()` / `event_loop_monitor.stop()`
    - 工具管理器：`ToolManager.get().initialize(...)` / `ToolManager.get().shutdown()`
  - 放在 `src/util/lifespan.py` 内集中调用，便于排查与维护。

- 叶子/惰性资源（自动回收，注册到 `graceful_shutdown`）
  - 由业务深处或按需惰性创建的客户端/资源（如通用 HTTP 客户端、遥测客户端等）不应要求 `lifespan` 了解其存在。
  - 创建者在构造时就近注册关闭回调：
    - `from src.util.graceful_shutdown import get_registry`
    - `get_registry().register(self.close)`（支持异步或同步函数）
  - `lifespan` 在关停阶段统一执行：`await get_registry().run_all()`（逆序执行，先关叶子后关核心）。

- 关停顺序建议（已在 `lifespan.py` 落地）：
  1) `await event_loop_monitor.stop()`
  2) `await get_registry().run_all()`（释放所有注册的叶子资源）
  3) `await ToolManager.get().shutdown()`
  4) `await close_database_connections()`

- 示例：在惰性单例中注册清理
```python
from src.util.graceful_shutdown import get_registry

class MyClient:
    def __init__(self):
        self._pool = create_pool()
        get_registry().register(self.close)

    async def close(self) -> None:
        await self._pool.aclose()
```

- 事件循环（UI/CLI 等非 FastAPI 入口）
  - 可使用 `src/util/event_loop.py`：
    - 启动：`start_event_loop()`
    - 调度协程：`schedule_coro()/run_sync()/run_async()`
    - 停止：`stop_event_loop()`（建议通过 `atexit` 或外层模块统一触发）

注意：不要依赖 `__del__` 做资源回收；解释器退出顺序不可控，可能导致清理未执行或顺序错误。


# 启动方式

根据后端开发的具体情况，使用 uv 运行对应server 入口代码即可；
假设项目根目录下存在 server.py 文件（里面是一个argparser + uvicorn 的server运行代码），那么直接执行 `uv run server.py` 


# 代码规范

每次执行完成后，应进行统一的代码检查与格式化：

- 使用 Ruff 作为 Python 代码的检查与格式化工具。
- 推荐方式：
  - macOS/Linux：`./scripts/lint.sh [targets]`（默认 `src tests`）
  - Windows PowerShell：`pwsh ./scripts/lint.ps1 [targets]`（默认 `src tests`）
  - 若未安装 Ruff：
    - 先执行 `uv sync`
      - 若提示未安装uv，则先提示用户安装uv
    - 再执行脚本
    - 兜底手段：`pip install ruff`
- Makefile 等价目标：`make lint`（执行 `ruff check --fix src tests` 与 `ruff format src tests`）。

说明：`scripts/lint.sh` 与 `scripts/lint.ps1` 会自动将工作目录切换到仓库根目录，支持传入自定义检查目标。

# 测试

## 单测
- 比如在src/.../{$module_name}的模块要写单测，那么在test目录下，和src同子目录构建test_{$module_name}

运行方式为
```
uv run ...
```


# docs
## 变更记录
每次用户提完需求，
- 若有涉及feature变更，则在docs/version对应的目录下，新增 or 更改 对应md文件。文件里要和当前git分支名固定。
- 若有涉及 feature 内的 功能更变，则直接在docs/version对应目录的feature文件里 填写简短的变更记录即可。