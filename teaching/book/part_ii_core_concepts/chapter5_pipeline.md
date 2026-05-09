# 第5章 管道与流水线（Pipeline）

> **目标**：理解Pipeline如何编排Agent的执行顺序

---

## 🎯 学习目标

学完之后，你能：
- 理解Pipeline的设计模式
- 使用SequentialPipeline串联Agent
- 使用FanoutPipeline并发送Agent
- 选择合适的Pipeline类型

---

## 🚀 先跑起来

```python
from agentscope.pipeline import SequentialPipeline, FanoutPipeline
from agentscope.agent import ReActAgent

# 创建Agent
analyzer = ReActAgent(name="Analyzer", ...)
summarizer = ReActAgent(name="Summarizer", ...)

# 顺序管道：A的结果传给B
pipeline = SequentialPipeline(agents=[analyzer, summarizer])
result = await pipeline(input_message)

# 并行管道：同时发给A、B、C
multi_agent = FanoutPipeline(agents=[agent1, agent2, agent3])
results = await multi_agent(input_message)
```

---

## 🔍 Pipeline类型

### SequentialPipeline：顺序执行

```
输入 → Agent1 → 结果1 → Agent2 → 结果2 → Agent3 → 输出
```

```python
pipeline = SequentialPipeline(agents=[analyzer, summarizer, formatter])
result = await pipeline(input_text)
```

### FanoutPipeline：并行分发

```
输入 → [Agent1, Agent2, Agent3] → [结果1, 结果2, 结果3]
```

```python
# 每个Agent处理相同输入
multi = FanoutPipeline(agents=[analyst1, analyst2, analyst3])
results = await multi(topic)  # 返回列表
```

---

## 💡 Java开发者注意

Pipeline类似Java的责任链模式或Stream的`pipe`：

```python
# Python Pipeline
result = SequentialPipeline([a1, a2, a3])(input)
```

```java
// Java Stream
var result = Stream.of(a1, a2, a3)
    .reduce(input, (msg, agent) -> agent.process(msg));
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **什么时候用SequentialPipeline？**
   - Agent之间有依赖，需要前一Agent的输出
   - 流水线式处理

2. **什么时候用FanoutPipeline？**
   - 需要并行处理同一任务
   - 多角度分析/投票场景

</details>

---

★ **Insight** ─────────────────────────────────────
- **SequentialPipeline = 流水线**，A→B→C顺序执行
- **FanoutPipeline = 广播**，同时分发给多个Agent
- 选择依据：**数据流是否有依赖**
─────────────────────────────────────────────────
