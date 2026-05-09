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
from agentscope.realtime import RealtimeAgent
from agentscope.server import WebSocketServer

# 创建实时Agent
agent = RealtimeAgent(
    name="语音助手",
    model=model,
    asr="whisper",  # 语音识别
    tts="tts-3"     # 语音合成
)

# WebSocket服务
server = WebSocketServer(agent=agent)
await server.start()
```

---

★ **Insight** ─────────────────────────────────────
- **RealtimeAgent** = 实时语音交互
- **ASR/TTS** = 语音转文字、文字转语音
- **WebSocket** = 实时双向通信
─────────────────────────────────────────────────
