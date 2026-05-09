# 2-5 术语其实很简单

> **目标**：用通俗易懂的话解释消息相关的术语

---

## 📖 术语其实很简单

### **Msg** = **消息**

> "就像微信消息，有发送者、内容、接收者"

**说人话**：Msg就是AgentScope里的"通用语言"，所有组件用它交流

```
Msg
 ├── name: 谁发的？ ("user", "assistant", "system")
 ├── content: 发什么？ ("你好", "天气查询结果")
 └── role: 什么角色？ (user/assistant/system)
```

---

### **Pipeline** = **流水线**

> "就像工厂的流水线，一件产品从原材料变成成品要经过多道工序"

**说人话**：把多个Agent串起来，按顺序处理

```
原材料 → 工序A → 工序B → 工序C → 成品
  Msg  → AgentA → AgentB → AgentC → 新Msg
```

**两种Pipeline**：
- SequentialPipeline：按顺序，一个接一个
- FanoutPipeline：广播，同时发给多个

---

### **MsgHub** = **消息中心/邮局**

> "就像邮局，你寄信进去，订报的人都会收到"

**说人话**：广播消息给所有订阅者

```
发件人 → 邮局 → 订户A
             → 订户B
             → 订户C
```

**核心概念**：
- **publish**：发布消息
- **subscribe**：订阅消息

---

### **ContentBlock** = **内容块**

> "就像邮件的附件，可以是不同的类型"

**说人话**：Msg的content可以是多种类型的ContentBlock

```python
from agentscope.message import TextBlock, ImageBlock, AudioBlock

# 文本内容块
content = TextBlock(text="这是一段文字")

# 图片内容块
content = ImageBlock(url="https://example.com/image.jpg")

# 混合内容
content = [
    TextBlock(text="看这张图："),
    ImageBlock(url="https://example.com/image.jpg")
]
```

---

### **role** = **角色**

> "就像戏剧里的角色，user是观众，assistant是演员"

**说人话**：区分消息是谁发的

| role | 含义 | 使用场景 |
|------|------|----------|
| `"user"` | 用户 | 用户说的话 |
| `"assistant"` | 助手 | AI回复 |
| `"system"` | 系统 | 系统消息/配置 |

---

## 📊 消息系统全景图

```
┌─────────────────────────────────────────────────────────────┐
│                     消息系统                                  │
│                                                             │
│   用户 ──► Msg ──► Pipeline ──► Agent ──► Model            │
│                │                    │                       │
│                │                    ├──► Memory（记忆）      │
│                │                    │                        │
│                │                    └──► Toolkit（工具）    │
│                │                                             │
│                ▼                                             │
│           MsgHub ──► 订阅者A                                 │
│                ├──► 订阅者B                                  │
│                └──► 订阅者C                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 术语对照表

| AgentScope | 说人话 | Java对照 | 示例 |
|------------|--------|----------|------|
| Msg | 消息 | POJO/DTO | `Msg(name="user", content="hi")` |
| Pipeline | 流水线 | Chain of Responsibility | `SequentialPipeline([a, b])` |
| MsgHub | 邮局/广播站 | EventBus | `await hub.broadcast(msg)` |
| ContentBlock | 内容块 | MediaType | `TextBlock(text="hi")` |
| role | 角色 | MessageType | `"user"`, `"assistant"` |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Pipeline和MsgHub的核心区别？**
   - Pipeline：顺序处理，上一输出是下一输入
   - MsgHub：广播通知，所有订阅者收到相同消息

2. **ContentBlock有什么用？**
   - 支持多种类型的内容
   - 文本、图片、音频都可以
   - 让消息更丰富

3. **什么时候Pipeline和MsgHub一起用？**
   - Pipeline处理完，通过MsgHub广播给多个订阅者
   - 典型场景：分析→通知

</details>

---

★ **Insight** ─────────────────────────────────────
- **Msg是数据载体**，Pipeline是处理器，MsgHub是广播站
- 三者配合实现复杂的消息流转
- 理解它们的关系，就能理解AgentScope的消息系统
─────────────────────────────────────────────────
