# 6-3 追踪多Agent协作

> **目标**：追踪消息在多Agent系统中的完整流动过程

---

## 🎯 这一章的目标

学完之后，你能：
- 画出多Agent系统的消息流动图
- 理解MsgHub如何协调多个Agent
- 设计复杂的多Agent协作系统

---

## 🚀 先跑起来：多Agent辩论系统

```python showLineNumbers
import agentscope
from agentscope.message import Msg
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub, SequentialPipeline
from agentscope.model import OpenAIChatModel

# 初始化
agentscope.init()

# 创建模型
model = OpenAIChatModel(api_key="...", model="gpt-4")

# 创建主持人Agent
host = ReActAgent(
    name="Host",
    model=model,
    sys_prompt="你是一个辩论主持人，负责总结各方观点"
)

# 创建正方Agent
pro_agent = ReActAgent(
    name="ProSide",
    model=model,
    sys_prompt="你是一个正方辩手，坚持以下立场：AI应该广泛应用"
)

# 创建反方Agent
con_agent = ReActAgent(
    name="ConSide",
    model=model,
    sys_prompt="你是一个反方辩手，坚持以下立场：AI应用需要更多限制"
)

# 创建MsgHub协调多Agent
msghub = MsgHub(
    participants=[pro_agent, con_agent],
    announcement=None  # 可选：广播消息
)

# 辩论流程
async def debate(topic: str, rounds: int = 3):
    # 第一轮：发布辩题
    initial_msg = Msg(name="Host", content=f"辩题：{topic}", role="system")
    await msghub.publish(initial_msg)

    # 收集各方回应
    pro_result = await pro_agent(f"请就辩题'{topic}'发表正方观点")
    con_result = await con_agent(f"请就辩题'{topic}'发表反方观点")

    # 多轮辩论
    for i in range(rounds - 1):
        # 正方回应反方
        pro_response = await pro_agent(f"反方观点：{con_result.content}，请反驳")
        # 反方回应正方
        con_response = await con_agent(f"正方观点：{pro_result.content}，请反驳")

    # 最终总结
    summary = await host(
        f"请总结以下辩论：\n正方：{pro_response.content}\n反方：{con_response.content}"
    )

    return {
        "pro": pro_response.content,
        "con": con_response.content,
        "summary": summary.content
    }
```

---

## 🔍 追踪消息的完整旅程

### 第一步：创建MsgHub并订阅Agent

```
┌─────────────────────────────────────────────────────────────┐
│  MsgHub初始化                                              │
│                                                             │
│  msghub = MsgHub(participants=[pro_agent, con_agent])     │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ MsgHub内部状态：                                     │   │
│  │ - subscribed_agents: [pro_agent, con_agent]        │   │
│  │ - message_queue: []                                │   │
│  │ - announcement: None                               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### 第二步：发布辩题

```
┌─────────────────────────────────────────────────────────────┐
│  Host发布辩题                                              │
│                                                             │
│  initial_msg = Msg(                                        │
│      name="Host",                                           │
│      content="辩题：AI应该广泛应用",                        │
│      role="system"                                          │
│  )                                                         │
│                                                             │
│  msghub.publish(initial_msg)                               │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│   ProSide Agent         │     │   ConSide Agent         │
│   收到：辩题消息         │     │   收到：辩题消息         │
└─────────────────────────┘     └─────────────────────────┘
              │                               │
              ▼                               ▼
```

### 第三步：Agent并行处理

```
┌─────────────────────────────────────────────────────────────┐
│  FanoutPipeline模式：Agent并行处理                          │
│                                                             │
│  ┌───────────────────┐       ┌───────────────────┐        │
│  │  ProSide Agent    │       │  ConSide Agent    │        │
│  │                   │       │                   │        │
│  │ 1. 思考正方观点   │       │ 1. 思考反方观点   │        │
│  │ 2. 调用Model      │       │ 2. 调用Model      │        │
│  │ 3. 生成回复       │       │ 3. 生成回复       │        │
│  └───────────────────┘       └───────────────────┘        │
│           │                           │                    │
│           ▼                           ▼                    │
│  ┌───────────────────┐       ┌───────────────────┐        │
│  │ pro_result.content│       │ con_result.content│        │
│  └───────────────────┘       └───────────────────┘        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
```

### 第四步：多轮辩论

```
┌─────────────────────────────────────────────────────────────┐
│  多轮辩论循环                                               │
│                                                             │
│  for round in range(rounds):                               │
│      │                                                     │
│      ├─► Pro回应Con ─► Con回应Pro                         │
│      │       │               │                             │
│      │       ▼               ▼                             │
│      │   [正方反驳]       [反方反驳]                       │
│      │                                                     │
│      └──────► 继续下一轮                                    │
└─────────────────────────────────────────────────────────────┘
```

### 第五步：Host总结

```
┌─────────────────────────────────────────────────────────────┐
│  Host总结                                                   │
│                                                             │
│  summary_prompt = f"""                                      │
│      请总结以下辩论：                                       │
│      正方：{pro_response.content}                           │
│      反方：{con_response.content}                           │
│  """                                                       │
│                                                             │
│  final_summary = await host(summary_prompt)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                        返回辩论结果
```

---

## 📊 多Agent协作模式对比

| 模式 | 说明 | 适用场景 | 消息流 |
|------|------|----------|--------|
| SequentialPipeline | 顺序处理 | 流水线任务 | A → B → C → ... |
| FanoutPipeline | 并行处理 | 多角度分析 | A → [B, C, D] |
| MsgHub | 发布订阅 | 事件通知、松耦合 | 发布 → 所有订阅者 |

---

## 🔬 关键代码段解析

### 代码段1：多Agent辩论系统的初始化

```python showLineNumbers
# 这是第25-56行的核心代码
# 创建主持人Agent
host = ReActAgent(
    name="Host",
    model=model,
    sys_prompt="你是一个辩论主持人，负责总结各方观点"
)

