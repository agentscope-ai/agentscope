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
- 使用 `OpenAIRealtimeModel` 作为语音模型
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
- `OpenAIRealtimeModel`：OpenAI的实时语音模型（GPT-4o）

---

## 💻 完整代码

```python showLineNumbers
# P8-5_voice_assistant.py
import asyncio
import agentscope
from agentscope.agent import RealtimeAgent
from agentscope.realtime import OpenAIRealtimeModel, ClientEvents
from agentscope.message import Msg

# 注意：RealtimeAgent是AgentScope的实时语音组件
# 与ReActAgent不同，RealtimeAgent专为语音场景设计

# 1. 初始化
agentscope.init(project="VoiceAssistant")

# 2. 创建语音模型（OpenAI的实时语音模型）
voice_model = OpenAIRealtimeModel(
    api_key="your-api-key",
    model="gpt-4o-realtime-preview",
    voice="alloy"  # 可选: alloy, echo, fable, onyx, nova, shimmer
)

# 3. 创建语音助手Agent
assistant = RealtimeAgent(
    name="VoiceAssistant",
    sys_prompt="""你是一个友好的语音助手，名叫小秘。
你的特点是：
1. 回答简洁明了，适合语音播报
2. 语气亲切友好
3. 遇到不懂的问题会承认并建议用户查阅资料

请用口语化的方式回复，不要使用列表或复杂格式。""",
    model=voice_model
)

# 4. 运行语音对话
async def run_voice_assistant():
    """运行语音助手

    RealtimeAgent使用队列来处理输入输出，
    需要在另一个任务中处理来自agent的消息
    """
    # 创建队列用于处理agent的输出
    output_queue = asyncio.Queue()

    # 启动Agent
    await assistant.start(output_queue)

    print("🎤 语音助手已启动，按 Ctrl+C 退出")

    try:
        # 在后台任务中处理agent的输出
        async def process_output():
            while True:
                msg = await output_queue.get()
                print(f"助手: {msg}")

        # 启动输出处理任务
        output_task = asyncio.create_task(process_output())

        # 主循环：模拟用户输入
        # 实际应用中，这里会从麦克风获取音频输入
        user_inputs = [
            "你好，你叫什么名字？",
            "今天天气怎么样？",
            "给我讲个笑话吧",
            "再见"
        ]

        for user_input in user_inputs:
            print(f"\n👤 用户: {user_input}")
            # 将用户输入发送给agent（需要构造ClientEvents对象）
            client_event = ClientEvents.ClientTextInputEvent(text=user_input)
            await assistant.handle_input(client_event)

            if "再见" in user_input or "拜拜" in user_input:
                break

        output_task.cancel()

    finally:
        await assistant.stop()
        print("\n🎤 对话结束")

# 5. 运行
async def main():
    print("=" * 50)
    print("语音助手演示")
    print("=" * 50)
    await run_voice_assistant()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔍 代码解读

### 1. RealtimeAgent vs ReActAgent

```python showLineNumbers
# ReActAgent - 文本Agent，通过工具调用扩展能力
from agentscope.agent import ReActAgent
text_agent = ReActAgent(
    name="TextAssistant",
    model=OpenAIChatModel(...),
    sys_prompt="你是一个助手"
)

# RealtimeAgent - 语音Agent，专为实时语音交互设计
from agentscope.agent import RealtimeAgent
from agentscope.realtime import OpenAIRealtimeModel
voice_agent = RealtimeAgent(
    name="VoiceAssistant",
    model=OpenAIRealtimeModel(...),  # 使用语音模型
    sys_prompt="你是一个语音助手"
)
```

**关键区别**：
| 特性 | ReActAgent | RealtimeAgent |
|------|------------|---------------|
| 用途 | 文本对话、工具调用 | 实时语音交互 |
| 模型 | ChatModel | RealtimeModel |
| 接口 | `await agent(msg)` | `await agent.start()` / `handle_input()` |
| 适用场景 | 客服、问答 | 语音助手、实时对话 |

### 2. 语音模型配置

```python showLineNumbers
voice_model = OpenAIRealtimeModel(
    api_key="your-api-key",
    model="gpt-4o-realtime-preview",
    voice="alloy"  # 音色选择
)
```

**可选音色**：`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`

### 3. 生命周期管理

```python showLineNumbers
# 启动agent
await assistant.start(output_queue)

# 发送输入
client_event = ClientEvents.ClientTextInputEvent(text="你好")
await assistant.handle_input(client_event)

