# 4-1 统一接口

> **目标**：理解ChatModelBase如何统一不同模型的调用方式

---

## 🎯 这一章的目标

学完之后，你能：
- 理解ChatModelBase的抽象接口
- 使用统一的代码调用不同模型
- 切换模型只需要改一行配置

---

## 🚀 先跑起来

```python showLineNumbers
import agentscope
from agentscope.model import ChatModelBase

# 不管用什么模型，调用方式都一样
async def call_model(model: ChatModelBase, prompt: str):
    response = await model(prompt)
    return response

# 切换模型只需要改这个
# model = OpenAIChatModel(api_key="...", model="gpt-4")
model = AnthropicModel(api_key="...", model="claude-3")
# model = DashScopeModel(api_key="...", model="qwen-max")

# 调用方式完全一样
result = await call_model(model, "你好")
```

---

## 🔍 为什么需要统一接口

### 问题：每个模型的API都不一样

```
OpenAI API:
{"messages": [{"role": "user", "content": "..."}]}

Claude API:
{"prompt": "...", "max_tokens": 1024}

DashScope API:
{"input": {"messages": [...]}, "parameters": {...}}
```

### 解决方案：ChatModelBase抽象

```
┌─────────────────────────────────────────────────────────────┐
│                     应用层                                  │
│                                                             │
│  Agent 只调用 model(prompt)                               │
│                      │                                      │
│                      ▼                                      │
│              ┌─────────────────┐                           │
│              │  ChatModelBase   │ ← 统一接口               │
│              │  (抽象)           │                          │
│              └────────┬────────┘                           │
│                       │                                      │
│         ┌─────────────┼─────────────┐                       │
│         ▼             ▼             ▼                       │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐                 │
│  │  OpenAI  │ │  Claude  │ │ DashScope │                 │
│  │   Model  │ │   Model  │ │   Model   │                 │
│  └───────────┘ └───────────┘ └───────────┘                 │
│                                                             │
│  每个Model只需要实现自己的格式转换                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔍 ChatModelBase接口

```python showLineNumbers
from abc import ABC, abstractmethod

class ChatModelBase(ABC):
    """所有模型的基类"""
    
    @abstractmethod
    def __call__(self, messages: list[Msg]) -> Msg:
        """统一的调用接口"""
        pass
    
    @abstractmethod
    def format(self, messages: list[Msg]) -> dict:
        """将Msg转换为API格式"""
        pass
    
    @abstractmethod
    def parse(self, response: dict) -> Msg:
        """将API响应转换为Msg"""
        pass
```

### 实际使用示例

```python showLineNumbers
# 创建不同模型的实例
openai_model = OpenAIChatModel(api_key="sk-xxx", model="gpt-4")
claude_model = AnthropicModel(api_key="sk-ant-xxx", model="claude-3-opus")
dashscope_model = DashScopeModel(api_key="sk-xxx", model="qwen-max")

# 传给Agent时，只需要是ChatModelBase类型
agent = ReActAgent(
    model=openai_model,  # 或者 claude_model, dashscope_model
    ...
)

# 切换模型？只需要改这一行
agent = ReActAgent(
    model=claude_model,  # 改这一行就够了！
    ...
)
```

---

## 💡 Java开发者注意

ChatModelBase类似Java的**适配器模式**或**工厂模式**：

```java
// Java 适配器模式
public interface Model {
    Response chat(String prompt);
}

// 不同的实现
public class OpenAIModel implements Model {
    @Override
    public Response chat(String prompt) {
        // OpenAI特定逻辑
    }
}

public class ClaudeModel implements Model {
    @Override
    public Response chat(String prompt) {
        // Claude特定逻辑
    }
}

// 使用方不需要知道具体是哪个模型
public class Agent {
    private Model model;
    
    public void setModel(Model model) {
        this.model = model;
    }
    
    public void chat(String input) {
        model.chat(input);  // 统一接口
    }
}
```

| AgentScope | Java | 说明 |
|------------|------|------|
| ChatModelBase | 接口/抽象类 | 定义统一契约 |
| OpenAIChatModel | 具体实现 | OpenAI适配器 |
| AnthropicModel | 具体实现 | Claude适配器 |
| DashScopeModel | 具体实现 | 阿里通义适配器 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **为什么要用ChatModelBase而不是直接调用API？**
   - 统一接口，切换模型不需要改代码
   - Formatter负责格式转换，应用层不需要关心
   - 便于测试和Mock

2. **切换模型需要改几行代码？**
   - 只需要改创建模型实例的那一行
   - Agent代码完全不用动

3. **Formatter和ChatModel是什么关系？**
   - Formatter：把Msg转成API格式
   - ChatModel：调用API并返回结果
   - 两者配合工作

</details>

---

★ **Insight** ─────────────────────────────────────
- **ChatModelBase是适配器**：统一不同模型的调用方式
- **切换模型只需要改一行**：提高可维护性
- **Formatter负责格式转换**：关注点分离
─────────────────────────────────────────────────