# 创建正方Agent
pro_agent = ReActAgent(
    name="ProSide",
    model=model,
    sys_prompt="你是一个正方辩手，坚持以下立场：AI应该广泛应用"
)

# 创建反方Agent
con_agent = ReActAgent(
    name="ConSide",
    model=model,
    sys_prompt="你是一个反方辩手，坚持以下立场：AI应用需要更多限制"
)

# 创建MsgHub协调多Agent
msghub = MsgHub(
    participants=[pro_agent, con_agent],
    announcement=None
)
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么每个Agent有不同的sys_prompt？ | 不同角色需要不同的行为设定 |
| MsgHub的participants是什么？ | 参与辩论的Agent列表 |
| announcement参数有什么用？ | 可选的全局广播消息 |

```
┌─────────────────────────────────────────────────────────────┐
│              多Agent系统初始化                            │
│                                                             │
│   host = ReActAgent("Host", sys_prompt="主持人...")       │
│   pro_agent = ReActAgent("ProSide", sys_prompt="正方...")  │
│   con_agent = ReActAgent("ConSide", sys_prompt="反方...")  │
│                                                             │
│   msghub = MsgHub(                                        │
│       participants=[pro_agent, con_agent]  ← 参与者列表   │
│   )                                                       │
│                                                             │
│   注意：host不在participants中，因为host是裁判            │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：多Agent系统的关键是**角色分工**——每个Agent有明确的sys_prompt定义其职责和行为。

---

### 代码段2：辩论流程是如何实现的？

```python showLineNumbers
# 这是第59-84行的核心代码
async def debate(topic: str, rounds: int = 3):
    # 发布辩题给所有参与者
    initial_msg = Msg(name="Host", content=f"辩题：{topic}", role="system")
    await msghub.publish(initial_msg)

    # 收集各方第一轮观点（并行）
    pro_result = await pro_agent(f"请就辩题'{topic}'发表正方观点")
    con_result = await con_agent(f"请就辩题'{topic}'发表反方观点")

    # 多轮辩论
    for i in range(rounds - 1):
        # 正方回应反方
        pro_response = await pro_agent(f"反方观点：{con_result.content}，请反驳")
        # 反方回应正方
        con_response = await con_agent(f"正方观点：{pro_result.content}，请反驳")

    # Host总结
    summary = await host(
        f"请总结以下辩论：\n正方：{pro_response.content}\n反方：{con_response.content}"
    )
```

**思路说明**：

| 阶段 | 操作 | 说明 |
|------|------|------|
| 初始化 | msghub.publish() | 发布辩题给参与者 |
| 第一轮 | pro_agent() + con_agent() 并行 | 各自发表观点 |
| 多轮 | for循环 | 交替反驳 |
| 总结 | host() | 主持人总结 |

```
┌─────────────────────────────────────────────────────────────┐
│              辩论流程时序图                              │
│                                                             │
│   publish(辩题) ──► [ProSide, ConSide] 收到             │
│        │                                                   │
│        ▼                                                   │
│   pro_agent() ──► 并行 ──► con_agent()                  │
│        │                         │                        │
│        ▼                         ▼                        │
│   pro_result                 con_result                  │
│        │                         │                        │
│        └──────────┬──────────────┘                        │
│                   ▼                                       │
│   for round in range(rounds-1):                          │
│       pro_agent(反驳) ──► con_agent(反驳)               │
│                                                             │
│   host(总结) ──► final_summary                           │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：辩论是**交替回合制**——正反双方轮流发言，最后由主持人总结。

---

### 代码段3：MsgHub在多Agent协作中的作用

