# 第 22 章 造一个新 Tool

> 本章你将：从零创建一个工具函数、注册到 Toolkit、让 Agent 使用它。

---

## 22.1 目标

创建一个"计算器"工具，让 Agent 可以做数学运算。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 22.2 Step 1: 写工具函数

工具函数就是普通的 Python 函数，但需要遵循两个规则：

1. **有类型注解**（参数和返回值）
2. **有文档字符串**（描述和 Args 段）

```python
def calculator(operation: str, a: float, b: float) -> str:
    """执行数学运算

    Args:
        operation (str): 运算类型，支持 add/subtract/multiply/divide
        a (float): 第一个数字
        b (float): 第二个数字

    Returns:
        str: 运算结果的文字描述
    """
    ops = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else "错误：除数不能为零",
    }

    if operation not in ops:
        return f"不支持的运算: {operation}，请使用 add/subtract/multiply/divide"

    result = ops[operation](a, b)
    return f"{a} {operation} {b} = {result}"
```

注意：
- 类型注解让框架知道参数类型（`str` → `"string"`, `float` → `"number"`）
- 文档字符串的第一行是工具描述，`Args` 段是每个参数的描述
- 返回值是字符串（简单直接，避免复杂类型）

---

## 22.3 Step 2: 注册并测试

```python
import agentscope
from agentscope.tool import Toolkit
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg

agentscope.init(project="calculator-demo")

toolkit = Toolkit()
toolkit.register_tool_function(calculator)

# 验证 Schema 生成正确
import json
for schema in toolkit.get_tool_schemas():
    print(json.dumps(schema, ensure_ascii=False, indent=2))

# 创建 Agent 并使用
agent = ReActAgent(
    name="math_assistant",
    sys_prompt="你是一个数学助手，可以帮用户做加减乘除运算。",
    model=OpenAIChatModel(model_name="gpt-4o", stream=True),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)

result = await agent(Msg("user", "123 加 456 等于多少？", "user"))
print(result.content)
```

---

## 22.4 Step 3: 添加错误处理

```python
def calculator(operation: str, a: float, b: float) -> str:
    """执行数学运算"""
    try:
        ops = {"add": lambda x, y: x + y, ...}
        result = ops[operation](a, b)
        return f"{a} {operation} {b} = {result}"
    except ZeroDivisionError:
        return "错误：除数不能为零"
    except KeyError:
        return f"不支持的运算: {operation}"
    except Exception as e:
        return f"计算错误: {e}"
```

工具函数应该**永远不抛出异常**，而是返回错误信息的字符串。Agent 会根据错误信息调整下一步操作。

---

## 22.5 试一试

1. 添加更多运算（`power`, `sqrt`）
2. 让工具返回 JSON 格式而不是纯文本
3. 添加一个中间件记录每次工具调用

---

## 22.6 检查点

你现在已经能：

- 编写符合规范的工具函数（类型注解 + 文档字符串）
- 注册到 Toolkit 并验证 Schema
- 让 Agent 通过自然语言使用你的工具

---

## 下一章预告
