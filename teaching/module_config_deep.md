# Config 配置系统源码深度剖析

## 目录

1. 模块概述
2. 目录结构
3. 核心功能源码解读
4. 设计模式总结
5. 代码示例
6. 练习题

---

## 1. 模块概述

AgentScope 的配置系统采用 **ContextVar** 实现线程安全、异步安全的运行时配置管理。与传统的全局变量不同，ContextVar 允许每个异步任务拥有独立的配置副本，非常适合 Python 的异步编程场景。

**核心职责：**
- 管理运行时配置（run_id、project、name、created_at、trace_enabled）
- 提供线程/异步安全的配置访问
- 支持配置初始化与动态更新

**源码位置：**
- 主配置类：`/Users/nadav/IdeaProjects/agentscope/src/agentscope/_run_config.py`
- 初始化逻辑：`/Users/nadav/IdeaProjects/agentscope/src/agentscope/__init__.py` 中的 `init()` 函数

---

## 2. 目录结构

```
agentscope/
├── _run_config.py          # 配置类核心实现
├── _logging.py             # 日志系统（相关模块）
├── __init__.py             # init() 初始化函数
└── _utils/
    └── _common.py          # 通用工具函数
```

---

## 3. 核心功能源码解读

### 3.1 _ConfigCls 配置类

```python
# _run_config.py
from contextvars import ContextVar

class _ConfigCls:
    """The run instance configuration in agentscope."""

    def __init__(
        self,
        run_id: ContextVar[str],
        project: ContextVar[str],
        name: ContextVar[str],
        created_at: ContextVar[str],
        trace_enabled: ContextVar[bool],
    ) -> None:
        # Copy the default context variables
        self._run_id = run_id
        self._created_at = created_at
        self._project = project
        self._name = name
        self._trace_enabled = trace_enabled
```

**设计亮点：**

- **ContextVar 而非普通变量**：每个协程/线程获取的是独立的副本，避免相互干扰
- **私有属性 + Property 访问**：封装内部实现，对外提供清晰的 get/set 接口

### 3.2 Property 访问器模式

```python
@property
def run_id(self) -> str:
    """Get the run ID."""
    return self._run_id.get()

@run_id.setter
def run_id(self, value: str) -> None:
    """Set the run ID."""
    self._run_id.set(value)
```

每个配置项（run_id、project、name、created_at、trace_enabled）都遵循相同的模式：

1. **getter**：通过 `ContextVar.get()` 获取当前上下文中的值
2. **setter**：通过 `ContextVar.set()` 设置当前上下文中的值

### 3.3 全局配置实例初始化

```python
# __init__.py
from contextvars import ContextVar
from datetime import datetime
import shortuuid

_config = _ConfigCls(
    run_id=ContextVar("run_id", default=shortuuid.uuid()),
    project=ContextVar(
        "project",
        default="UnnamedProject_At" + datetime.now().strftime("%Y%m%d"),
    ),
    name=ContextVar(
        "name",
        default=datetime.now().strftime("%H%M%S_") + _generate_random_suffix(4),
    ),
    created_at=ContextVar(
        "created_at",
        default=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    ),
    trace_enabled=ContextVar("trace_enabled", default=False),
)
```

**默认值生成策略：**
- `run_id`：使用 shortuuid 生成短唯一ID
- `project`：格式 `UnnamedProject_At{日期}`
- `name`：格式 `{时分秒}_{随机后缀}`
- `created_at`：精确到毫秒的时间戳

### 3.4 init() 初始化函数

```python
def init(
    project: str | None = None,
    name: str | None = None,
    run_id: str | None = None,
    logging_path: str | None = None,
    logging_level: str = "INFO",
    studio_url: str | None = None,
    tracing_url: str | None = None,
) -> None:
    if project:
        _config.project = project

    if name:
        _config.name = name

    if run_id:
        _config.run_id = run_id

    setup_logger(logging_level, logging_path)

    # Studio 集成...
    if studio_url:
        # 注册运行实例到 AgentScope Studio
        data = {
            "id": _config.run_id,
            "project": _config.project,
            "name": _config.name,
            "timestamp": _config.created_at,
            "pid": os.getpid(),
            "status": "running",
        }
        response = requests.post(url=f"{studio_url}/trpc/registerRun", json=data)
        # ...
```

---

## 4. 设计模式总结

### 4.1 单例模式

`_config` 是全局单一实例，所有模块通过导入共享同一配置对象：

```python
# 某模块中使用
from agentscope import _config
print(_config.run_id)
```

