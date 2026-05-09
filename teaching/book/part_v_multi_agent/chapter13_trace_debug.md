# 第13章 消息追踪与调试

> **目标**：掌握多Agent系统的调试技巧

---

## 🎯 学习目标

学完之后，你能：
- 追踪消息在系统中的流动
- 可视化Agent交互
- 调试多Agent问题
- 性能分析与优化

---

## 🔍 追踪工具

### 消息流追踪

```python
from agentscope.tracing import trace

# 追踪整个流程
async with trace("multi_agent_process"):
    result = await multi_agent_pipeline(input)
    # 打印完整的消息流
```

### 可视化

```
User → MsgHub → [Agent1, Agent2] → Response
  │           ↕ broadcast
  └─────────────────────────────────────
```

---

★ **Insight** ─────────────────────────────────────
- **追踪** = 看清消息在系统中的完整旅程
- **trace** = 记录每个节点的输入输出
- **可视化** = 更容易发现瓶颈和问题
─────────────────────────────────────────────────
