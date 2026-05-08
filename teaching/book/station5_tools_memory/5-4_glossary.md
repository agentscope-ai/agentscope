# 5-4 术语其实很简单

> **目标**：用通俗易懂的话解释工具和记忆相关的术语

---

## 📖 术语其实很简单

### **Tool** = **工具**

> "就是Agent的**手脚**，让它能做具体的事"

**说人话**：计算器、搜索、发送邮件，都是Tool

```
Tool例子：
- search_weather() - 查天气
- calculate() - 数学计算
- send_email() - 发邮件
```

---

### **Toolkit** = **工具箱**

> "就是放工具的**盒子**"

**说人话**：把多个Tool装在一起，方便管理

```python
toolkit = Toolkit()
toolkit.register_tool_function(search_weather, group_name="weather")
toolkit.register_tool_function(calculate, group_name="basic")
toolkit.register_tool_function(send_email, group_name="email")
```

---

### **ToolResponse** = **工具响应**

> "就是工具执行后返回的结果包装"

**说人话**：所有工具函数必须返回 `ToolResponse` 对象

```python
from agentscope.tool import ToolResponse

def calculate(expression: str) -> ToolResponse:
    # 安全地解析并计算简单表达式
    result = str(eval(expression))  # 实际生产中请用 ast.literal_eval
    return ToolResponse(result=result)
```

---

### **Memory** = **记忆**

> "就是Agent的**大脑皮层**，能记住之前聊过什么"

**两种记忆**：
- 短期记忆 = 工作记忆 = 内存 = 用完就忘
- 长期记忆 = 持久记忆 = Redis = 忘不掉

---

## 📊 工具与记忆全景图

```
┌─────────────────────────────────────────────────────────────┐
│                    工具与记忆系统                             │
│                                                             │
│  ┌───────────────────┐       ┌───────────────────┐        │
│  │      Toolkit       │       │      Memory        │        │
│  │    （工具箱）      │       │     （记忆）       │        │
│  └─────────┬─────────┘       └─────────┬─────────┘        │
│            │                             │                  │
│            ▼                             ▼                  │
│  ┌───────────────────┐       ┌───────────────────┐        │
│  │ Tool: search      │       │ InMemory: 内存    │        │
│  │ Tool: calculate   │       │ Redis: 持久化      │        │
│  │ Tool: send_email  │       │ Mem0: AI记忆      │        │
│  └───────────────────┘       └───────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Tool和Toolkit的区别？**
   - Tool是单个工具
   - Toolkit是工具的集合

2. **什么时候用InMemoryMemory，什么时候用RedisMemory？**
   - 单次对话 → InMemoryMemory
   - 跨会话记住 → RedisMemory

</details>

---

★ **Insight** ─────────────────────────────────────
- **Tool是Agent的手脚**，让它能执行操作
- **Toolkit是工具箱**，通过 `register_tool_function()` 注册工具
- **ToolResponse是响应**，所有工具必须返回此对象
- **Memory是记忆**，让Agent记住对话历史
─────────────────────────────────────────────────
