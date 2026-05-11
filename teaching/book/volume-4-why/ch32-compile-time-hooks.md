# 第 32 章：编译期 Hook vs 运行时 Hook

> **难度**：进阶
>
> AgentScope 用元类在**类定义时**注入 Hook 包装。为什么不在每次调用时动态包装？这个选择有什么后果？

## 决策回顾

在第 15 章我们看了 `_AgentMeta` 的实现：

```python
# _agent_meta.py:159
class _AgentMeta(type):
    def __new__(mcs, name, bases, attrs):
        for func_name in ["reply", "print", "observe"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)
```

`reply`、`print`、`observe` 方法在类被 Python 解释器加载时就被包装了。这是**编译期**注入（严格说是类定义时，不是传统意义的编译期）。

---

## 被否方案：运行时动态包装

**方案**：在 `__call__` 中动态检查和执行 Hook：

```python
class AgentBase:
    async def __call__(self, msg=None, **kwargs):
        # 运行时检查 Hook
        for hook in self._pre_reply_hooks.values():
            msg = await hook(self, msg)

        result = await self.reply(msg, **kwargs)

        for hook in self._post_reply_hooks.values():
            result = await hook(self, msg, result)

        return result
```

Django 的 middleware 就是这样——在请求处理函数中显式调用中间件链。

---

## 对比

| 维度 | 编译期（元类） | 运行时（显式调用） |
|------|---------------|-------------------|
| **注入时机** | 类定义时 | 每次调用时 |
| **覆盖范围** | 所有子类自动获得 | 需要每个方法手动添加 |
| **遗漏风险** | 无（元类自动包装） | 有（可能忘记加 Hook 代码） |
| **透明度** | 低（看不到包装代码） | 高（Hook 调用显式可见） |
| **调试难度** | 较高（需要理解元类） | 较低（代码就在那里） |
| **性能** | 包装只做一次 | 每次调用都检查（可忽略） |

---

## 为什么选择编译期

### 理由一：覆盖保证

`reply`、`observe`、`print` 三个方法**必须**被 Hook 包装。如果是运行时方案，每个方法的实现者都需要记住加 Hook 代码。用元类后，无论谁写子类，这三个方法都会被自动包装。

### 理由二：继承链安全

继承链中多个类可能都定义了 `reply`。防重入保护（`hook_guard_attr`）确保 Hook 只执行一次。这是编译期包装才能优雅解决的问题——运行时方案需要在每个 `reply` 实现中加防重入检查。

### 理由三：不侵入业务逻辑

`reply` 方法的实现者不需要知道 Hook 的存在：

```python
class ReActAgent(ReActAgentBase):
    async def reply(self, msg=None):
        # 纯业务逻辑，不需要调用 super().reply()
        # 也不需要手动触发 Hook
        ...
```

---

## 后果分析

### 好处

1. **覆盖保证**：所有 Agent 子类自动获得 Hook 能力
2. **不侵入**：业务代码不需要关心 Hook
3. **统一执行链**：pre-hook → 业务 → post-hook 的顺序有保证

### 麻烦

1. **调试困难**：调用栈中看到的是 `async_wrapper` 而不是 `reply`
2. **元类恐惧**：很多 Python 开发者不熟悉元类，看到 `metaclass=_AgentMeta` 会困惑
3. **多重继承问题**：Python 的元类冲突规则可能导致意外的类创建行为
4. **IDE 支持差**：跳转到 `reply` 定义时，看到的是原始方法，不是包装后的版本

---

## 横向对比

| 框架 | Hook/拦截方式 | 时机 |
|------|-------------|------|
| **AgentScope** | 元类自动包装 | 编译期 |
| **LangChain** | 回调函数 `callbacks` | 运行时传参 |
| **Django** | middleware 显式调用 | 运行时 |
| **FastAPI** | 依赖注入 + 装饰器 | 混合 |

LangChain 用运行时回调——更灵活但更容易遗漏。AgentScope 用编译期注入——更安全但更不透明。

AgentScope 官方文档的 Building Blocks > Hooking Functions 页面展示了 Hook 的注册方式（`register_instance_hook` / `register_class_hook`）和支持的 Hook 类型表格（reply/observe/print 的 pre/post hooks），以及 ReAct Agent 额外支持的 reasoning/acting hooks。

Python 的元类机制是编译期注入的基础。Python 数据模型文档对元类的说明是：当 Python 解释器执行 `class MyClass(Base, metaclass=Meta):` 时，`Meta.__new__()` 会在类对象创建时被调用，可以在此时检查和修改类的属性字典（`attrs`）。这意味着元类可以在类定义时就自动包装某些方法——开发者甚至不需要知道这个机制的存在。

---

## 你的判断

1. 如果 LangChain 的运行时回调方案"更容易遗漏"，它为什么还是主流选择？
2. 元类方案的一个实际风险：如果有人直接调用 `agent._reply_original()` 绕过 Hook，框架能防止吗？

---

## 下一章预告

Hook 的注入时机是一个设计选择。接下来我们看另一个选择——ContentBlock 为什么用 `TypedDict`（Union 类型）而不是 OOP 类继承？
