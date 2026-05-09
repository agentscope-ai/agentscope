# 项目3：多Agent辩论系统

> **难度**：⭐⭐⭐（高级）
> **预计时间**：6小时

---

## 🎯 学习目标

- 使用MsgHub协调多Agent
- 实现Agent间消息传递
- 复杂对话流程控制

---

## 1. 需求分析

### 功能需求

- 正方和反方Agent辩论
- 主持人Agent总结
- 多轮辩论

```
主持人: 辩题：AI是否应该广泛应用
正方: AI可以提高效率...
反方: 但会带来就业问题...
[多轮辩论]
主持人: 总结双方观点...
```

---

## 2. 系统设计

```
┌─────────────────────────────────────────┐
│              MsgHub                      │
│  participants=[pro, con, host]         │
└─────────────────┬───────────────────────┘
                   │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ 正方   │ │ 反方   │ │ 主持人 │
   │ Agent  │ │ Agent  │ │ Agent  │
   └────────┘ └────────┘ └────────┘
```

---

## 3. 核心代码

```python
from agentscope.pipeline import MsgHub
from agentscope.message import Msg

# 创建辩手Agent
pro_agent = ReActAgent(
    name="正方",
    model=model,
    sys_prompt="你是正方辩手，坚持AI利大于弊"
)

con_agent = ReActAgent(
    name="反方",
    model=model,
    sys_prompt="你是反方辩手，认为AI需要更多限制"
)

host = ReActAgent(
    name="主持人",
    model=model,
    sys_prompt="你是主持人，总结辩论"
)

# 辩论流程
async def debate(topic: str, rounds: int = 3):
    async with MsgHub(participants=[pro_agent, con_agent]) as hub:
        # 广播辩题
        await hub.broadcast(Msg(
            name="host",
            content=f"辩题：{topic}",
            role="system"
        ))
        
        # 多轮辩论
        for round in range(rounds):
            pro_response = await pro_agent(f"请就'{topic}'发表正方观点")
            con_response = await con_agent(f"请就'{topic}'发表反方观点")
    
    # 主持人总结
    summary = await host(f"总结辩论：正方={pro_response}, 反方={con_response}")
    return summary
```

---

## 4. 扩展思考

1. **如何让Agent听到对方观点？**
   - 通过MsgHub广播给对方
   - 每次广播后对方observe

2. **如何计分评判？**
   - 添加评判Agent
   - 收集双方论点后评判

---

★ **Insight** ─────────────────────────────────────
- **MsgHub** = 广播给所有参与者
- **多Agent协作** = 共享消息中枢
- **顺序交流** = 通过broadcast传递观点
─────────────────────────────────────────────────
