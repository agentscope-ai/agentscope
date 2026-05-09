# 5-1 Toolkit是什么

> **目标**：理解Toolkit如何管理和注册Agent可用的工具

---

## 🎯 这一章的目标

学完之后，你能：
- 创建自定义Tool
- 使用Toolkit注册和管理工具
- 理解Tool和Agent的关系

---

## 🚀 先跑起来

```python showLineNumbers
from agentscope.tool import Toolkit, ToolResponse
import ast

# 创建自定义工具函数（无需装饰器）
def safe_calculate(expression: str) -> ToolResponse:
    """计算数学表达式（安全版本，仅支持基本运算）

    Args:
        expression: 数学表达式，如 "2+3*4"

    Returns:
        ToolResponse: 包含计算结果的响应
    """
    # 使用 ast.literal_eval 避免安全风险
    result = str(ast.literal_eval(expression))
    return ToolResponse(result=result)

# 使用Toolkit注册工具
toolkit = Toolkit()
toolkit.register_tool_function(safe_calculate, group_name="basic")

# 链式注册多个工具
toolkit = Toolkit()
toolkit.register_tool_function(search_weather, group_name="weather") \
       .register_tool_function(send_email, group_name="email")

# 传给Agent
agent = ReActAgent(
    name="Assistant",
    model=model,
    toolkit=toolkit  # 注意是 toolkit= 不是 tools=
)
```

---

## 🔍 Toolkit的结构

```
┌─────────────────────────────────────────────────────────────┐
│                      Toolkit                                 │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Tool: search_weather                              │   │
│  │ - name: "search_weather"                        │   │
│  │ - description: "查询天气"                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Tool: calculate                                   │   │
│  │ - name: "calculate"                            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔬 关键代码段解析

### 代码段1：为什么工具函数返回 ToolResponse 而不是普通值？

```python showLineNumbers
# 这是第23-34行
def safe_calculate(expression: str) -> ToolResponse:
    """计算数学表达式（安全版本）"""
    result = str(ast.literal_eval(expression))
    return ToolResponse(result=result)  # 为什么不是直接返回result？
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么返回`ToolResponse`而不是`str`？ | AgentScope统一接口，便于解析 |
| `ToolResponse`里面有什么？ | `result`（执行结果）、`error`（错误信息）等 |
| 如果计算出错怎么办？ | `ToolResponse(error="除数不能为零")` |

**💡 设计思想**：`ToolResponse`是Agent和工具之间的**契约**。无论什么工具，返回格式都一样，Agent就能统一处理。

---

### 代码段2：为什么需要 group_name？

```python showLineNumbers
# 这是第37-43行
toolkit = Toolkit()
toolkit.register_tool_function(safe_calculate, group_name="basic")
toolkit.register_tool_function(search_weather, group_name="weather")

# 或者链式注册
toolkit = Toolkit()
toolkit.register_tool_function(search_weather, group_name="weather") \
       .register_tool_function(send_email, group_name="email")
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| `group_name`是什么？ | 工具的分组名称 |
| 为什么要分组？ | 可以按组名决定暴露哪些工具给Agent |
| 不写会怎样？ | 默认组名为"default" |

**💡 设计思想**：分组让工具管理更灵活。可以按场景暴露不同工具，比如开发时暴露所有工具，生产时只暴露安全工具。

---

### 代码段3：为什么Agent接收的是toolkit而不是tools列表？

```python showLineNumbers
# 这是第46-50行
agent = ReActAgent(
    name="Assistant",
    model=model,
    toolkit=toolkit  # 注意是 toolkit= 不是 tools=
)
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么用`toolkit=`而不是`tools=`？ | AgentScope的API设计就是这样 |
| toolkit里可以有什么？ | 工具函数、工具组、工具配置等 |
| 一个Agent可以有多个Toolkit吗？ | 可以，通过组合模式 |

**💡 设计思想**：`Toolkit`是一个**容器**，而不仅仅是列表。容器可以动态添加/删除工具、按组管理工具、配置工具行为。

---

## 💡 Java开发者注意

Toolkit类似Java的**工具类集合**：

| Python | Java | 说明 |
|--------|------|------|
| `register_tool_function()` | 注册方法 | 将函数注册为工具 |
| `Toolkit` | Utils类 | 工具集合 |
| `ToolResponse` | 返回对象 | 工具执行结果 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Tool的description有什么用？**
   - 帮助模型理解什么时候该调用
   - 理解需要传什么参数

2. **Toolkit和Tool是什么关系？**
   - Toolkit是Tool的容器
   - 一个Toolkit可以装多个Tool

</details>

---

★ **Insight** ─────────────────────────────────────
- **Toolkit = 工具箱**，管理Agent可用的所有工具
- **register_tool_function() = 注册**，将函数注册为可调用的工具
- **ToolResponse = 响应对象**，包含工具执行结果
- **group_name = 分组**，控制工具在何时暴露给Agent
─────────────────────────────────────────────────
