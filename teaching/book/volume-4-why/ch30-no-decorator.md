# 第 30 章：为什么不用装饰器注册工具

> **难度**：入门
>
> LangChain 用 `@tool` 装饰器注册工具函数。AgentScope 用 `toolkit.register_tool_function(func)`。显式注册有什么好处？

## 决策回顾

AgentScope 的工具注册（`_toolkit.py:274`）：

```python
toolkit = Toolkit()

# 方式一：装饰器注册（其实是语法糖）
@toolkit.register_tool_function
def get_weather(city: str) -> ToolResponse:
    ...

# 方式二：方法调用注册
toolkit.register_tool_function(get_weather, tool_name="weather")
```

装饰器方式看起来和 LangChain 的 `@tool` 很像，但本质不同——AgentScope 的装饰器是实例方法，绑定到特定的 `Toolkit` 实例。

---

## 被否方案：全局装饰器

**方案**：用全局装饰器注册，像 LangChain：

```python
@tool(name="get_weather", description="获取天气")
def get_weather(city: str) -> str:
    ...
```

**问题**：

1. **全局状态**：工具注册到全局注册表，多个 Agent 无法使用不同的工具集
2. **测试困难**：全局状态在测试之间泄漏，需要手动清理
3. **运行时灵活性差**：不能在运行时决定注册哪些工具

**场景示例**：

```python
# 全局装饰器的问题
@tool
def admin_delete_database():  # 管理员专用工具
    ...

# Agent A（普通用户）不应该有这个工具
agent_a = Agent(tools=?)  # 怎么排除？

# Agent B（管理员）才有
agent_b = Agent(tools=?)  # 怎么只包含？
```

---

## AgentScope 的选择：实例级注册

每个 `Toolkit` 实例维护自己的工具字典：

```python
# _toolkit.py:170-173
class Toolkit(StateModule):
    def __init__(self):
        self.tools: dict[str, RegisteredToolFunction] = {}
        self._middlewares: list = []
```

这意味着：

1. **不同 Agent 可以有不同的工具集**：

```python
admin_toolkit = Toolkit()
admin_toolkit.register_tool_function(delete_database)

user_toolkit = Toolkit()
user_toolkit.register_tool_function(query_database)

admin_agent = ReActAgent(..., toolkit=admin_toolkit)
user_agent = ReActAgent(..., toolkit=user_toolkit)
```

2. **运行时动态注册**：

```python
toolkit = Toolkit()
if user_has_access("weather"):
    toolkit.register_tool_function(get_weather)
if user_has_access("database"):
    toolkit.register_tool_function(query_db)
```

3. **测试隔离**：

```python
def test_tool():
    toolkit = Toolkit()  # 每个测试创建新实例
    toolkit.register_tool_function(my_tool)
    # 测试结束后 toolkit 被销毁，无全局污染
```

---

## 后果分析

### 好处

1. **无全局状态**：每个 Toolkit 实例独立
2. **运行时灵活性**：可以动态决定注册哪些工具
3. **多 Agent 场景**：不同 Agent 绑定不同工具集
4. **测试友好**：无需清理全局注册表

### 麻烦

1. **多写一行代码**：需要先创建 `Toolkit` 实例
2. **装饰器不能独立使用**：必须用 `@toolkit.register_tool_function`，不能脱离 toolkit

---

## 横向对比

| 框架 | 注册方式 | 作用域 |
|------|---------|--------|
| **AgentScope** | `toolkit.register_tool_function()` | 实例级 |
| **LangChain** | `@tool` 装饰器 | 全局 |
| **CrewAI** | 装饰器 + 类方法 | 类级 |
| **AutoGen** | 函数列表传参 | 实例级 |

AgentScope 和 AutoGen 都选择了实例级注册——这是多 Agent 场景的刚需。

> **官方文档对照**：本文对应 [Building Blocks > Tool Capabilities](https://docs.agentscope.io/building-blocks/tool-capabilities)。官方文档展示了 `register_tool_function` 的使用方法，本章分析了为什么不使用全局装饰器的设计理由。
>
> **推荐阅读**：[AgentScope 1.0 论文](https://arxiv.org/pdf/2508.16279) 第 2.1 节讨论了工具系统的设计目标。

---

## 你的判断

1. 全局装饰器的简洁性是否值得牺牲灵活性？
2. 如果同时支持两种方式（全局 + 实例级），会不会增加认知负担？

---

## 下一章预告

注册方式决定了"工具怎么来"。但工具相关的代码都塞在一个文件里——`_toolkit.py` 有 1500+ 行。这是上帝类还是合理的设计？下一章我们看模块拆分的权衡。
