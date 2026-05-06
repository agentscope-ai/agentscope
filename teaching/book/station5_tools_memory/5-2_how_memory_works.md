# 5-2 Memory是怎么工作的

> **目标**：理解短期记忆和长期记忆的区别和使用场景

---

## 🎯 这一章的目标

学完之后，你能：
- 理解Memory的两种类型
- 使用InMemoryMemory和RedisMemory
- 知道什么时候用哪种Memory

---

## 🚀 先跑起来

```python showLineNumbers
import agentscope
from agentscope.memory import InMemoryMemory, RedisMemory

# 短期记忆 - 保存在内存中
memory1 = InMemoryMemory()

# 长期记忆 - 保存在Redis中
memory2 = RedisMemory(
    host="localhost",
    port=6379,
    key_prefix="agent_"
)

# 传给Agent
agent = ReActAgent(
    name="Assistant",
    model=model,
    memory=memory1  # 选择记忆类型
)
```

---

## 🔍 Memory的两种类型

```
┌─────────────────────────────────────────────────────────────┐
│                      Memory类型                             │
│                                                             │
│   Memory                                                      │
│       │                                                       │
│       ├──► 短期记忆（Working Memory）                        │
│       │        │                                             │
│       │        └──► InMemoryMemory                         │
│       │             │                                       │
│       │             └──► 存在内存，断电丢失                   │
│       │                 类比：人的工作记忆                    │
│       │                                                       │
│       └──► 长期记忆（Long-term Memory）                      │
│                │                                             │
│                ├──► RedisMemory                             │
│                ├──► Mem0Memory                              │
│                └──► 持久化存储，断电不丢失                    │
│                    类比：人的长期记忆                         │
└─────────────────────────────────────────────────────────────┘
```

### 短期记忆 InMemoryMemory

```python showLineNumbers
# 特点：快，但断电丢失
memory = InMemoryMemory()

# 添加消息
memory.add(Msg(name="user", content="你好"))
memory.add(Msg(name="assistant", content="你好！"))

# 获取历史
history = memory.get()
# [Msg(...), Msg(...)]
```

### 长期记忆 RedisMemory

```python showLineNumbers
# 特点：持久化，可以跨会话
memory = RedisMemory(
    host="localhost",
    port=6379,
    key_prefix="user_123_"  # 用户隔离
)

# 添加消息（自动持久化）
memory.add(Msg(name="user", content="我叫张三"))
memory.add(Msg(name="assistant", content="好的，张三先生"))

# 即使重启，记忆还在
memory2 = RedisMemory(host="localhost", port=6379, key_prefix="user_123_")
history = memory2.get()  # 还能获取之前的对话
```

---

## 💡 Java开发者注意

Memory类似Java的**Session/Cache**：

| AgentScope | Java | 说明 |
|------------|------|------|
| InMemoryMemory | HttpSession | 内存存储 |
| RedisMemory | Redis Cache | 分布式缓存 |
| memory.add() | session.setAttribute() | 存储数据 |
| memory.get() | session.getAttribute() | 获取数据 |

```java
// Java Session
HttpSession session = request.getSession();
session.setAttribute("user", "张三");
String user = (String) session.getAttribute("user");

// AgentScope Memory
memory.add(Msg(name="user", content="张三"))
msgs = memory.get()
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **什么时候用短期记忆，什么时候用长期记忆？**
   - 短期：单次对话，不需要记住历史
   - 长期：需要跨会话记住用户偏好/信息

2. **RedisMemory和InMemoryMemory的核心区别？**
   - 存储位置：内存 vs Redis
   - 持久性：断电丢失 vs 持久保存
   - 性能：内存更快，Redis有网络开销

3. **Memory是怎么帮助Agent的？**
   - 保存对话历史
   - 让Agent知道"我们之前聊过什么"
   - 实现上下文连贯的对话

</details>

---

★ **Insight** ─────────────────────────────────────
- **短期记忆** = 工作记忆 = InMemoryMemory = 内存 = 快但丢
- **长期记忆** = 持久记忆 = RedisMemory = 持久 = 慢但稳
- 选择哪个取决于：是否需要跨会话记住
─────────────────────────────────────────────────
