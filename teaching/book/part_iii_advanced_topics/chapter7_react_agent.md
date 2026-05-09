# 第7章 ReActAgent工作原理

> **目标**：理解ReActAgent的Reasoning+Acting循环

---

## 🎯 学习目标

学完之后，你能：
- 理解ReAct范式的核心思想
- 掌握ReActAgent的工作流程
- 理解reasoning和acting的交替
- 调试Agent的思考过程

---

## 🚀 先跑起来

```python
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter

agent = ReActAgent(
    name="Assistant",
    model=model,
    sys_prompt="你是一个有帮助的助手",
    formatter=OpenAIChatFormatter()
)

# Agent自动执行ReAct循环
response = await agent(Msg(name="user", content="你好"))
print(response.content)
```

---

## 🔍 ReAct范式

### 什么是ReAct

**ReAct = Reasoning + Acting**

```
用户输入 → 思考(Reasoning) → 行动(Acting) → 观察(Observation) → ...
```

**核心思想**：
- Agent不是直接生成回复
- 而是先思考要做什么，再执行动作
- 根据动作结果继续思考

### ReAct vs 普通Agent

**普通Agent**：
```
输入 → 直接生成回复
```

**ReActAgent**：
```
输入 → 思考 → 工具调用 → 观察结果 → 思考 → 回复
```

---

## 🔬 工作流程

```python
async def reply(self, msg: Msg) -> Msg:
    # 1. 思考：分析用户消息，决定策略
    thought = await self._reasoning(msg)
    
    # 2. 行动：根据思考执行动作
    if needs_tool:
        action_result = await self._acting(thought)
    else:
        action_result = None
    
    # 3. 生成回复
    response = await self._generate_response(thought, action_result)
    return response
```

### 步骤详解

**1. Reasoning（思考）**
```python
async def _reasoning(self, msg: Msg) -> str:
    # 构建提示词，包含历史对话
    prompt = self._build_reasoning_prompt(msg)
    # 调用模型生成思考过程
    thought = await self.model(prompt)
    return thought
```

**2. Acting（行动）**
```python
async def _acting(self, thought: str) -> Any:
    # 解析思考，决定调用哪个工具
    tool_call = self._parse_tool_call(thought)
    if tool_call:
        # 执行工具调用
        result = await self.toolkit.call(tool_call.name, tool_call.args)
        return result
    return None
```

**3. 生成响应**
```python
async def _generate_response(self, thought: str, action_result: Any) -> Msg:
    # 最终生成用户可见的回复
    final_prompt = f"思考: {thought}\n结果: {action_result}"
    response = await self.model(final_prompt)
    return Msg(name=self.name, content=response, role="assistant")
```

---

## 💡 Java开发者注意

ReAct循环类似Java的策略模式+命令模式：

```python
# Python ReAct
thought = reasoner.analyze(input)  # 策略
if needs_action:
    result = executor.execute(thought)  # 命令
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **为什么需要ReAct而不是直接回复？**
   - 处理复杂任务时需要推理过程
   - 工具调用需要"思考"来决定用什么
   - 透明化Agent决策过程

2. **reasoning和acting如何交替？**
   - 按轮次交替，可能多轮
   - 每轮根据观察结果决定下一步

</details>

---

★ **Insight** ─────────────────────────────────────
- **ReAct = Reasoning + Acting + Observation**
- 思考决定做什么，行动执行操作
- 观察结果影响下一步思考
- 多轮循环直到完成任务
─────────────────────────────────────────────────
