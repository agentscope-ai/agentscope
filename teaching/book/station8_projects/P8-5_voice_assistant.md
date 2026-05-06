# P8-5 语音对话助手

> **目标**：构建一个能进行语音对话的助手

---

## 📋 需求分析

**我们要做一个**：能听能说的语音助手

**核心功能**：
1. 接收语音输入
2. 转换为文本
3. Agent处理
4. 文本转语音输出

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    语音助手架构                              │
│                                                             │
│  语音输入 ──► STT ──► Agent ──► TTS ──► 语音输出          │
│                                                             │
│  RealtimeAgent = 语音专用Agent                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 💻 完整代码

```python showLineNumbers
# P8-5_voice_assistant.py
import agentscope
from agentscope import RealtimeAgent
from agentscope.model import OpenAIChatModel
from agentscope.realtime import OpenAIVoiceModel

# 1. 初始化
agentscope.init(project="VoiceAssistant")

# 2. 创建语音Agent
agent = RealtimeAgent(
    name="VoiceAssistant",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    voice=OpenAIVoiceModel(api_key="your-key")
)

# 3. 启动语音对话
async def main():
    await agent.start()  # 开始监听语音输入
    # 用户说话，Agent实时回复

asyncio.run(main())
```

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用RealtimeAgent
- 理解了语音Agent的架构
- 完成了语音对话助手
─────────────────────────────────────────────────
