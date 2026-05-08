# 6-1 Pipeline是什么（多Agent协作）

> **目标**：理解如何用Pipeline编排多个Agent的协作

---

## 🎯 这一章的目标

学完之后，你能：
- 理解SequentialPipeline的顺序协作
- 理解FanoutPipeline的并行协作
- 设计多Agent协作流程

---

## 🚀 多Agent协作示例

```python showLineNumbers
import agentscope
from agentscope.agent import ReActAgent
from agentscope.pipeline import SequentialPipeline, FanoutPipeline

# 创建多个Agent
translator = ReActAgent(name="Translator", model=..., sys_prompt="...")
reviewer = ReActAgent(name="Reviewer", model=..., sys_prompt="...")
formatter = ReActAgent(name="Formatter", model=..., sys_prompt="...")

# SequentialPipeline - 翻译→校对→格式化
translation_pipeline = SequentialPipeline([
    translator,
    reviewer,
    formatter
])

# 运行
result = await translation_pipeline("请把这段中文翻译成英文")
```

---

## 🔍 顺序协作 vs 并行协作

### SequentialPipeline - 顺序

```
┌─────────────────────────────────────────────────────────────┐
│               SequentialPipeline                            │
│                                                             │
│  输入 ──► Agent A ──► Agent B ──► Agent C ──► 输出        │
│                                                             │
│  一个接一个，上一步输出是下一步输入                          │
└─────────────────────────────────────────────────────────────┘
```

**适合场景**：
- 翻译→校对→格式化
- 分析→推理→回答
- 预处理→处理→后处理

### FanoutPipeline - 并行

```
┌─────────────────────────────────────────────────────────────┐
│                FanoutPipeline                               │
│                                                             │
│                    ┌─► Agent B                             │
│  输入 ─────────────┤                                         │
│                    ├─► Agent C                             │
│                    └─► Agent D                             │
│                                                             │
│  一个输入，同时发给多个Agent                                 │
└─────────────────────────────────────────────────────────────┘
```

**适合场景**：
- 头脑风暴：一个问题问多个专家
- 多角度分析：同时做正面和负面分析
- 投票决策：收集多个意见

---

## 💡 Java开发者注意

Pipeline类似Java的**Stream API**和**责任链模式**：

```java
// Java Stream
list.stream()
    .filter(predicate)
    .map(mapper)
    .collect(toList());

// AgentScope Pipeline
SequentialPipeline([filter_agent, map_agent, collect_agent])
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **什么场景用SequentialPipeline？**
   - 任务有顺序依赖
   - 一步一步处理

2. **什么场景用FanoutPipeline？**
   - 任务独立，可以并行
   - 需要多角度分析

</details>

---

★ **Insight** ─────────────────────────────────────
- **SequentialPipeline** = 流水线 = 一个接一个
- **FanoutPipeline** = 广播 = 一个输入，多个输出
- 选择取决于任务是否有依赖关系
─────────────────────────────────────────────────
