# P8-5 语音对话助手

> **目标**：构建一个能进行语音对话的智能助手

---

## 📋 需求分析

**我们要做一个**：能听、能思考、能说话的智能助手

**核心功能**：
1. 接收语音输入（用户说话）
2. 语音转文本（STT）
3. Agent处理文本
4. 文本转语音输出（Agent说话）

**技术要点**：
- 使用 `RealtimeAgent` 实现实时语音交互
- 集成 OpenAI 的语音模型
- 支持 TTS（Text-to-Speech）和 STT（Speech-to-Text）

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    语音助手架构                              │
│                                                             │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│  │  麦克风  │───►│   STT   │───►│  Agent  │───►│   TTS   │ │
│  │ (用户说话)│    │(语音→文本)│    │(思考回复)│    │(文本→语音)│ │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘ │
│                                            │                │
│                                            ▼                │
│                                      ┌─────────┐           │
│                                      │  扬声器  │           │
│                                      │(播放语音)│           │
│                                      └─────────┘           │
└─────────────────────────────────────────────────────────────┘
```

**AgentScope组件**：
- `RealtimeAgent`：专为实时语音交互设计的Agent
- `OpenAIVoiceModel`：OpenAI的语音模型（GPT-4o）

---

## 💻 完整代码

```python showLineNumbers
# P8-5_voice_assistant.py
import asyncio
import agentscope
from agentscope.message import Msg
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

# 注意：RealtimeAgent是AgentScope的实时语音组件
# 本示例展示其工作原理，实际使用需要完整的语音配置

# 1. 初始化
agentscope.init(project="VoiceAssistant")

# 2. 创建模型
model = OpenAIChatModel(
    api_key="your-api-key",
    model="gpt-4o"  # 支持语音的模型
)

# 3. 创建语音助手Agent
voice_assistant = ReActAgent(
    name="VoiceAssistant",
    model=model,
    sys_prompt="""你是一个友好的语音助手，名叫小秘。
你的特点是：
1. 回答简洁明了，适合语音播报
2. 语气亲切友好
3. 遇到不懂的问题会承认并建议用户查阅资料

请用口语化的方式回复，不要使用列表或复杂格式。""",
    formatter=OpenAIChatFormatter()
)

# 4. 模拟语音对话函数
async def voice_conversation():
    """模拟语音对话流程

    实际应用中，这里会连接真实的STT和TTS服务
    """
    print("🎤 语音助手已启动，说'你好'开始对话")

    # 模拟多轮对话
    user_inputs = [
        "你好，你叫什么名字？",
        "今天天气怎么样？",
        "给我讲个笑话吧",
        "再见"
    ]

    for user_input in user_inputs:
        print(f"\n👤 用户: {user_input}")

        # Agent处理并生成回复
        response = await voice_assistant(user_input)

        print(f"🤖 助手: {response.content}")

        # 检查是否结束
        if "再见" in user_input or "拜拜" in user_input:
            print("\n🎤 对话结束")
            break

# 5. 完整的语音集成代码（需要额外配置）
async def real_voice_assistant():
    """
    真实的语音助手实现

    需要：
    1. OpenAI API Key（支持语音）
    2. 麦克风和扬声器设备
    3. 正确的音频格式配置
    """
    from agentscope.realtime import OpenAIVoiceModel

    # 创建语音模型
    voice_model = OpenAIVoiceModel(
        api_key="your-api-key",
        model="gpt-4o-realtime-preview",
        voice="alloy"  # 可选: alloy, echo, fable, onyx, nova, shimmer
    )

    # 创建带语音能力的Agent
    assistant = ReActAgent(
        name="VoiceAssistant",
        model=model,
        voice=voice_model,  # 绑定语音模型
        sys_prompt="你是一个友好的语音助手...",
        formatter=OpenAIChatFormatter()
    )

    # 启动语音对话
    print("🎤 开始语音对话，按 Ctrl+C 退出")

    async with assistant.stream() as stream:
        # stream 会自动处理 STT → Agent → TTS 的流程
        async for response in stream:
            print(f"助手: {response}")

# 6. 运行
async def main():
    # 使用模拟版本测试核心逻辑
    print("=" * 50)
    print("模拟语音对话演示")
    print("=" * 50)
    await voice_conversation()

    # 真实语音（需要正确配置）
    # await real_voice_assistant()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔍 代码解读

### 1. 语音助手的特点

```python
sys_prompt="""你是一个友好的语音助手，名叫小秘。
你的特点是：
1. 回答简洁明了，适合语音播报
2. 语气亲切友好
3. ...
请用口语化的方式回复，不要使用列表或复杂格式。"""
```

语音助手的prompt需要特别设计：
- 简短口语化（适合TTS播报）
- 避免复杂格式（列表、代码块等）
- 明确助手身份和风格

