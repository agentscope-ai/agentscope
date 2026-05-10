# 1-3 术语其实很简单

> **目标**：用通俗易懂的话解释核心术语，让它们不再陌生

---

## 📖 术语其实很简单

### **Agent** = **智能体**

> "就像一个**机器人**，能接收你的问题，然后想办法回答你"

**官方定义**：能够感知环境、做出决策、执行动作的智能体

**AgentScope中的Agent**：
```python
from agentscope.message import Msg

agent = Agent(
    name="Alice",
    model=OpenAIChatModel(...),
    sys_prompt="你是一个友好的助手"
)
response = await agent(Msg(name="user", content="你好", role="user"))
# agent就是Agent，它能接收消息并回复
```

**Java开发者注意**：Agent就像Java的一个**Service Bean**：
- 有自己的状态（Memory）
- 能调用其他服务（Model/Tool）
- 接收输入，处理后返回输出

---

### **Model** = **模型**

> "就是那个**会思考的大脑**，可能是GPT-4，可能是Claude"

**官方定义**：大语言模型的抽象，能接收文本输入并生成文本输出

**AgentScope中的Model**：
```python
model = OpenAIChatModel(api_key="...", model="gpt-4")
# model就是Model，它负责实际的"思考"
```

---

### **Toolkit** = **工具箱**

> "就像Agent的**工具箱**，里面有各种工具能让Agent用"

**官方定义**：管理一组可调用工具的容器

**Toolkit能做什么**：
```python
toolkit = Toolkit()
toolkit.register_tool_function(execute_python_code, group_name="code")
toolkit.register_tool_function(calculate, group_name="basic")
```

---

### **Memory** = **记忆**

> "就像Agent的**大脑皮层**，能记住之前聊过什么"

**官方定义**：存储和管理Agent对话历史的组件

**两种Memory**：
- **InMemoryMemory**：短期记忆，关机就没
- **RedisMemory**：长期记忆，持久保存

---

## 📊 核心关系图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户输入                             │
│                         "你好"                              │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      Agent（协调者）                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 1. 创建Msg  2. 保存Memory  3. 调用Model  4. 返回Msg   │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Model（模型接口）                         │
│         API JSON ───────────────────────────► 响应JSON     │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
                     返回回复给用户
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Agent和Model的区别是什么？**
   - Agent是协调者，类似Controller
   - Model是大脑，负责实际思考
   - 没有Model，Agent不知道该怎么回答

2. **Toolkit和Tool的区别是什么？**
   - Toolkit是容器，管理多个Tool
   - Tool是具体的功能

3. **什么时候用InMemoryMemory，什么时候用RedisMemory？**
   - 单次对话、快速原型 → InMemoryMemory
   - 需要跨会话记住 → RedisMemory

</details>

---

★ **Insight** ─────────────────────────────────────
- **Agent = 机器人**，协调各方完成任务
- **Model = 大脑**，负责实际思考
- **Toolkit = 工具箱**，Agent执行任务的能力
- **Memory = 记忆**，让Agent知道之前发生了什么
─────────────────────────────────────────────────
