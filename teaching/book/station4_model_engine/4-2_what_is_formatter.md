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
from agentscope import Msg
from agentscope.formatter import FormatterBase, JsonFormatter

# 使用内置Formatter
formatter = JsonFormatter()

# 转换Msg列表为API格式
messages = [
    Msg(name="system", content="你是助手", role="system"),
    Msg(name="user", content="你好", role="user"),
]

api_format = formatter.format(messages)
# 输出适合API的JSON格式
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
from agentscope import Msg

class FormatterBase(ABC):
    """Formatter基类"""

    @abstractmethod
    def format(self, messages: list[Msg]) -> dict:
        """将Msg列表转换为API格式"""
        pass

    @abstractmethod
    def parse(self, response: dict) -> Msg:
        """将API响应转换为Msg"""
        pass

# 具体实现
class OpenAIFormatter(FormatterBase):
    def format(self, messages):
        return {
            "model": "gpt-4",
            "messages": [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
        }

    def parse(self, response):
        return Msg(
            name="assistant",
            content=response["choices"][0]["message"]["content"],
            role="assistant"
        )
```

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
formatter = OpenAIFormatter();
apiRequest = formatter.format(messages);  // Msg → API JSON
msg = formatter.parse(response);  // API JSON → Msg
```

| AgentScope | Java | 说明 |
|------------|------|------|
| Formatter | ObjectMapper | 格式转换 |
| format() | writeValueAsString() | 对象转JSON |
| parse() | readValue() | JSON转对象 |

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
