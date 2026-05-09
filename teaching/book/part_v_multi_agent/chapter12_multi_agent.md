# 第12章 多Agent协作模式

> **目标**：掌握多Agent系统设计和编排

---

## 🎯 学习目标

学完之后，你能：
- 设计多Agent系统架构
- 使用Pipeline编排Agent
- 使用MsgHub协调Agent
- 处理Agent间通信

---

## 🚀 协作模式

### 1. 流水线模式

```
User → Analyzer → Reviewer → Editor → Response
```

```python
pipeline = SequentialPipeline([
    analyzer,
    reviewer,
    editor
])
result = await pipeline(user_input)
```

### 2. 广播模式

```
User → [Analyst1, Analyst2, Analyst3] → 汇总
```

```python
async with MsgHub(participants=[a1, a2, a3]) as hub:
    await hub.broadcast(Msg(content=topic))
```

### 3. 分层模式

```
                    ┌─ AgentA1
User → Router ──────┼─ AgentB1 → Merger
                    └─ AgentB2
```

---

★ **Insight** ─────────────────────────────────────
- **流水线** = 有序依赖，依次处理
- **广播** = 独立并行，各自处理
- **分层** = 先路由再分发，最后合并
─────────────────────────────────────────────────