```python showLineNumbers
# MsgHub协调多Agent的核心机制
msghub = MsgHub(participants=[pro_agent, con_agent])

# 发布消息
await msghub.publish(Msg(
    name="Host",
    content="辩题：AI应该广泛应用吗？",
    role="system"
))

# 内部实现
# 1. 遍历participants列表
# 2. 依次调用每个Agent的receive方法
# 3. Agent处理消息（可能是异步）
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| publish做了什么？ | 遍历participants，调用receive |
| Agent收到消息后？ | 根据自己的sys_prompt处理 |
| 为什么用MsgHub不用Pipeline？ | 需要同时通知所有辩手 |

```
┌─────────────────────────────────────────────────────────────┐
│              MsgHub广播消息                           │
│                                                             │
│   msghub.publish(msg)                                     │
│        │                                                 │
│        ▼                                                 │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  for agent in [pro_agent, con_agent]:               │  │
│   │      agent.receive(msg)                            │  │
│   └─────────────────────────────────────────────────────┘  │
│        │                    │                              │
│        ▼                    ▼                              │
│   pro_agent收到        con_agent收到                      │
│   ┌────────────┐        ┌────────────┐                   │
│   │ 思考正方观点│        │ 思考反方观点│                   │
│   └────────────┘        └────────────┘                   │
│                                                             │
│   两者同时处理，互不阻塞                                 │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：MsgHub实现**松耦合**——发布者不关心谁在听，订阅者不关心谁在说。

---

### 代码段4：FanoutPipeline vs SequentialPipeline

```python showLineNumbers
# FanoutPipeline：并行分发
fanout = FanoutPipeline([pro_agent, con_agent])
# input同时发给所有Agent
results = await fanout(input)
# 返回所有Agent的响应列表

# SequentialPipeline：顺序执行
seq = SequentialPipeline([analyzer, summarizer, translator])
# analyzer的结果传给summarizer
result = await seq(input)
```

**思路说明**：

| 类型 | 数据流 | 返回值 | 适用场景 |
|------|--------|--------|----------|
| FanoutPipeline | input → [A, B, C] | 所有响应列表 | 并行分析 |
| SequentialPipeline | A → B → C | 最终结果 | 流水线 |

```
┌─────────────────────────────────────────────────────────────┐
│              FanoutPipeline vs SequentialPipeline          │
│                                                             │
│   FanoutPipeline:                                          │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                    input                             │  │
│   │                  ↙     ↘                           │  │
│   │              A         B                           │  │
│   │               ↘     ↙                             │  │
│   │                [result_A, result_B]               │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   SequentialPipeline:                                      │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  input → A → result1 → B → result2 → C → final   │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
│   关键区别：Fanout并行，Sequential顺序                  │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Fanout是**一对多**，Sequential是**链式**。根据协作模式选择。

---

## 💡 Java开发者注意

MsgHub类似Java的**消息队列（Message Queue）**：

| MsgHub概念 | Java对应 | 说明 |
|------------|----------|------|
| publish() | queue.send() | 发送消息 |
| subscribe() | @KafkaListener | 订阅消息 |
| announcement | Topic广播 | 广播消息 |

```python
# Python MsgHub - 发布订阅
msghub = MsgHub(participants=[agent_a, agent_b])
await msghub.publish(Msg(name="system", content="通知"))

# Java Kafka - 发布订阅
kafkaTemplate.send("topic", "message");
@KafkaListener(topics = "topic")
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **MsgHub和Pipeline的核心区别是什么？**
   - MsgHub：发布订阅模式，一个消息可以同时发给所有订阅者
   - Pipeline：顺序执行模式，消息像流水线一样依次经过每个处理者
   - MsgHub适合"通知"场景，Pipeline适合"流程处理"场景

2. **多Agent辩论为什么要用FanoutPipeline而不是SequentialPipeline？**
   - 因为正方和反方需要**同时**收到辩题，而不是一前一后
   - 并行处理能提高效率，也符合真实辩论场景

3. **如果增加一个"裁判Agent"，MsgHub需要怎么改？**
   - 只需要在创建MsgHub时加入裁判Agent：
   - `msghub = MsgHub(participants=[pro_agent, con_agent, judge_agent])`
   - 裁判会和其他Agent一样收到所有消息

4. **MsgHub的announcement参数有什么用？**
   - 用于广播消息给所有订阅者
   - 比如系统公告、全场通知等

</details>

---

★ **Insight** ─────────────────────────────────────
- **MsgHub = 消息中枢**，实现发布订阅模式
- **FanoutPipeline = 并行分发**，多Agent同时处理
- **SequentialPipeline = 顺序执行**，按步骤处理
- 多Agent协作的关键是**选择合适的协调模式**
─────────────────────────────────────────────────
