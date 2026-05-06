# P8-3 多Agent辩论系统

> **目标**：构建一个多Agent辩论系统

---

## 📋 需求分析

**我们要做一个**：让正反两方Agent针对话题进行辩论

**核心功能**：
1. 主持人发布辩题
2. 正方和反方同时发表观点
3. 汇总辩论结果

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    多Agent辩论架构                           │
│                                                             │
│  主持人 ──► MsgHub.publish() ──► 正方Agent                 │
│                                  ──► 反方Agent              │
│                                  ──► 听众Agent              │
└─────────────────────────────────────────────────────────────┘
```

---

## 💻 完整代码

```python showLineNumbers
# P8-3_multi_agent_debate.py
import agentscope
from agentscope import Agent
from agentscope.model import OpenAIChatModel
from agentscope.pipeline import MsgHub

# 1. 初始化
agentscope.init(project="Debate")

# 2. 创建辩论Agent
pro_agent = Agent(
    name="ProSide",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    sys_prompt="你是正方辩手。请针对辩题发表有利观点。"
)

con_agent = Agent(
    name="ConSide",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    sys_prompt="你是反方辩手。请针对辩题发表有利观点。"
)

# 3. 创建消息中枢
hub = MsgHub([pro_agent, con_agent])

# 4. 辩论
import asyncio

async def debate(topic: str):
    # 发布辩题
    await hub.publish(Agent(
        name="Host",
        model=None,
        sys_prompt=""
    ), f"辩题：{topic}")
    
    # 收集双方观点
    pro_view = await pro_agent(f"请发表正方观点：{topic}")
    con_view = await con_agent(f"请发表反方观点：{topic}")
    
    return {"pro": pro_view, "con": con_view}

asyncio.run(debate("AI是否会取代人类工作？"))
```

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用MsgHub实现多Agent协作
- 理解了发布-订阅模式
- 完成了多Agent辩论系统
─────────────────────────────────────────────────