### 4.2 ContextVar 上下文隔离模式

```
┌─────────────────────────────────────────────────────────┐
│                    Main Context                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Async Task 1                          │    │
│  │           run_id = "task-1-abc"                │    │
│  └─────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Async Task 2                          │    │
│  │           run_id = "task-2-xyz"                │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

每个异步任务可以独立修改 `run_id`，互不影响。

### 4.3 建造者模式（init 函数）

`init()` 函数接受可选参数，只更新提供的配置项，未提供的保持默认值。

### 4.4 门面模式

配置系统对内管理复杂的 ContextVar，对外提供简洁的 property 接口。

---

## 5. 代码示例

### 5.1 基础使用

```python
import agentscope

# 初始化配置
agentscope.init(
    project="my_agent_project",
    name="experiment_001",
    logging_level="DEBUG"
)

# 访问配置
from agentscope import _config
print(f"Run ID: {_config.run_id}")
print(f"Project: {_config.project}")
print(f"Created at: {_config.created_at}")
```

### 5.2 异步环境中的配置隔离

```python
import asyncio
import agentscope

agentscope.init(project="async_test")

async def task_a():
    agentscope._config.run_id = "task-a"
    await asyncio.sleep(0.1)
    print(f"Task A run_id: {agentscope._config.run_id}")

async def task_b():
    agentscope._config.run_id = "task-b"
    await asyncio.sleep(0.1)
    print(f"Task B run_id: {agentscope._config.run_id}")

async def main():
    await asyncio.gather(task_a(), task_b())

asyncio.run(main())
# 输出:
# Task A run_id: task-a
# Task B run_id: task-b
```

### 5.3 动态更新配置

```python
import agentscope

# 初始配置
agentscope.init(project="original_project", name="run_1")

# 运行过程中动态更新
agentscope._config.project = "updated_project"
agentscope._config.name = "run_2"

print(f"Project: {agentscope._config.project}")  # updated_project
print(f"Name: {agentscope._config.name}")       # run_2
```

---

## 6. 练习题

### 练习 1：理解 ContextVar 隔离

**问题**：在以下代码中，`task_1` 和 `task_2` 打印的 `run_id` 分别是什么？为什么？

```python
import asyncio
import agentscope

agentscope.init()

async def task_1():
    agentscope._config.run_id = "modified-by-task-1"
    await asyncio.sleep(0)
    return agentscope._config.run_id

async def task_2():
    await asyncio.sleep(0)
    return agentscope._config.run_id

print(asyncio.run(task_1()))
print(asyncio.run(task_2()))
```

**答案**：
- `task_1` 返回 `"modified-by-task-1"`
- `task_2` 返回原始默认值

**原因解析**：
- 每次调用 `asyncio.run()` 会创建一个全新的上下文（Context）
- ContextVar 的隔离是在上下文级别，而非任务级别
- `task_1` 修改的是它所在上下文的变量
- 当 `task_2` 在新的 `asyncio.run()` 中运行时，它看到的是新上下文的默认值

**如果想在同一上下文中隔离，应使用 `asyncio.create_task()`**：
```python
async def main():
    # 同一上下文，但不同任务
    t1 = asyncio.create_task(task_1())
    t2 = asyncio.create_task(task_2())
    # 注意：这种情况下 task_2 仍会看到 "modified-by-task-1"
    # 因为 ContextVar 在同一上下文中共享
```

### 练习 2：实现配置验证器

**任务**：扩展 `_ConfigCls`，添加一个 `project` 的验证器，拒绝包含空格的项目名。

```python
@project.setter
def project(self, value: str) -> None:
    if " " in value:
        raise ValueError("Project name cannot contain spaces")
    self._project.set(value)
```

### 练习 3：配置持久化

**任务**：编写一个函数，将当前配置序列化为 JSON 格式，便于日志记录和调试。

```python
def serialize_config(config: _ConfigCls) -> dict:
    return {
        "run_id": config.run_id,
        "project": config.project,
        "name": config.name,
        "created_at": config.created_at,
        "trace_enabled": config.trace_enabled,
    }
```

---

## 小结

| 特性 | 实现方式 |
|------|----------|
| 线程安全 | ContextVar |
| 异步隔离 | ContextVar（每个协程独立） |
| 配置项访问 | Property 装饰器 |
| 全局单例 | 模块级 `_config` 变量 |
| 默认值 | ContextVar default 参数 |

配置系统虽然代码量不大，但是 AgentScope 架构中不可或缺的基础设施，为整个框架提供了统一的运行时上下文管理能力。
