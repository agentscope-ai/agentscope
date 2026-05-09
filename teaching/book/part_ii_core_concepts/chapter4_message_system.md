# 第4章 消息传递机制（Msg）

> **目标**：理解AgentScope中消息的结构和作用

---

## 🎯 学习目标

学完之后，你能：
- 理解Msg消息类的设计
- 创建各种类型的消息
- 使用Msg与Agent交互
- 理解ContentBlock机制

---

## 🚀 先跑起来

```python
from agentscope.message import Msg

# 创建用户消息
user_msg = Msg(
    name="user",
    content="你好，Agent！",
    role="user"
)

# 创建助手回复
assistant_msg = Msg(
    name="assistant",
    content="你好！有什么可以帮助你的？",
    role="assistant"
)

# 打印消息
print(user_msg)
print(assistant_msg)
```

---

## 🔍 Msg消息结构

### 核心字段

| 字段 | 类型 | 说明 | Java对应 |
|------|------|------|----------|
| `name` | str | 消息发送者名称 | from/userId |
| `content` | str \| list | 消息内容 | body/payload |
| `role` | str | 角色（user/assistant/system） | messageType |

### Msg的创建

```python
# 简单文本消息
msg = Msg(name="Alice", content="Hello", role="user")

# 复杂内容（ContentBlocks）
from agentscope.message import ToolCallBlock, ToolResultBlock

msg = Msg(
    name="assistant",
    content=[
        ToolCallBlock(
            type="tool_call",
            id="call_123",
            name="get_weather",
            arguments='{"city": "北京"}'
        )
    ],
    role="assistant"
)
```

---

## 💡 Java开发者注意

Msg相当于Java中的协议消息对象：

```python
# Python Msg
msg = Msg(name="user", content="Hello", role="user")
```

```java
// Java等效
public class Msg {
    private String name;
    private Object content;  // String或List<Block>
    private String role;    // "user", "assistant", "system"
}
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Msg的role有哪些合法值？**
   - `"user"`, `"assistant"`, `"system"`
   - 不支持`"tool"`——工具返回用ToolResultBlock

2. **什么时候使用ContentBlocks而不是文本？**
   - 需要结构化数据时（工具调用、图像等）
   - 多模态内容（文本+图像+音频）

</details>

---

★ **Insight** ─────────────────────────────────────
- **Msg = 协议消息**，统一不同角色的消息格式
- **role区分来源**：user/assistant/system
- **ContentBlocks = 结构化内容**，支持工具调用等多模态
─────────────────────────────────────────────────