# 停止agent
await assistant.stop()
```

**设计要点**：
- `start(queue)` - 启动agent，传入队列用于接收输出
- `handle_input(event)` - 发送用户输入（需要构造ClientEvents对象）
- `stop()` - 停止agent

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
Step 2: 创建RealtimeAgent
        ↓
Step 3: 实现输入输出循环
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
│   │              RealtimeAgent 处理                       │  │
│   │   分析意图 → 生成回复                                │  │
│   │   "今天天气晴朗，25度..."                           │  │
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
语音助手演示
==================================================
🎤 语音助手已启动，按 Ctrl+C 退出

👤 用户: 你好，你叫什么名字？
助手: 你好！我叫小秘，是一个语音助手。有什么可以帮你的吗？

👤 用户: 今天天气怎么样？
助手: 今天天气很不错，大部分地区都是晴天，气温在二十度左右，非常适合外出活动哦。

👤 用户: 给我讲个笑话吧
助手: 好的，来了！为什么程序员总是分不清圣诞节和万圣节？因为 Oct 31 = Dec 25 ！哈哈，怎么样，好笑吗？

👤 用户: 再见
助手: 再见！很高兴和你聊天，有需要随时叫我。

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
- 认识了RealtimeAgent与ReActAgent的区别
─────────────────────────────────────────────────

## 💡 Java开发者注意

```python
# Python Agent - RealtimeAgent使用
voice_model = OpenAIRealtimeModel(
    api_key="your-key",
    model="gpt-4o-realtime-preview"
)
assistant = RealtimeAgent(
    name="VoiceAssistant",
    model=voice_model,  # 语音模型作为model参数
    sys_prompt="你是一个友好的语音助手..."
)
# 使用start/stop管理生命周期
await assistant.start(output_queue)
client_event = ClientEvents.ClientTextInputEvent(text="你好")
await assistant.handle_input(client_event)
await assistant.stop()
```

**对比Java**：
| Python AgentScope | Java |
|-------------------|------|
| `RealtimeAgent.start(queue)` | `agent.start()` |
| `RealtimeAgent.handle_input(event)` | `agent.receive(input)` |
| `RealtimeAgent.stop()` | `agent.stop()` |

> 注：Java SDK中的等价格式为`OpenAIVoiceModel`

**RealtimeAgent的Java等价概念**：
```java
// Java: 手动构建语音管道
RealtimeAgent agent = new RealtimeAgent(
    "VoiceAssistant",
    new OpenAIRealtimeModel(apiKey, model),
    systemPrompt
);

BlockingQueue<Message> outputQueue = new LinkedBlockingQueue<>();
agent.start(outputQueue);

ClientTextInputEvent event = new ClientTextInputEvent("你好");
agent.receive(event);
Message response = outputQueue.take();

agent.stop();
```

---

## 🎯 思考题

<details>
<summary>1. RealtimeAgent和ReActAgent有什么区别？什么时候用哪个？</summary>

**答案**：
| 特性 | ReActAgent | RealtimeAgent |
|------|------------|---------------|
| **用途** | 文本对话、工具调用 | 实时语音交互 |
| **模型类型** | ChatModel | RealtimeModel |
| **接口** | `await agent(msg)` | `start()`/`handle_input()`/`stop()` |
| **适用场景** | 客服、问答、RAG | 语音助手、实时对话 |

**选择建议**：
- 需要工具调用 → ReActAgent
- 纯语音交互 → RealtimeAgent
- 需要流式响应 → RealtimeAgent
- 需要文本+工具 → ReActAgent
</details>

<details>
<summary>2. 为什么语音助手的prompt需要特别设计？</summary>

**答案**：
- **输出载体不同**：文本Agent输出给"眼睛看"，语音Agent输出给"耳朵听"
- **格式限制**：语音无法呈现列表、代码块、表格；口语化表达是必须的
- **信息密度**：语音一次播报的信息量有限，需要精简核心内容
- **容错性**：语音不像文本可以回看，需要更清晰的逻辑结构
- **特殊内容处理**：遇到数字、代码、专有名词需要特殊处理（如"API Key"读作"A-P-I Key"）
</details>

<details>
<summary>3. RealtimeAgent的生命周期管理是怎样的？</summary>

**答案**：
RealtimeAgent使用显式的生命周期管理：

1. **`start(queue)`**：启动agent，传入队列用于接收输出消息
2. **`handle_input(event)`**：发送用户输入（需要构造ClientEvents对象）
3. **`stop()`**：停止agent，清理资源

```python
output_queue = asyncio.Queue()
await agent.start(output_queue)

# 主循环处理输入
client_event = ClientEvents.ClientTextInputEvent(text="你好")
await agent.handle_input(client_event)

# 在另一个任务中处理输出
async def handle_output():
    while True:
        msg = await output_queue.get()
        print(f"收到: {msg}")

await agent.stop()
```

**注意**：`start()`和`stop()`必须配对调用，使用`async with`或`try/finally`确保资源释放。
</details>
