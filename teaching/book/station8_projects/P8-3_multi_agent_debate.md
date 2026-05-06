# P8-3 多Agent辩论系统

> **目标**：构建一个多Agent辩论系统，让正反两方针对话题进行辩论

---

## 📋 需求分析

**我们要做一个**：能进行多轮辩论的系统

**核心功能**：
1. 主持人发布辩题
2. 正方和反方同时发表观点
3. 根据对方观点进行多轮辩论
4. 主持人汇总辩论结果

**预期效果**：
```
用户: AI是否会取代人类工作？
正方: AI会创造更多就业机会...
反方: AI确实会取代部分工作...
（多轮交锋后）
主持人总结: 双方就AI对就业的影响进行了深入辩论...
```

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    多Agent辩论架构                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                      MsgHub                          │  │
│  │              (消息中枢/发布订阅)                      │  │
│  └─────────────────────────────────────────────────────┘  │
│                         │                                  │
│          ┌─────────────┼─────────────┐                   │
│          ▼             ▼             ▼                    │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐              │
│    │ 正方Agent │ │ 反方Agent │ │ 主持人    │              │
│    └──────────┘ └──────────┘ └──────────┘              │
└─────────────────────────────────────────────────────────────┘
```

**关键组件**：
- `MsgHub`：消息中枢，实现发布订阅模式
- `ReActAgent`：带推理能力的Agent
- 辩论话题通过system prompt注入

---

## 💻 完整代码

```python showLineNumbers
# P8-3_multi_agent_debate.py
import agentscope
from agentscope import Msg
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.pipeline import MsgHub

# 1. 初始化
agentscope.init(project="DebateSystem")

# 2. 创建模型
model = OpenAIChatModel(
    api_key="your-api-key",
    model="gpt-4"
)

# 3. 创建主持人Agent
host = ReActAgent(
    name="Host",
    model=model,
    sys_prompt="""你是一个辩论主持人。你的任务是：
1. 公平地对待正反双方
2. 总结双方的核心观点
3. 给出客观的结论"""
)

# 4. 创建正方Agent
pro_agent = ReActAgent(
    name="ProSide",
    model=model,
    sys_prompt="""你是一个正方辩手，坚持以下立场：人工智能的发展利大于弊。
请针对辩题发表有利观点，并用逻辑和证据支持你的立场。"""
)

# 5. 创建反方Agent
con_agent = ReActAgent(
    name="ConSide",
    model=model,
    sys_prompt="""你是一个反方辩手，坚持以下立场：人工智能的发展需要更多限制。
请针对辩题发表有利观点，并用逻辑和证据支持你的立场。"""
)

# 6. 创建MsgHub
msghub = MsgHub(agents=[pro_agent, con_agent])

# 7. 辩论函数
import asyncio

async def run_debate(topic: str, rounds: int = 3):
    """运行辩论

    Args:
        topic: 辩论话题
        rounds: 辩论轮数
    """
    print(f"开始辩论：{topic}")
    print("=" * 50)

    # 第一轮：开场陈述
    print("\n【第一轮：开场陈述】")

    pro_opening = await pro_agent(f"请就'{topic}'发表正方开场陈述")
    print(f"正方：{pro_opening.content[:100]}...")

    con_opening = await con_agent(f"请就'{topic}'发表反方开场陈述")
    print(f"反方：{con_opening.content[:100]}...")

    # 多轮辩论
    pro_view = pro_opening.content
    con_view = con_opening.content

    for i in range(2, rounds + 1):
        print(f"\n【第{i}轮：自由辩论】")

        # 正方回应反方
        pro_rebuttal = await pro_agent(
            f"反方观点：{con_view}\n\n"
            f"请针对反方观点进行反驳，坚持正方立场。"
        )
        print(f"正方反驳：{pro_rebuttal.content[:100]}...")

        # 反方回应正方
        con_rebuttal = await con_agent(
            f"正方观点：{pro_view}\n\n"
            f"请针对正方观点进行反驳，坚持反方立场。"
        )
        print(f"反方反驳：{con_rebuttal.content[:100]}...")

        pro_view = pro_rebuttal.content
        con_view = con_rebuttal.content

    # 总结
    print("\n【主持人总结】")
    summary = await host(
        f"请总结以下辩论，客观分析双方观点：\n\n"
        f"正方观点：{pro_view}\n\n"
        f"反方观点：{con_view}"
    )
    print(summary.content)

    return {
        "topic": topic,
        "pro": pro_view,
        "con": con_view,
        "summary": summary.content
    }

# 8. 运行
async def main():
    result = await run_debate("AI是否会取代人类工作？")
    return result

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔍 代码解读

### 1. MsgHub创建

```python
msghub = MsgHub(agents=[pro_agent, con_agent])
```

MsgHub将正方和反方Agent注册为订阅者，它们会自动收到发布的消息。

### 2. 异步辩论循环

```python
for i in range(2, rounds + 1):
    pro_rebuttal = await pro_agent(f"反方观点：{con_view}...")
    con_rebuttal = await con_agent(f"正方观点：{pro_view}...")
```

多轮辩论通过异步循环实现，每轮双方互换角色进行回应。

### 3. 主持人总结

```python
summary = await host(
    f"请总结以下辩论，客观分析双方观点：\n\n"
    f"正方观点：{pro_view}\n\n"
    f"反方观点：{con_view}"
)
```

主持人Agent接收双方所有观点，生成客观总结。

---

## 🚀 运行效果

```
开始辩论：AI是否会取代人类工作？
==================================================

【第一轮：开场陈述】
正方：人工智能将创造更多就业机会，特别是在...
反方：虽然AI提高了效率，但确实会导致...

【第二轮：自由辩论】
正方反驳：反方提到的失业问题只是短期现象...
反方反驳：正方忽视了结构性失业的严重性...

【第三轮：自由辩论】
正方反驳：我不同意反方关于AI无法创新的观点...
反方反驳：正方过于乐观，忽视了技术进步的代价...

【主持人总结】
双方就AI对就业的影响进行了深入辩论。正方认为AI会创造新就业...

{
    "topic": "AI是否会取代人类工作？",
    "pro": "正方最终观点...",
    "con": "反方最终观点...",
    "summary": "主持人总结..."
}
```

---

## 🐛 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| Agent回复太长 | 没有限制长度 | 在prompt中加"简短回复" |
| 辩论偏离主题 | Agent过于发散 | 在prompt中强调"紧扣主题" |
| 一方过于强势 | 模型性格差异 | 调整sys_prompt使双方平衡 |

---

## 🎯 扩展思考

1. **如何添加评分机制？**
   - 增加一个Judge Agent专门评分
   - 根据逻辑性、证据充分性等维度评分

2. **如何支持更多参与方？**
   - 增加第三方、第四方Agent
   - 修改MsgHub的agents列表即可

3. **如何持久化辩论记录？**
   - 将结果存入Redis或数据库
   - 添加日志记录每轮内容

4. **如何添加实时观众互动？**
   - 观众通过MsgHub发送问题
   - Agent实时回应观众提问

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用MsgHub协调多个Agent
- 实现了发布订阅模式的多Agent系统
- 理解了辩论系统的设计与实现
- 完成了多Agent协作的完整项目
─────────────────────────────────────────────────
