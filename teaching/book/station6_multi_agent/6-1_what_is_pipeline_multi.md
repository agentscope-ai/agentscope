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

## 🔬 关键代码段解析

### 代码段1：为什么每个Agent都需要 sys_prompt？

```python showLineNumbers
# 这是第24-26行
translator = ReActAgent(name="Translator", model=..., sys_prompt="你是一个专业翻译员，擅长中英文互译")
reviewer = ReActAgent(name="Reviewer", model=..., sys_prompt="你是一个严谨的校对员，检查语法和用词")
formatter = ReActAgent(name="Formatter", model=..., sys_prompt="你是一个格式化专家，确保输出格式美观")
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么每个Agent都有不同的sys_prompt？ | 决定每个Agent的"角色"和"能力" |
| sys_prompt和Agent的name是什么关系？ | name是标识，sys_prompt是定义行为 |
| 如果都用相同的sys_prompt会怎样？ | Agent之间没有分工，都做一样的事 |

```
┌─────────────────────────────────────────────────────────────┐
│           每个Agent都有独特角色                             │
│                                                             │
│   translator  ──► "你是一个专业翻译员..."                 │
│   reviewer    ──► "你是一个严谨的校对员..."              │
│   formatter   ──► "你是一个格式化专家..."                │
│                                                             │
│   类比：团队中不同岗位的职责描述                          │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：在多Agent系统中，**角色分工**是关键。每个Agent通过`sys_prompt`定义自己的职责，就像团队中的岗位描述。

---

### 代码段2：SequentialPipeline的数据流

```python showLineNumbers
# 这是第29-36行
translation_pipeline = SequentialPipeline([
    translator,   # 第一步
    reviewer,     # 第二步
    formatter     # 第三步
])

result = await translation_pipeline("请把这段中文翻译成英文")
```

**思路说明**：

```
┌─────────────────────────────────────────────────────────────┐
│           SequentialPipeline 数据流动                       │
│                                                             │
│  用户输入                                                    │
│  "请把这段中文翻译成英文"                                    │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Translator Agent                                     │  │
│  │ 输入："请把这段中文翻译成英文"                        │  │
│  │ 输出："Please translate this Chinese to English"     │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Reviewer Agent                                      │  │
│  │ 输入："Please translate this..."                     │  │
│  │ 输出："Please translate this Chinese to English."   │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ Formatter Agent                                     │  │
│  │ 输入："Please translate..."                         │  │
│  │ 输出："【翻译结果】\nPlease translate..."           │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  最终输出                                                  │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：SequentialPipeline的核心是**管道式数据流**。每个Agent的输出直接成为下一个Agent的输入，无需额外处理。

---

### 代码段3：为什么需要FanoutPipeline？

```python showLineNumbers
# 头脑风暴场景
brainstorm_pipeline = FanoutPipeline([
    economist,   # 经济专家
    lawyer,     # 法律专家
    tech_expert # 技术专家
])

results = await brainstorm_pipeline("这个创业项目值得投资吗？")
# results = [经济分析, 法律分析, 技术分析]
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么需要Fanout？ | 一个问题需要多角度分析 |
| 输出是什么？ | 所有Agent回复的列表 |
| 谁来汇总？ | 需要另外的Agent或人工 |

```
┌─────────────────────────────────────────────────────────────┐
│            FanoutPipeline 并行协作                         │
│                                                             │
│                        输入                                 │
│              "这个创业项目值得投资吗？"                      │
│                   ┌─────┼─────┐                            │
│                   ▼     ▼     ▼                            │
│            ┌────────┬────────┬────────┐                   │
│            │经济专家│法律专家│技术专家│                   │
│            │ 分析... │ 分析... │ 分析... │                   │
│            └────────┴────────┴────────┘                   │
│                   │     │     │                            │
│                   └─────┼─────┘                            │
│                         ▼                                  │
│                   [经济分析, 法律分析, 技术分析]             │
│                         │                                  │
│                         ▼                                  │
│                   需要人工或Agent汇总                       │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：FanoutPipeline解决**多视角问题**。一个问题需要多个专家同时给出意见，而不是顺序等待。

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
