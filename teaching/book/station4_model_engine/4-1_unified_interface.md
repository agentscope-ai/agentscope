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
from agentscope.model import (
    ChatModelBase,
    OpenAIChatModel,
    AnthropicChatModel,
    DashScopeChatModel
)

# 不管用什么模型，调用方式都一样
async def call_model(model: ChatModelBase, prompt: str):
    response = await model(prompt)
    return response

# 切换模型只需要改这个
# model = OpenAIChatModel(api_key="...", model="gpt-4")
model = AnthropicChatModel(api_key="...", model="claude-3")
# model = DashScopeChatModel(api_key="...", model="qwen-max")

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
claude_model = AnthropicChatModel(api_key="sk-ant-xxx", model="claude-3-opus")
dashscope_model = DashScopeChatModel(api_key="sk-xxx", model="qwen-max")

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

## 🔬 关键代码段解析

### 代码段1：统一接口的意义 —— 为什么需要ChatModelBase？

```python showLineNumbers
# 这是第27-37行
async def call_model(model: ChatModelBase, prompt: str):
    response = await model(prompt)  # 统一调用方式
    return response

# 切换模型只需要改这个
model = OpenAIChatModel(api_key="...", model="gpt-4")
# model = AnthropicChatModel(api_key="...", model="claude-3")

# 调用方式完全一样
result = await call_model(model, "你好")
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么要统一接口？ | 换模型不改代码 |
| `ChatModelBase`是什么？ | 抽象基类，定义统一契约 |
| 为什么能换模型不改代码？ | 因为调用方式一样：都是`model(prompt)` |

**💡 设计思想**：软件设计的核心原则是**依赖倒置**——高层模块不依赖低层模块，都依赖抽象。Agent不直接依赖OpenAI或Claude，而是依赖`ChatModelBase`接口。

---

### 代码段2：为什么需要format和parse？

```python showLineNumbers
# ChatModelBase 的两个关键方法
class ChatModelBase(ABC):
    @abstractmethod
    def format(self, messages: list[Msg]) -> dict:
        """将Msg转换为API格式"""
        pass

    @abstractmethod
    def parse(self, response: dict) -> Msg:
        """将API响应转换为Msg"""
        pass
```

**思路说明**：

```
┌─────────────────────────────────────────────────────────────┐
│              format 和 parse 的分工                        │
│                                                             │
│   format（格式化）                                          │
│   ┌─────────────────────────────────────────────────────┐ │
│   │ Msg ──────────────────────────► API请求JSON         │ │
│   │                                                     │ │
│   │ 统一格式              →        各API专属格式         │ │
│   └─────────────────────────────────────────────────────┘ │
│                                                             │
│   parse（解析）                                            │
│   ┌─────────────────────────────────────────────────────┐ │
│   │ API响应JSON ──────────────────────────────────► Msg │ │
│   │                                                     │ │
│   │ 各API专属格式         →        统一格式              │ │
│   └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

| 方法 | 方向 | 目的 |
|------|------|------|
| `format` | Msg → API格式 | 让Agent的输出能被各API接受 |
| `parse` | API格式 → Msg | 让各API的响应被Agent统一处理 |

**💡 设计思想**：这是**适配器模式**的应用。format负责"输出适配"，parse负责"输入适配"。两边都面向统一格式Msg进行转换。

---

### 代码段3：切换模型的真实例子

```python showLineNumbers
# 场景：你的应用一开始用OpenAI，后来发现Claude效果更好

# 第一版：使用OpenAI
agent = ReActAgent(
    name="MyAgent",
    model=OpenAIChatModel(
        api_key="sk-xxx",
        model="gpt-4"
    ),
    sys_prompt="你是一个有帮助的助手"
)

# 第二版：切换到Claude（只改这里！）
agent = ReActAgent(
    name="MyAgent",
    model=AnthropicChatModel(  # 只改这一行
        api_key="sk-ant-xxx",
        model="claude-3-opus"
    ),
    sys_prompt="你是一个有帮助的助手"  # 完全不用改
)
```

**思路说明**：

```
┌─────────────────────────────────────────────────────────────┐
│                  切换模型的真实案例                         │
│                                                             │
│   原来：                                                    │
│   Agent ──► OpenAIChatModel ──► OpenAI API               │
│                    │                                        │
│                    │ api_key, model参数不同                  │
│                    ▼                                        │
│   切换后：                                                  │
│   Agent ──► AnthropicChatModel ──► Claude API            │
│                    │                                        │
│                    │ 其他代码完全不用改！                     │
│                    ▼                                        │
│              只有这里不同                                   │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：这种设计让**切换成本最低**。在AI领域，模型更新换代很快，好的架构应该让切换模型像换灯泡一样简单。

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
| AnthropicChatModel | 具体实现 | Claude适配器 |
| DashScopeChatModel | 具体实现 | 阿里通义适配器 |

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
