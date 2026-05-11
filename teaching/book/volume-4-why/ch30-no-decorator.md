# 第 30 章 为什么不用装饰器注册工具

> 本章讨论：显式 `register_tool_function()` vs 装饰器 `@tool`。

---

## 30.1 两种方案

```python
# 方案 A：装饰器（LangChain 风格）
@tool
def get_weather(city: str) -> str:
    """获取天气"""
    ...

# 方案 B：显式注册（AgentScope 风格）
def get_weather(city: str) -> str:
    """获取天气"""
    ...

toolkit.register_tool_function(get_weather)
```

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 30.2 AgentScope 的选择：显式注册

### 为什么？

1. **工具与框架解耦**：`get_weather` 是一个普通函数，不依赖任何框架。可以在非 Agent 场景中复用
2. **注册时机灵活**：可以在运行时决定注册哪些工具（条件注册、动态注册）
3. **一个函数多个 Toolkit**：同一个函数可以注册到不同的 Toolkit 实例，配置不同的参数

### 装饰器的坏处

```python
@tool  # 这一行让函数依赖了框架
def get_weather(city: str) -> str:
    ...
```

一旦加了 `@tool`，这个函数就绑定了框架。在没有框架的环境里（比如普通脚本），要么导入框架，要么去掉装饰器。

### 显式注册的代价

多写一行代码。但换来的是解耦和灵活性。

---

## 30.3 检查点

你现在已经理解了：

- **显式注册 > 装饰器**：工具函数不依赖框架
- **好处**：解耦、灵活注册、可复用
- **代价**：多一行代码

---

## 下一章预告