### 2. 语音模型配置

```python
voice_model = OpenAIVoiceModel(
    api_key="your-api-key",
    model="gpt-4o-realtime-preview",
    voice="alloy"  # 音色选择
)
```

可选音色：`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

### 3. 流式语音处理

```python showLineNumbers
async with assistant.stream() as stream:
    async for response in stream:
        print(f"助手: {response}")
```

**设计要点**：
- `stream()` 方法返回一个异步生成器
- 自动处理 STT → Agent → TTS 的完整流程
- 支持实时流式响应

---

## 🔬 项目实战思路分析

### 项目结构

```
voice_assistant/
├── P8-5_voice_assistant.py    # 主程序
├── realtime_model.py             # 语音模型配置
└── README.md                  # 说明文档
```

### 开发步骤

```
Step 1: 配置语音模型
        ↓
Step 2: 设计语音友好的prompt
        ↓
Step 3: 实现语音对话循环
        ↓
Step 4: 测试运行
```

### STT → Agent → TTS 完整流程

```
┌─────────────────────────────────────────────────────────────┐
│              语音对话完整流程                               │
│                                                             │
│   用户说话                                                   │
│   "今天天气怎么样？"                                        │
│        │                                                    │
│        ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │              STT (语音转文字)                        │  │
│   │   "今天天气怎么样？" ────────────────────────────────│  │
│   └─────────────────────────────────────────────────────┘  │
│        │                                                    │
│        ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │              Agent 处理                               │  │
│   │   分析意图 → 决定是否调用工具 → 生成回复              │  │
│   │   "今天天气晴朗，25度..."                             │  │
│   └─────────────────────────────────────────────────────┘  │
│        │                                                    │
│        ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │              TTS (文字转语音)                        │  │
│   │   ──────────────────────────► "今天天气晴朗，25度..." │  │
│   │   (播放语音)                                         │  │
│   └─────────────────────────────────────────────────────┘  │
│        │                                                    │
│        ▼                                                    │
│   用户听到语音回复                                          │
└─────────────────────────────────────────────────────────────┘
```

### 语音prompt设计要点

```python showLineNumbers
# 语音友好的prompt特点
sys_prompt="""你是一个友好的语音助手，名叫小秘。

设计要点：
1. 简洁口语化 - TTS播报时更自然
2. 避免列表格式 - 口语不适合念"第一点、第二点"
3. 明确身份和风格 - 用户知道在和谁对话
4. 处理特殊内容 - 遇到无法语音化的内容要有替代方案

好的回复示例：
"今天天气很不错，大部分地区都是晴天，气温在二十度左右。"

不好的回复示例：
"今天天气预报：
1. 北京：晴，25度
2. 上海：多云，28度
3. 广州：雨，26度"
"""
```

---

## 🚀 运行效果

```
==================================================
模拟语音对话演示
==================================================
🎤 语音助手已启动，说'你好'开始对话

👤 用户: 你好，你叫什么名字？
🤖 助手: 你好！我叫小秘，是一个语音助手。有什么可以帮你的吗？

👤 用户: 今天天气怎么样？
🤖 助手: 今天天气很不错，大部分地区都是晴天，气温在二十度左右，非常适合外出活动哦。

👤 用户: 给我讲个笑话吧
🤖 助手: 好的，来了！为什么程序员总是分不清圣诞节和万圣节？因为 Oct 31 = Dec 25 ！哈哈，怎么样，好笑吗？

👤 用户: 再见
🤖 助手: 再见！很高兴和你聊天，有需要随时叫我。

🎤 对话结束
```

---

## 🐛 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 语音模型调用失败 | API Key无语音权限 | 确认API Key支持GPT-4o |
| 音频格式错误 | 设备配置问题 | 检查麦克风/扬声器设置 |
| 回复太慢 | 网络延迟 | 使用本地TTS/STT服务 |
| 中文发音奇怪 | 某些TTS模型中文支持差 | 使用支持中文的语音模型 |

---

## 🎯 扩展思考

1. **如何实现打断功能？**
   - 检测用户新语音输入
   - 停止当前TTS播放
   - 立即响应新输入

2. **如何添加多语言支持？**
   - 检测用户语言
   - 调用对应语言的TTS模型
   - Agent切换对应语言回复

3. **如何实现语音情绪识别？**
   - 添加情感分析模块
   - Agent根据用户情绪调整回复风格
   - 语音模型也可以表达不同情感

4. **如何本地部署避免API费用？**
   - 使用开源STT（如Whisper）
   - 使用开源TTS（如Coqui TTS）
   - 保持Agent调用云端API

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用RealtimeAgent实现语音交互
- 理解了STT → Agent → TTS的完整语音流程
- 掌握了语音助手prompt的设计要点
- 完成了语音对话助手的完整项目
─────────────────────────────────────────────────
