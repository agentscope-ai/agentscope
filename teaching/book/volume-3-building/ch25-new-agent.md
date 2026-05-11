# 第 25 章 造一个新 Agent

> 本章你将：继承 `AgentBase` 或 `ReActAgentBase`，创建自定义 Agent。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 25.1 选择继承层级

| 需求 | 继承 |
|------|------|
| 完全自定义逻辑 | `AgentBase` |
| 需要推理-行动分离 | `ReActAgentBase` |
| 微调 ReAct 行为 | `ReActAgent` |

---

## 25.2 最简 Agent

```python
from agentscope.agent import AgentBase
from agentscope.message import Msg

class EchoAgent(AgentBase):
    """原样返回用户消息的 Agent"""
    name: str

    async def reply(self, msg=None):
        content = msg.content if msg else ""
        return Msg(self.name, f"你说: {content}", "assistant")

    async def observe(self, msg):
        print(f"[{self.name}] 收到: {msg.content}")

agent = EchoAgent(name="echo")
result = await agent(Msg("user", "你好", "user"))
print(result.content)  # "你说: 你好"
```

---

## 25.3 带工具的 Agent

继承 `ReActAgent`，添加自定义行为：

```python
from agentscope.agent import ReActAgent

class WeatherAgent(ReActAgent):
    """专门查天气的 Agent"""

    async def reply(self, msg=None):
        # 在调用父类之前做预处理
        if msg and "天气" not in msg.content:
            return Msg(self.name, "我只能查天气哦", "assistant")

        # 调用标准 ReAct 流程
        return await super().reply(msg)
```

---

## 25.4 试一试

1. 创建一个"翻译 Agent"（只做翻译）
2. 创建一个"代码审查 Agent"（分析代码并给出建议）
3. 给自定义 Agent 添加 Hook

---

## 25.5 检查点

你现在已经能：

- 继承 `AgentBase` 创建简单 Agent
- 继承 `ReActAgent` 创建带工具的 Agent
- 覆盖 `reply()` 自定义行为

---

## 下一章预告
