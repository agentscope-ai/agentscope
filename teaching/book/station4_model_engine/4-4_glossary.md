# 4-4 术语其实很简单

> **目标**：用通俗易懂的话解释Model相关的术语

---

## 📖 术语其实很简单

### **ChatModelBase** = **模型基类**

> "就是所有模型的**老祖宗**，定义了模型应该长什么样"

**说人话**：不管是什么模型（OpenAI/Claude），都要实现这个接口

```python showLineNumbers
class ChatModelBase(ABC):
    @abstractmethod
    def __call__(self, messages: list[Msg]) -> Msg:
        """统一调用方式"""
        pass
```

---

### **OpenAIChatModel** = **OpenAI模型**

> "就是用OpenAI的GPT模型来思考"

**支持的模型**：
- `gpt-4` - 最强
- `gpt-4-turbo` - 性价比
- `gpt-3.5-turbo` - 便宜快速

```python showLineNumbers
model = OpenAIChatModel(
    api_key="sk-xxx",
    model="gpt-4"
)
```

---

### **AnthropicChatModel** = **Claude模型**

> "就是用Anthropic的Claude模型来思考"

**支持的模型**：
- `claude-3-opus` - 最强
- `claude-3-sonnet` - 性价比
- `claude-3-haiku` - 快速

```python showLineNumbers
model = AnthropicChatModel(
    api_key="sk-ant-xxx",
    model="claude-3-opus"
)
```

---

### **Formatter** = **格式化器**

> "就是**翻译官**，把消息翻译成各API认识的格式"

**说人话**：OpenAI和Claude的格式不一样，Formatter负责翻译

```
Msg → Formatter → API JSON
API JSON → Formatter → Msg
```

---

### **API Key** = **API密钥**

> "就是使用API的**通行证**"

**说人话**：没有Key就不能调用API，就像没有门票不能进演唱会

```bash
# 设置环境变量（推荐）
export OPENAI_API_KEY="sk-xxx"

# 或者在代码中传入（不推荐）
model = OpenAIChatModel(api_key="sk-xxx")
```

---

## 📊 模型调用全景图

```
┌─────────────────────────────────────────────────────────────┐
│                    模型调用链路                             │
│                                                             │
│  Agent ──► Formatter ──► ChatModel ──► API ──► ChatModel    │
│                  │                │           │     │      │
│                  │                │           │     │      │
│            转换为API格式        HTTP调用   响应   解析Msg │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 支持的模型                                            │  │
│  │                                                       │  │
│  │ OpenAI: gpt-4, gpt-3.5-turbo                       │  │
│  │ Claude: claude-3-opus, claude-3-sonnet             │  │
│  │ DashScope: qwen-max, qwen-turbo                     │  │
│  │ Ollama: llama2, mistral                            │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 术语对照表

| AgentScope | 说人话 | Java对照 | 示例 |
|------------|--------|----------|------|
| ChatModelBase | 模型基类 | 接口/抽象类 | 定义统一契约 |
| OpenAIChatModel | GPT模型 | HTTP Client | OpenAI适配器 |
| AnthropicChatModel | Claude模型 | HTTP Client | Claude适配器 |
| Formatter | 格式化器 | ObjectMapper | 格式转换 |
| API Key | 通行证 | Token | 认证凭证 |
| messages | 消息列表 | List<Message> | 对话历史 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **ChatModelBase的作用是什么？**
   - 定义统一的模型调用接口
   - 所有模型都要实现它
   - 便于切换不同模型

2. **Formatter和ChatModel什么关系？**
   - Formatter负责格式转换
   - ChatModel负责调用API
   - 两者配合工作

3. **API Key为什么要放环境变量？**
   - 安全：代码不会被提交到Git
   - 隔离：不同环境用不同Key
   - 管理：方便更换Key

</details>

---

★ **Insight** ─────────────────────────────────────
- **ChatModelBase是统一接口**，不管用什么模型，调用方式都一样
- **Formatter负责格式转换**，让不同API能接收正确格式
- **API Key是通行证**，没有就调用不了
─────────────────────────────────────────────────
