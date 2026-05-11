# 第 17 章 Schema 工厂

> 本章你将理解：AgentScope 如何从配置创建对象、Schema 驱动的对象工厂、工具参数的自动提取。

---

## 17.1 工厂模式

工厂模式（Factory Pattern）的核心：**不直接 `new` 对象，而是通过配置或参数让工厂创建**。

AgentScope 有两处工厂模式：
1. 工具参数 Schema：从函数签名自动生成 JSON Schema
2. 模型/Agent 配置：从字典创建对象（`_run_config.py`）

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 17.2 工具参数的自动提取

`Toolkit.register_tool_function()` 做了一件神奇的事——从 Python 函数自动生成 JSON Schema：

```python
def get_weather(city: str, unit: str = "celsius") -> str:
    """获取城市天气

    Args:
        city (str): 城市名
        unit (str): 温度单位，celsius 或 fahrenheit
    """
    ...
```

注册后自动生成：

```json
{
    "name": "get_weather",
    "description": "获取城市天气",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名"},
            "unit": {"type": "string", "description": "温度单位", "default": "celsius"}
        },
        "required": ["city"]
    }
}
```

### 提取过程

1. **函数名** → `name`
2. **文档字符串第一行** → `description`
3. **类型注解** → `type`（`str` → `"string"`, `int` → `"integer"`）
4. **默认值** → `default`
5. **文档字符串的 Args 段** → 每个参数的 `description`
6. **无默认值的参数** → 加入 `required`

这个提取让模型知道工具的完整参数格式，从而正确生成 `ToolUseBlock`。

---

## 17.3 设计一瞥：为什么自动提取而不是手写？

```python
# 方案 A：手写 Schema（容易出错）
toolkit.register(
    name="get_weather",
    description="获取城市天气",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名"}
        },
        "required": ["city"]
    },
    func=get_weather,
)

# 方案 B：自动提取（AgentScope 的选择）
toolkit.register_tool_function(get_weather)
```

自动提取的好处：
1. **DRY**：不重复写参数信息
2. **一致性**：函数签名和 Schema 永远同步
3. **少出错**：不会有"函数改了但 Schema 忘了改"的问题

---

## 17.4 试一试

### 查看自动生成的 Schema

```python
from agentscope.tool import Toolkit

def calculate(a: int, b: int, operation: str = "add") -> int:
    """执行数学运算

    Args:
        a (int): 第一个数字
        b (int): 第二个数字
        operation (str): 运算类型，add/subtract/multiply
    """
    pass

toolkit = Toolkit()
toolkit.register_tool_function(calculate)

import json
for schema in toolkit.get_tool_schemas():
    print(json.dumps(schema, ensure_ascii=False, indent=2))
```

---

## 17.5 检查点

你现在已经理解了：

- **工厂模式**：从配置/签名自动创建对象或 Schema
- **自动 Schema 提取**：从函数签名、类型注解、文档字符串生成 JSON Schema
- **DRY 原则**：函数定义是唯一的真相来源

---

## 下一章预告

Schema 工厂自动化了对象创建。下一章看中间件管道——给工具执行加横切逻辑。
