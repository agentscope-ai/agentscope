# 4-2 Formatter是什么

> **目标**：理解Formatter如何转换消息格式以适配不同API

---

## 🎯 这一章的目标

学完之后，你能：
- 理解Formatter的转换作用
- 使用FormatterBase创建自定义格式化器
- 理解为什么不同API需要不同Formatter

---

## 🚀 先跑起来

```python showLineNumbers
import asyncio
from agentscope.message import Msg
from agentscope.formatter import OpenAIChatFormatter

# 使用内置Formatter（注意：format是异步方法）
formatter = OpenAIChatFormatter()

# 准备Msg列表
messages = [
    Msg(name="system", content="你是助手", role="system"),
    Msg(name="user", content="你好", role="user"),
]

# 异步调用format方法，转换为API格式
api_format = await formatter.format(messages)
# 输出: [{"role": "system", "name": "system", "content": [{"type": "text", "text": "你是助手"}]}, ...]
```

---

## 🔍 Formatter的转换作用

### 问题：每个API的格式都不一样

```
Msg (统一格式)
{name="user", content="你好", role="user"}
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    Formatter转换                            │
└─────────────────────────────────────────────────────────────┘
        │
        ├──► OpenAI格式
        │    {"messages": [{"role": "user", "content": "你好"}]}
        │
        ├──► Claude格式
        │    {"prompt": "user: 你好\nassistant:"}
        │
        └──► DashScope格式
             {"input": {"messages": [...]}, "parameters": {...}}
```

---

## 🔍 Formatter的结构

```python showLineNumbers
from abc import ABC, abstractmethod
from agentscope.message import Msg

class FormatterBase(ABC):
    """Formatter基类"""

    @abstractmethod
    async def format(self, msgs: list[Msg]) -> list[dict]:
        """将Msg列表转换为API格式的字典列表"""
        pass

# 具体实现
class OpenAIChatFormatter(FormatterBase):
    """OpenAI格式转换器"""

    async def format(self, msgs: list[Msg]) -> list[dict]:
        """将Msg列表转换为OpenAI API格式"""
        formatted = []
        for msg in msgs:
            # 处理消息内容
            content = []
            for block in msg.get_content_blocks():
                if block.get("type") == "text":
                    content.append({"type": "text", "text": block.get("text", "")})
            formatted.append({
                "role": msg.role,
                "name": msg.name,
                "content": content
            })
        return formatted
```

---

## 🔬 关键代码段解析

### 代码段1：Formatter为什么需要抽象基类？

```python showLineNumbers
class FormatterBase(ABC):
    """Formatter基类"""

    @abstractmethod
    async def format(self, msgs: list[Msg]) -> list[dict]:
        """将Msg列表转换为API格式"""
        pass
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么需要抽象基类？ | 定义统一接口，让切换Formatter不影响上层代码 |
| 抽象方法是什么？ | 用`@abstractmethod`装饰的方法，子类必须实现 |
| 有什么用？ | 可以随时切换Formatter，不用改Agent代码 |

```
┌─────────────────────────────────────────────────────────────┐
│                 Formatter抽象基类设计                       │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐  │
│   │              FormatterBase (抽象)                   │  │
│   │                                                  │  │
│   │  + format(msgs) -> list[dict]  {abstract}       │  │
│   └─────────────────────────────────────────────────────┘  │
│                          ▲                                 │
│         ┌──────────────┼──────────────┐                │
│         ▼              ▼              ▼                  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐       │
│  │ OpenAI     │ │ Anthropic  │ │ DashScope  │       │
│  │ Formatter  │ │ Formatter  │ │ Formatter  │       │
│  └────────────┘ └────────────┘ └────────────┘       │
│                                                             │
│   切换模型时，只需要换Formatter，不改Agent代码            │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：**依赖倒置**原则——上层模块（Agent）依赖抽象（FormatterBase），不依赖具体实现。

---

### 代码段2：OpenAIChatFormatter的实现

