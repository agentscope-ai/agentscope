# 第 34 章 ContextVar 的秘密

> 本章讨论：为什么 `_ConfigCls` 用 `ContextVar` 而不是普通全局变量。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 34.1 问题

`_config` 是全局配置对象。如果用普通全局变量：

```python
_config = {"project": "demo", "run_id": "123"}
```

在多线程环境下：

```python
# 线程 1
_config["project"] = "project_a"

# 线程 2（同时）
_config["project"] = "project_b"

# 线程 1 读取
print(_config["project"])  # "project_b" —— 被线程 2 覆盖了！
```

---

## 34.2 ContextVar 的解法

```python
from contextvars import ContextVar

_project = ContextVar("project", default="demo")

# 线程 1
_project.set("project_a")

# 线程 2
_project.set("project_b")

# 线程 1
print(_project.get())  # "project_a" —— 不受线程 2 影响
```

`ContextVar` 为每个线程/协程维护独立的值。设置和读取都在各自的上下文中，互不干扰。

---

## 34.3 在 AgentScope 中的应用

`_ConfigCls` 的每个属性都用 `ContextVar` 存储：

```python
class _ConfigCls:
    @property
    def project(self) -> str:
        return self._project.get()

    @project.setter
    def project(self, value: str) -> None:
        self._project.set(value)
```

这意味着：**同一个 `_config` 对象，在不同线程/协程中可以有不同的配置值**。

这在以下场景有用：
- Web 服务同时处理多个 Agent 请求
- 异步框架中多个 Agent 并行运行
- 测试中并行运行测试用例

---

## 34.4 检查点

你现在已经理解了：

- **ContextVar**：线程/协程安全的上下文变量
- **为什么不用全局变量**：多线程竞争问题
- **在 _ConfigCls 中的应用**：每个上下文有独立的配置值

---

## 下一章预告
