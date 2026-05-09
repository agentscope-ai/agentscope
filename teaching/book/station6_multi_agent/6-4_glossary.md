# 6-4 术语其实很简单

> **目标**：用通俗易懂的话解释多Agent相关的术语

---

## 📖 术语其实很简单

### **SequentialPipeline** = **顺序流水线**

> "就像工厂的流水线，一个接一个"

**说人话**：上一个的输出是下一个的输入

```
原材料 → 工人A → 工人B → 工人C → 成品
  Msg  → AgentA → AgentB → AgentC → 新Msg
```

---

### **FanoutPipeline** = **扇出流水线**

> "就像分叉的河流，一头分多头"

**说人话**：一个输入，同时给多个Agent处理

```
        ┌─► Agent B
输入 ───┤
        ├─► Agent C
        └─► Agent D
```

---

### **MsgHub** = **消息中心**

> "就像邮局，订报的人都会收到"

**说人话**：发布消息，所有订阅者都能收到

```
发件人 → 邮局 → 订户A
             → 订户B
             → 订户C
```

---

### **subscribe** = **订阅**

> "就是**报名**参加"

**说人话**：告诉MsgHub"有消息记得发给我"

```python
# 使用with语法自动管理订阅
async with MsgHub(participants=[agent_a, agent_b]) as hub:
    await hub.broadcast(Msg(content="有新消息"))
```

---

### **broadcast** = **广播**

> "就是**发布**消息给所有订阅者"

**说人话**：把消息投进MsgHub，所有订阅者都会收到

```python
await hub.broadcast(Msg(content="有新消息"))
```

---

## 📊 多Agent协作模式对比

| 模式 | 关系 | 输入输出 | 适用场景 |
|------|------|----------|----------|
| SequentialPipeline | 顺序依赖 | 上一输出→下一输入 | 流水线任务 |
| FanoutPipeline | 并行独立 | 一个输入→多个输出 | 多角度分析 |
| MsgHub | 发布订阅 | 一个发布→多个接收 | 事件通知 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Pipeline和MsgHub的核心区别？**
   - Pipeline：硬编码顺序，上一输出是下一输入
   - MsgHub：动态订阅，所有订阅者收到相同消息

2. **什么场景用MsgHub？**
   - 事件通知
   - 日志记录
   - 多方协作但不关心顺序

</details>

---

★ **Insight** ─────────────────────────────────────
- **Pipeline = 流水线**，有顺序依赖
- **MsgHub = 广播站**，松耦合协作
- 选择取决于任务是否需要顺序
─────────────────────────────────────────────────
