# 项目5：语音对话助手

> **难度**：⭐⭐⭐⭐⭐（大师）
> **预计时间**：12小时

---

## 🎯 学习目标

- 实时语音交互
- WebSocket通信
- 多模态输入处理

---

## 1. 需求分析

用户通过语音与Agent对话，实时接收语音回复。

```
用户(语音): "帮我查一下天气"
Agent: [语音回复] "北京今天天气晴朗"
```

---

## 2. 系统设计

```
┌────────┐    ┌────────┐    ┌────────┐
│  麦克风 │───→│  ASR   │───→│ Agent  │
└────────┘    └────────┘    └────────┘
                              │
                              ▼
┌────────┐    ┌────────┐    ┌────────┐
│  扬声器 │←───│  TTS   │←───│ Response│
└────────┘    └────────┘    └────────┘
```

---

## 3. 核心代码

```python
from agentscope.agent import RealtimeAgent
from agentscope.realtime import OpenAIRealtimeModel

# 创建实时Agent
model = OpenAIRealtimeModel(
    api_key="your-api-key",
    model="gpt-4o-realtime-preview"
)
agent = RealtimeAgent(
    name="语音助手",
    model=model,
    sys_prompt="你是一个友好的语音助手..."
)

# 使用start/stop管理生命周期
await agent.start(output_queue)

# 发送用户输入（需要构造ClientEvents对象）
from agentscope.realtime import ClientEvents
client_event = ClientEvents.ClientTextInputEvent(text="你好")
await agent.handle_input(client_event)

await agent.stop()
```

> 注：WebSocket服务需要配合FastAPI等Web框架自行搭建，参考 `examples/agent/realtime_voice_agent/run_server.py`

---

★ **Insight** ─────────────────────────────────────
- **RealtimeAgent** = 实时语音交互
- **ASR/TTS** = 语音转文字、文字转语音
- **WebSocket** = 实时双向通信
─────────────────────────────────────────────────
