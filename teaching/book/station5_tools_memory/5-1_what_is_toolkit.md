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
from agentscope.tool import Tool, Toolkit

# 创建自定义Tool
@Tool
def calculate(expression: str) -> str:
    """计算数学表达式
    
    Args:
        expression: 数学表达式，如 "2+3*4"
    
    Returns:
        计算结果
    """
    return str(eval(expression))

# 使用Toolkit注册工具
toolkit = Toolkit([calculate])

# 或者链式添加
toolkit = Toolkit().add(search_weather).add(send_email)

# 传给Agent
agent = ReActAgent(
    name="Assistant",
    model=model,
    tools=toolkit  # 绑定工具
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
| `@Tool` 装饰器 | 注解 | 标记为工具 |
| `Toolkit` | Utils类 | 工具集合 |
| `func` | static方法 | 实际执行 |

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
- **@Tool = 标记**，把函数标记为可调用的工具
- **description = 使用说明**，帮助模型决定何时调用
─────────────────────────────────────────────────
