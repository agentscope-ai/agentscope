# 第10章 Toolkit工具系统

> **目标**：理解Toolkit如何扩展Agent能力

---

## 🎯 学习目标

学完之后，你能：
- 注册和管理工具函数
- 调用外部API
- 处理工具返回结果
- 组合使用多个工具

---

## 🚀 先跑起来

```python
from agentscope.tool import Toolkit

# 创建Toolkit
toolkit = Toolkit()

# 注册工具函数
@toolkit.register_tool_function(
    name="get_weather",
    description="获取城市天气"
)
def get_weather(city: str) -> str:
    return f"{city}今天天气晴朗"

# Agent使用Toolkit
agent = ReActAgent(
    name="助手",
    toolkit=toolkit,
    ...
)
```

---

## 🔍 工具调用流程

```
Agent决定调用工具
       ↓
Toolkit解析工具名和参数
       ↓
执行工具函数
       ↓
返回ToolResultBlock
       ↓
Agent处理结果
```

---

## 💡 Java开发者注意

Toolkit类似Java的SPI（Service Provider Interface）：

```python
# Python Toolkit
class Toolkit:
    def register_tool_function(self, func, name, description):
        self._tools[name] = func
```

---

★ **Insight** ─────────────────────────────────────
- **Toolkit = 工具注册器**，统一管理函数
- **register_tool_function** = 工具"插拔"
- **ToolResultBlock** = 工具返回包装
─────────────────────────────────────────────────
