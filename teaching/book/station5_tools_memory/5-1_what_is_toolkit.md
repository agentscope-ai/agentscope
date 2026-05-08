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
