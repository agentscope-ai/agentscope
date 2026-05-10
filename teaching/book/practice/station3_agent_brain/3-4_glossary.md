# 3-4 术语其实很简单

> **目标**：用通俗易懂的话解释Agent相关的术语

---

## 📖 术语其实很简单

### **ReActAgent** = **推理行动Agent**

> "就是一个会**先思考再行动**的机器人"

**说人话**：不是直接回答，而是想清楚→做一下→看看结果→再想→再做到完成

```
ReAct = Rea + Act = Reason + Act = 思考 + 行动
```

---

### **Thought** = **思考**

> "就是Agent在想：**我应该怎么做**"

**说人话**：分析问题，决定下一步做什么

```
Thought例子：
"用户问天气，我需要调用天气API获取北京的气温。
 让我调用 search_weather(city='北京')"
```

---

### **Action** = **行动**

> "就是Agent**调用工具**去获取信息"

**说人话**：调用Tool，执行实际操作

```
Action例子：
调用 search_weather(city="北京")
调用 calculate(2+3)
调用 search_web("什么是AI")
```

---

### **Observation** = **观察**

> "就是Agent**看工具返回了什么**"

**说人话**：获取Tool执行后的结果

```
Observation例子：
天气API返回：{"weather": "晴", "temp": 25}
计算器返回：5
搜索返回：[结果1, 结果2, 结果3]
```

---

### **Hook** = **拦截器**

> "就是**在某个时机插入的钩子**，让你可以做额外的事"

**说人话**：在Agent处理流程的某个点，插入自定义逻辑

```
Hook例子：
pre_reply: 回复前打印日志
post_reply: 回复后记录指标
pre_observe: 观察结果前过滤敏感词
```

---

### **Tool** = **工具**

> "就是Agent的**手脚**，让它能做具体的事"

**说人话**：天气预报、计算器、搜索，都是Tool

```
Tool例子：
search_weather() - 查天气
calculate() - 数学计算
search_web() - 网页搜索
```

---

### **sys_prompt** = **系统提示词**

> "就是给Agent的**角色设定**"

**说人话**：告诉Agent它是谁，应该怎么说话

```python
# sys_prompt例子
sys_prompt="你是一个友好的客服，说话要热情专业。"

# 不同角色的sys_prompt
"你是一个严谨的分析师，说话要有数据依据"
"你是一个幽默的助手，可以开玩笑"
"你是一个翻译，翻译要准确流畅"
```

---

## 📊 Agent处理流程图

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent 处理流程                        │
│                                                             │
│  用户问题 ──► ┌────────────────────────────────────────┐  │
│              │                                        │  │
│              │   1. Thought: 分析问题                   │  │
│              │                                        │  │
│              │   2. 如果需要 → Action: 调用Tool       │  │
│              │                                        │  │
│              │   3. Observation: 获取Tool结果          │  │
│              │                                        │  │
│              │   4. 思考→行动→观察 → 循环直到完成     │  │
│              │                                        │  │
│              │   5. 回复用户                           │  │
│              │                                        │  │
│              └────────────────────────────────────────┘  │
│                              │                            │
│                              ▼                            │
│                         回复用户                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 术语对照表

| AgentScope | 说人话 | Java对照 | 示例 |
|------------|--------|----------|------|
| ReActAgent | 会思考后行动的Agent | StatefulHandler | `ReActAgent(...)` |
| Thought | 思考 | analyze() | 分析问题，决定行动 |
| Action | 行动 | execute() | 调用Tool |
| Observation | 观察 | getResult() | 获取Tool结果 |
| Hook | 拦截器 | Filter/Interceptor | pre_reply/post_reply |
| Tool | 工具 | Service/Util | search_weather() |
| sys_prompt | 角色设定 | @Description | "你是一个客服" |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **ReActAgent和普通Agent的区别？**
   - 普通Agent：直接调用LLM返回结果
   - ReActAgent：Thought→Action→Observation循环

2. **Hook和Tool的区别？**
   - Hook：拦截处理流程，做日志/监控
   - Tool：被Agent调用，完成具体任务

3. **sys_prompt为什么重要？**
   - 决定Agent的行为风格
   - 影响回复的语气、专业度
   - 类似给AI设定角色

</details>

---

★ **Insight** ─────────────────────────────────────
- **ReAct = 思考 + 行动**，Agent通过循环解决问题
- **Tool = Agent的手**，让它能获取外部信息
- **Hook = 拦截器**，在不改变核心逻辑的情况下增强功能
- **sys_prompt = 角色设定**，决定Agent的风格
─────────────────────────────────────────────────
