# 第 32 章 编译期 Hook vs 运行时 Hook

> 本章讨论：为什么用元类实现 Hook（编译期注入）而不是装饰器链（运行时注入）。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 32.1 两种 Hook 方案

### 方案 A：元类（编译期）

```python
class _AgentMeta(type):
    def __new__(mcs, name, bases, attrs):
        attrs["reply"] = _wrap_with_hooks(attrs["reply"])
        return super().__new__(mcs, name, bases, attrs)
```

Hook 在类被创建时就注入了。

### 方案 B：装饰器链（运行时）

```python
class AgentBase:
    @with_hooks
    async def reply(self, msg):
        ...
```

或者更灵活的运行时注册：

```python
agent.add_hook("pre_reply", my_hook)
```

---

## 32.2 AgentScope 的选择：元类

### 为什么？

1. **保证性**：每个 Agent 都有 Hook，不会因为忘记加装饰器而遗漏
2. **一致性**：所有 Agent 的 Hook 行为统一
3. **子类无需关心**：创建新 Agent 时不需要知道 Hook 的存在

### 代价

- **隐式行为**：看 Agent 代码看不到 Hook 的存在，调试时不直观
- **元类复杂性**：元类是 Python 中较难理解的概念
- **与多重继承的交互**：需要防重入守卫

### 运行时 Hook 的好处

- **显式**：在哪里加了 Hook 一目了然
- **灵活**：可以在任何时候添加/移除 Hook
- **简单**：不需要理解元类

### 为什么没选运行时

AgentScope 的 Hook 是核心功能（日志、追踪、Studio 集成都依赖它），不能遗漏。元类提供了"编译期保证"——只要继承了 `AgentBase`，Hook 就一定存在。

---

## 32.3 检查点

你现在已经理解了：

- **元类 Hook**：编译期注入，保证所有 Agent 都有 Hook
- **装饰器 Hook**：运行时注入，显式但可能遗漏
- **权衡**：保证性 vs 显式性

---

## 下一章预告
