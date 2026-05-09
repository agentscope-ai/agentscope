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

## 🔬 关键代码段解析

### 代码段1：Memory为什么能保存对话历史？

```python showLineNumbers
# 这是第69-78行
memory = InMemoryMemory()

# 添加消息
memory.add(Msg(name="user", content="你好"))
memory.add(Msg(name="assistant", content="你好！"))

# 获取历史
history = memory.get()
# [Msg(...), Msg(...)]
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| `add`做了什么？ | 把Msg存入列表 |
| `get`返回什么？ | 返回所有保存的Msg列表 |
| 存在哪里？ | Python的内存（变量） |

```
┌─────────────────────────────────────────────────────────────┐
│              InMemoryMemory 工作原理                       │
│                                                             │
│   memory = InMemoryMemory()                              │
│                                                             │
│   add(Msg("user", "你好"))                               │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.messages = [Msg("user", "你好")]            │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   add(Msg("assistant", "你好！"))                        │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  self.messages = [Msg("user", "你好"),            │  │
│   │                  Msg("assistant", "你好！")]        │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   get()                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  return self.messages  ──► 历史列表                 │  │
│   └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：InMemoryMemory就是一个**列表**，简单但高效。断电就丢，所以叫"短期记忆"。

---

### 代码段2：RedisMemory为什么能持久保存？

```python showLineNumbers
# 这是第85-98行
memory = RedisMemory(
    host="localhost",
    port=6379,
    key_prefix="user_123_"
)

# 添加消息（自动持久化）
memory.add(Msg(name="user", content="我叫张三"))

# 即使重启，记忆还在
memory2 = RedisMemory(host="localhost", port=6379, key_prefix="user_123_")
history = memory2.get()  # 还能获取之前的对话
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| RedisMemory和InMemoryMemory的区别？ | 数据存在Redis，不是内存 |
| 为什么能跨会话？ | Redis是独立进程，不受Python重启影响 |
| `key_prefix`有什么用？ | 区分不同用户/会话的数据 |

```
┌─────────────────────────────────────────────────────────────┐
│              RedisMemory 工作原理                          │
│                                                             │
│   ┌─────────────────┐      ┌─────────────────┐        │
│   │   Python进程     │      │   Redis进程     │        │
│   │                   │      │                 │        │
│   │  RedisMemory    │ ──►  │  SET user_123_ │        │
│   │  .add()         │      │  "Msg序列化..." │        │
│   │                   │      │                 │        │
│   │  RedisMemory    │ ◄──  │  GET user_123_ │        │
│   │  .get()         │      │  "Msg序列化..." │        │
│   └─────────────────┘      └─────────────────┘        │
│          │                         ▲                    │
│          │                         │                    │
│          ▼                         │                    │
│   重启Python         ──►  数据还在Redis里                │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：RedisMemory把Msg**序列化**后存到Redis。Redis是独立进程，Python重启不影响数据。

---

### 代码段3：Memory在Agent中的作用

```python showLineNumbers
agent = ReActAgent(
    name="Assistant",
    model=model,
    memory=InMemoryMemory()  # Agent的记忆组件
)

# 对话过程
response = await agent("你好")
# Agent内部：
# 1. memory.add(user_msg)      # 保存用户消息
# 2. memory.add(assistant_msg)  # 保存回复
# 3. memory.get()               # 获取历史，发送给Model
```

**思路说明**：

| 阶段 | 操作 | 说明 |
|------|------|------|
| 接收消息 | `memory.add(user_msg)` | 保存用户说了什么 |
| 生成回复 | `memory.get()` | 获取历史上下文 |
| 回复后 | `memory.add(assistant_msg)` | 保存Agent回复了什么 |

```
┌─────────────────────────────────────────────────────────────┐
│              Memory在Agent中的作用                         │
│                                                             │
│   对话历史：                                               │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  [user: 你好, assistant: 你好！有什么帮助？,       │  │
│   │   user: 我想订机票, assistant: 好的...]           │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   发送给Model的历史：                                      │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  system: 你是一个助手                              │  │
│   │  user: 你好                                       │  │
│   │  assistant: 你好！有什么帮助？                    │  │
│   │  user: 我想订机票                                 │  │
│   │  assistant: 好的...                               │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   没有Memory = 每次都是新对话，Agent不记得之前聊过      │
│   有Memory = 完整的对话上下文                            │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Memory让Agent拥有**连续记忆**。没有Memory，每次对话都是全新的；有了Memory，Agent能理解上下文。

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
