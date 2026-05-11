# 第 15 章 元类与 Hook

> 本章你将理解：`_AgentMeta` 如何自动包装方法、Hook 的实现细节、防止重入的机制。

---

## 15.1 回顾：元类初见

第 5 章我们简单介绍了元类——在类创建时自动修改类。现在深入看实现。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 15.2 源码入口

| 文件 | 内容 |
|------|------|
| `src/agentscope/agent/_agent_meta.py` | `_AgentMeta`, `_ReActAgentMeta`, `_wrap_with_hooks` |
| `src/agentscope/agent/_agent_base.py` | Hook 存储（类级别 + 实例级别） |
| `src/agentscope/types/_hook.py` | Hook 类型定义 |

---

## 15.3 逐行阅读

### `_AgentMeta.__new__`：自动包装

```python
class _AgentMeta(type):
    def __new__(mcs, name, bases, attrs):
        for func_name in ["reply", "observe", "print"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)
```

关键：`if func_name in attrs`。这意味着只在**当前类定义了这个方法时**才包装，不会重复包装继承来的方法。

### `_ReActAgentMeta`：扩展包装

```python
class _ReActAgentMeta(_AgentMeta):
    def __new__(mcs, name, bases, attrs):
        for func_name in ["_reasoning", "_acting"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)
```

继承 `_AgentMeta`，额外包装 `_reasoning` 和 `_acting`。调用 `super().__new__()` 确保 `reply`, `observe`, `print` 也被包装。

### `_wrap_with_hooks`：洋葱模型实现

```python
async def async_wrapper(self, *args, **kwargs):
    # 防重入守卫
    if getattr(self, hook_guard_attr, False):
        return await original_func(self, *args, **kwargs)

    # 参数归一化
    normalized_kwargs = _normalize_to_kwargs(original_func, self, *args, **kwargs)

    # pre-hooks
    for pre_hook in pre_hooks:
        result = await _execute_async_or_sync_func(pre_hook, self, deepcopy(normalized_kwargs))
        if result is not None:
            normalized_kwargs = result

    # 原始函数
    setattr(self, hook_guard_attr, True)
    try:
        output = await original_func(self, **normalized_kwargs)
    finally:
        setattr(self, hook_guard_attr, False)

    # post-hooks
    for post_hook in post_hooks:
        result = await _execute_async_or_sync_func(post_hook, self, deepcopy(normalized_kwargs), deepcopy(output))
        if result is not None:
            output = result

    return output
```

### 防重入守卫

```python
if getattr(self, hook_guard_attr, False):
    return await original_func(self, *args, **kwargs)
```

为什么需要这个？考虑这个场景：

```
ReActAgent 继承链：ReActAgent → ReActAgentBase → AgentBase

ReActAgent 定义了 reply()  → 被 _ReActAgentMeta 包装
ReActAgentBase 定义了 reply() 吗？没有（它是通过 _reasoning/_acting 组合的）
AgentBase 的 reply() 是抽象方法，不会被包装
```

但在某些复杂的继承场景下，同一个方法可能被多层包装。守卫确保只有最外层执行 Hook，内层直接调用原函数。

### Hook 存储

```python
# 类级别 Hook（所有实例共享）
_class_pre_reply_hooks: dict[str, Callable] = OrderedDict()
_class_post_reply_hooks: dict[str, Callable] = OrderedDict()

# 实例级别 Hook（单个实例特有）
_instance_pre_reply_hooks: dict[str, Callable] = OrderedDict()
_instance_post_reply_hooks: dict[str, Callable] = OrderedDict()
```

执行顺序：先实例级，后类级。

---

## 15.4 设计一瞥：为什么用元类而不是装饰器？

```python
# 方案 A：装饰器（需要手动加）
class MyAgent(AgentBase):
    @with_hooks
    async def reply(self, msg):
        ...

# 方案 B：元类（自动加）
class MyAgent(AgentBase):  # metaclass=_AgentMeta 自动处理
    async def reply(self, msg):
        ...
```

元类的好处：使用者不需要记得加装饰器，框架自动处理。坏处：隐式行为，调试时不直观。

AgentScope 选择元类是因为 Hook 是核心功能，需要保证每个 Agent 都有，不能因为忘记加装饰器而遗漏。

---

## 15.5 试一试

### 注册自定义 Hook

```python
from agentscope.agent import ReActAgent

async def log_hook(agent_self, kwargs):
    print(f"[HOOK] {agent_self.name} 即将处理")
    return kwargs  # 不修改参数

agent = ReActAgent(name="test", ...)
agent.register_instance_hook("pre_reply", "my_log", log_hook)
```

### 查看已注册的 Hook

```python
agent = ReActAgent(name="test", ...)
print("实例级 pre_reply:", list(agent._instance_pre_reply_hooks.keys()))
print("类级别 pre_reply:", list(agent._class_pre_reply_hooks.keys()))
```

---

## 15.6 检查点

你现在已经理解了：

- **`_AgentMeta`**：自动包装 `reply`, `observe`, `print` 方法
- **`_ReActAgentMeta`**：额外包装 `_reasoning`, `_acting`
- **`_wrap_with_hooks`**：洋葱模型，pre-hook → 原函数 → post-hook
- **防重入守卫**：防止多层继承时 Hook 重复执行
- **Hook 存储**：类级别 + 实例级别，OrderedDict 保证顺序

---

## 下一章预告

Hook 是给方法加横切逻辑。下一章看 Formatter 怎么用策略模式适配不同 API 格式。
