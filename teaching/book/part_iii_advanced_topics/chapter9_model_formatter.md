# 第9章 Model与Formatter

> **目标**：理解Model抽象和Formatter适配机制

---

## 🎯 学习目标

学完之后，你能：
- 理解Model的统一接口设计
- 掌握Formatter的消息转换
- 选择正确的Formatter
- 调试模型调用问题

---

## 🚀 先跑起来

```python
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# 创建模型
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-key"
)

# 创建Formatter（根据模型选择）
formatter = OpenAIChatFormatter()

# 模型调用
response = await model.chat(
    messages=formatter.format([user_msg, assistant_msg]),
    temperature=0.7
)

# 解析响应
result = formatter.parse(response)
```

---

## 🔍 核心概念

### Model：统一接口

```
┌─────────────────────────────────────┐
│         ChatModelBase               │
│    (统一接口：chat/moderation)       │
├─────────────────────────────────────┤
│  OpenAI    │ Anthropic │ DashScope  │
│  ChatModel │ ChatModel │ ChatModel   │
└─────────────────────────────────────┘
```

### Formatter：格式适配

不同模型有不同API格式，Formatter负责转换：

```
Msg列表 → [Formatter] → 模型API格式 → 模型 → 响应 → [Formatter] → Msg
```

| 模型 | Formatter | API格式 |
|------|-----------|---------|
| OpenAI | OpenAIChatFormatter | {"role": "user", "content": "..."} |
| Anthropic | AnthropicChatFormatter | {"role": "user", "content": [{"type": "text", ...}]} |
| DashScope | DashScopeChatFormatter | {"role": "user", "text": "..."} |

---

## 💡 Java开发者注意

类似适配器模式：

```python
# Python Formatter
class OpenAIFormatter:
    def format(self, messages):
        return [{"role": m.role, "content": m.content} for m in messages]
```

```java
// Java Adapter
public class OpenAIAdapter implements ModelAdapter {
    public Map<String, Object> format(List<Message> messages) {
        return messages.stream()
            .map(m -> Map.of("role", m.role, "content", m.content))
            .collect(toList());
    }
}
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **为什么需要Formatter？**
   - 不同模型API格式不同
   - Formatter统一转换，对上层透明

2. **怎么选择Formatter？**
   - 根据使用的模型选择对应的Formatter
   - OpenAI模型 → OpenAIChatFormatter

</details>

---

★ **Insight** ─────────────────────────────────────
- **Model = 统一接口**，屏蔽不同模型的差异
- **Formatter = 格式适配器**，转换Msg到API格式
- 组合使用：Model(Formatter(Msg)) → API → Formatter(Response) → Msg
─────────────────────────────────────────────────