```python showLineNumbers
class OpenAIChatFormatter(FormatterBase):
    """OpenAI格式转换器"""

    async def format(self, msgs: list[Msg]) -> list[dict]:
        """将Msg列表转换为OpenAI API格式"""
        formatted = []
        for msg in msgs:
            content = []
            for block in msg.get_content_blocks():
                if block.get("type") == "text":
                    content.append({"type": "text", "text": block.get("text", "")})
            formatted.append({
                "role": msg.role,
                "name": msg.name,
                "content": content
            })
        return formatted
```

**思路说明**：

| 方法 | 输入 | 输出 |
|------|------|------|
| format | `Msg(name, content, role)` | `[{"role": ..., "name": ..., "content": [...]}]` |

```
┌─────────────────────────────────────────────────────────────┐
│              OpenAI Formatter 转换示例                      │
│                                                             │
│   format:                                                   │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  [Msg("user", "你好", "user"),                    │  │
│   │   Msg("assistant", "你好！", "assistant")]          │  │
│   └─────────────────────────────────────────────────────┘  │
│                           ▼                               │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  [                                                    │  │
│   │    {"role": "user", "name": "user",                │  │
│   │     "content": [{"type": "text", "text": "你好"}]},│  │
│   │    {"role": "assistant", "name": "assistant",       │  │
│   │     "content": [{"type": "text", "text": "你好！"}]} │  │
│   │  ]                                                    │  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Formatter负责**格式化请求**，把API差异封装在Formatter内部。响应由Model处理后直接返回Msg。

---

### 代码段3：Formatter在Agent中的作用位置

```python showLineNumbers
# Agent调用Model的完整流程
async def agent_call(agent, user_input):
    # 1. 创建Msg
    msg = Msg(name="user", content=user_input, role="user")

    # 2. 格式化（Formatter）- 注意是async方法
    api_request = await agent.formatter.format([msg])

    # 3. 调用API（Model）- Model返回ChatResponse
    api_response = await agent.model(api_request, tools=agent.toolkit.get_json_schemas())

    # 4. Model自动解析响应为Msg
    response = api_response  # 已经是Msg对象

    return response
```

**思路说明**：

```
┌─────────────────────────────────────────────────────────────┐
│              Agent调用Model的完整流程                      │
│                                                             │
│   Agent                                                     │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                                                     │  │
│   │   user_input ──► Msg ──► format() ──► API请求   │  │
│   │                            │                        │  │
│   │                            ▼                        │  │
│   │                        Formatter                   │  │
│   │                                                     │  │
│   │   response ◄────────── Msg ◄─── Model返回         │  │
│   │                            ▲                        │  │
│   │                            │                        │  │
│   │                        Formatter                   │  │
│   │                                                     │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   Formatter = Msg ↔ API JSON 的翻译官                   │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Formatter在Agent和Model之间充当**翻译官**，让两者都能用统一格式（Msg）交流。响应由Model内部解析后直接返回Msg。

---

## 💡 Java开发者注意

Formatter类似Java的**ObjectMapper**（Jackson/Fastjson）：

```java
// Java ObjectMapper
ObjectMapper mapper = new ObjectMapper();

// JavaTo
String json = mapper.writeValueAsString(msgList);

// JsonTo
Msg msg = mapper.readValue(json, Msg.class);

// AgentScope Formatter
formatter = OpenAIChatFormatter();
apiRequest = await formatter.format(messages);  // Msg → API JSON列表
// 响应由Model直接返回Msg，无需parse
```

| AgentScope | Java | 说明 |
|------------|------|------|
| Formatter | ObjectMapper | 格式转换 |
| format() | writeValueAsString() | 对象转JSON |
| parse() | readValue() | 无（Model内部处理） |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **为什么需要Formatter而不是直接发Msg？**
   - 不同API接受不同格式的输入
   - Formatter把统一格式转成各API认识的格式

2. **Formatter转换的输入输出是什么？**
   - 输入：Msg列表
   - 输出：API格式的dict

3. **每个模型都需要自己的Formatter吗？**
   - 是的，因为每个API的请求格式不同
   - 但大部分是JSON格式的变体

</details>

---

★ **Insight** ─────────────────────────────────────
- **Formatter是翻译官**：把Msg翻译成各API认识的格式
- **format() = 输出翻译**：Msg → API JSON
- **parse() = 输入翻译**：API JSON → Msg
─────────────────────────────────────────────────
