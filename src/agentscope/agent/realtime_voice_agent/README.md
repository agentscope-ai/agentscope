# Live Voice Agent 设计文档

本模块提供了基于回调模式的实时语音 Agent 架构，支持多 Agent 间的语音通信和协作。

## 目录

- [设计思想](#设计思想)
- [核心架构](#核心架构)
- [事件系统](#事件系统)
- [组件详解](#组件详解)
- [数据流](#数据流)
- [使用示例](#使用示例)
- [与 realtime_voice_agent 的区别](#与-realtime_voice_agent-的区别)

---

## 设计思想

### 1. 回调模式 vs 迭代器模式

传统实现使用迭代器模式（`async for event in model.iter_events()`），需要在 Agent 中启动独立的监听任务。新架构采用**回调模式**：

```python
# 模式：回调
def _on_model_event(self, event: ModelEvent):
    agent_event = self._convert(event)
    self._queue_stream.put_nowait(agent_event)
```

**优势**：
- 简化代码结构，减少 Task 管理
- 事件处理更直接，延迟更低
- 更容易进行单元测试

### 2. 两层事件系统

将事件分为两层：

| 层级 | 名称 | 产生者 | 消费者 | 用途 |
|------|------|--------|--------|------|
| Model 层 | `ModelEvent` | 模型 WebSocket | Agent | API 原始事件 |
| Agent 层 | `AgentEvent` | Agent | MsgStream/前端 | 统一分发格式 |

这种分层设计：
- **解耦**：Model 层不需要知道 Agent 的存在
- **统一**：不同模型（DashScope/Gemini/OpenAI）产出相同格式的 AgentEvent
- **扩展**：AgentEvent 包含 `agent_id` 和 `agent_name`，支持多 Agent 场景

### 3. 中心化队列 + 分发循环

移除了 `VoiceMsgHub`，改用更简洁的 `EventMsgStream`：

```
Agent A ─┐
         ├──→ Central Queue ──→ dispatch_loop ──┬──→ Agent A.incoming
Agent B ─┘                                      ├──→ Agent B.incoming
                                                └──→ External callback
```

**设计原则**：
- 单一中心队列，避免多队列同步问题
- 分发循环自动过滤，Agent 不会收到自己的事件
- 外部回调支持 WebSocket 转发

---

## 核心架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          EventMsgStream                                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      Central Queue                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                │                                        │
│                         dispatch_loop                                   │
│                                │                                        │
│           ┌────────────────────┼────────────────────┐                  │
│           ▼                    ▼                    ▼                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐        │
│  │ Agent A         │  │ Agent B         │  │ on_event        │        │
│  │ .incoming_queue │  │ .incoming_queue │  │ (WebSocket)     │        │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────┘        │
│           │                    │                                        │
└───────────┼────────────────────┼────────────────────────────────────────┘
            │                    │
            ▼                    ▼
┌───────────────────┐  ┌───────────────────┐
│ RealtimeVoiceAgent│  │ RealtimeVoiceAgent│
│ ┌───────────────┐ │  │ ┌───────────────┐ │
│ │ callback      │ │  │ │ callback      │ │
│ │ (ModelEvent)  │ │  │ │ (ModelEvent)  │ │
│ └───────┬───────┘ │  │ └───────┬───────┘ │
│         │         │  │         │         │
│         ▼         │  │         ▼         │
│ ┌───────────────┐ │  │ ┌───────────────┐ │
│ │ Model         │ │  │ │ Model         │ │
│ │ (WebSocket)   │ │  │ │ (WebSocket)   │ │
│ └───────────────┘ │  │ └───────────────┘ │
└───────────────────┘  └───────────────────┘
         │                      │
         ▼                      ▼
   DashScope API          DashScope API
```

### 组件关系

```
EventMsgStream
    ├── agents: List[RealtimeVoiceAgent]
    ├── _queue: asyncio.Queue[AgentEvent]
    ├── _dispatch_task: asyncio.Task
    └── on_event: Callable[[AgentEvent], None]

RealtimeVoiceAgent
    ├── model: RealtimeVoiceModelBase
    ├── incoming_queue: asyncio.Queue[AgentEvent]
    ├── _queue_stream: asyncio.Queue[AgentEvent]  (reference to MsgStream's queue)
    └── _on_model_event: callback registered with model

RealtimeVoiceModelBase
    ├── _websocket: ClientConnection
    ├── agent_callback: Callable[[ModelEvent], None]
    └── _emit_event(event): calls agent_callback
```

---

## 事件系统

### ModelEvent（Model 层事件）

从模型 API 接收的原始事件，由 Model 产生，Agent 消费。

```python
# 事件类型
class ModelEventType(str, Enum):
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    RESPONSE_CREATED = "response_created"
    RESPONSE_AUDIO_DELTA = "response_audio_delta"
    RESPONSE_AUDIO_DONE = "response_audio_done"
    RESPONSE_AUDIO_TRANSCRIPT_DELTA = "response_audio_transcript_delta"
    RESPONSE_AUDIO_TRANSCRIPT_DONE = "response_audio_transcript_done"
    RESPONSE_TOOL_USE_DELTA = "response_tool_use_delta"
    RESPONSE_TOOL_USE_DONE = "response_tool_use_done"
    RESPONSE_DONE = "response_done"
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"
    INPUT_STARTED = "input_started"  # VAD 检测到语音开始
    INPUT_DONE = "input_done"        # VAD 检测到语音结束
    ERROR = "error"
    WEBSOCKET_CONNECT = "websocket_connect"
    WEBSOCKET_DISCONNECT = "websocket_disconnect"

# 示例事件
@dataclass
class ModelResponseAudioDelta(ModelEvent):
    response_id: str
    delta: str  # base64 编码的音频数据
    item_id: Optional[str] = None
    content_index: Optional[int] = None
    output_index: Optional[int] = None
```

### AgentEvent（Agent 层事件）

Agent 产出的事件，用于分发到其他 Agent 和前端。

```python
# 事件类型
class AgentEventType(str, Enum):
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    RESPONSE_CREATED = "response_created"
    RESPONSE_DELTA = "response_delta"  # 统一的响应增量事件
    RESPONSE_DONE = "response_done"
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"
    INPUT_STARTED = "input_started"
    INPUT_DONE = "input_done"
    ERROR = "error"

# 基类包含 agent 标识
@dataclass
class AgentEvent:
    type: AgentEventType
    agent_id: str      # Agent 唯一标识
    agent_name: str    # Agent 名称

# 响应增量事件使用 ContentBlock
@dataclass
class AgentResponseDelta(AgentEvent):
    response_id: str
    delta: ContentBlock  # TextBlock | AudioBlock | ToolUseBlock | ToolResultBlock
```

### ContentBlock（内容块）

```python
@dataclass
class TextBlock:
    text: str
    type: str = "text"

@dataclass
class AudioBlock:
    data: str  # base64 编码的音频
    media_type: str = "audio/pcm;rate=24000"
    type: str = "audio"

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"

@dataclass
class ToolResultBlock:
    id: str
    name: str
    output: str
    type: str = "tool_result"
```

---

## 组件详解

### 1. RealtimeVoiceModelBase

WebSocket 语音模型的基类，使用回调模式发送事件。

**职责**：
- 管理 WebSocket 连接
- 接收并解析 API 消息
- 通过回调发送 ModelEvent

**核心方法**：

```python
class RealtimeVoiceModelBase(ABC):
    # 回调属性
    agent_callback: ModelEventCallback | None = None

    # 启动连接
    async def start(self, **kwargs) -> None:
        # 1. 连接 WebSocket
        # 2. 发送 WEBSOCKET_CONNECT 事件
        # 3. 启动接收循环
        # 4. 发送会话配置

    # 发送音频（非阻塞）
    def send_audio(self, audio_data: bytes, sample_rate: int | None = None) -> None:
        # 预处理 -> Base64 编码 -> 发送

    # 抽象方法（子类实现）
    @abstractmethod
    def _parse_server_message(self, message: str) -> ModelEvent:
        """解析服务器消息为 ModelEvent"""
```

### 2. DashScopeRealtimeModel

DashScope 的具体实现。

**特点**：
- PCM 音频输入（16kHz）
- PCM 音频输出（24kHz）
- 服务端 VAD
- 输入音频转录

**消息解析示例**：

```python
def _parse_server_message(self, message: str) -> ModelEvent:
    msg = json.loads(message)
    event_type = msg.get("type", "")

    if event_type == "response.audio.delta":
        return ModelResponseAudioDelta(
            response_id=self._current_response_id or "",
            delta=msg.get("delta", ""),
            item_id=msg.get("item_id"),
        )
    elif event_type == "input_audio_buffer.speech_started":
        return ModelInputStarted(
            item_id=msg.get("item_id", ""),
            audio_start_ms=msg.get("audio_start_ms", 0),
        )
    # ... 其他事件
```

### 3. RealtimeVoiceAgent

使用回调模式的语音 Agent。

**职责**：
- 注册回调接收 ModelEvent
- 转换 ModelEvent 为 AgentEvent
- 推送到中心队列
- 处理来自其他 Agent 的事件

**核心流程**：

```python
class RealtimeVoiceAgent:
    async def start(self, msgstream_queue: asyncio.Queue[AgentEvent]) -> None:
        # 1. 保存队列引用
        self._queue_stream = msgstream_queue

        # 2. 注册回调
        self.model.agent_callback = self._on_model_event

        # 3. 启动模型
        await self.model.start(instructions=self.sys_prompt)

        # 4. 启动 incoming 处理循环
        self._incoming_task = asyncio.create_task(self._process_incoming_loop())

    def _on_model_event(self, model_event: ModelEvent) -> None:
        # ModelEvent -> AgentEvent
        agent_event = self._convert_model_to_agent_event(model_event)
        if agent_event:
            self._queue_stream.put_nowait(agent_event)

    async def _process_incoming_loop(self) -> None:
        # 处理来自其他 Agent 的事件
        while not self._stop_event.is_set():
            event = await self.incoming_queue.get()
            await self._handle_incoming_event(event)

    async def _handle_incoming_event(self, event: AgentEvent) -> None:
        # 跳过自己的事件
        if event.agent_id == self.id:
            return
        # 提取音频并发送给模型
        if isinstance(event, AgentResponseDelta):
            if isinstance(event.delta, AudioBlock):
                audio_bytes = base64.b64decode(event.delta.data)
                self.model.send_audio(audio_bytes)
```

### 4. EventMsgStream

事件分发中心。

**职责**：
- 管理中心队列
- 运行分发循环
- 管理 Agent 生命周期
- 支持外部事件回调

**核心流程**：

```python
class EventMsgStream:
    async def start(self) -> None:
        # 1. 启动所有 Agent（传入中心队列）
        for agent in self._agents:
            await agent.start(self._queue)

        # 2. 启动分发循环
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            event = await self._queue.get()

            # 分发到所有 Agent（排除发送者）
            for agent in self._agents:
                if event.agent_id != agent.id:
                    agent.incoming_queue.put_nowait(event)

            # 调用外部回调
            if self.on_event:
                self.on_event(event)

    async def stop(self) -> None:
        # 1. 设置停止标志
        # 2. 发送 None 到队列
        # 3. 取消分发任务
        # 4. 关闭所有 Agent
```

---

## 数据流

### 单 Agent 场景

```
用户音频 ──→ WebSocket Server ──→ model.send_audio()
                                        │
                                        ▼
                               DashScope API
                                        │
                                        ▼
                              WebSocket 响应
                                        │
                                        ▼
                         _parse_server_message()
                                        │
                                        ▼
                              ModelEvent
                                        │
                                        ▼
                           agent_callback()
                                        │
                                        ▼
                       _convert_model_to_agent_event()
                                        │
                                        ▼
                              AgentEvent
                                        │
                                        ▼
                           Central Queue
                                        │
                                        ▼
                          dispatch_loop
                                        │
                                        ▼
                            on_event() ──→ WebSocket ──→ 前端
```

### 双 Agent 对话场景

```
                   Agent A                              Agent B
                      │                                     │
DashScope API ←── Model A                           Model B ──→ DashScope API
       │              │                                 │              │
       │              ▼                                 ▼              │
       │      ModelAudioDelta                   ModelAudioDelta       │
       │              │                                 │              │
       │              ▼                                 ▼              │
       │      AgentResponseDelta               AgentResponseDelta     │
       │              │                                 │              │
       │              ▼                                 ▼              │
       │         ┌────────────────────────────────────────┐           │
       │         │          Central Queue                  │           │
       │         └────────────────────────────────────────┘           │
       │                          │                                    │
       │                   dispatch_loop                               │
       │                          │                                    │
       │              ┌───────────┴───────────┐                       │
       │              ▼                       ▼                       │
       │     Agent B.incoming        Agent A.incoming                 │
       │              │                       │                       │
       └──── model.send_audio() ◄────────────┘                       │
                                                                      │
                      model.send_audio() ◄────────────────────────────┘
```

---

## 使用示例

### 基础用法

```python
import asyncio
from agentscope.agent.realtime_voice_agent import (
    RealtimeVoiceAgent,
    DashScopeRealtimeModel,
    EventMsgStream,
)

async def main():
    # 1. 创建模型
    model = DashScopeRealtimeModel(
        api_key="your-api-key",
        model_name="qwen3-omni-flash-realtime",
        voice="Cherry",
    )

    # 2. 创建 Agent
    agent = RealtimeVoiceAgent(
        name="assistant",
        model=model,
        sys_prompt="You are a helpful voice assistant.",
    )

    # 3. 创建 MsgStream
    stream = EventMsgStream(agents=[agent])

    # 4. 注册外部回调（可选）
    def on_event(event):
        print(f"Event: {event.type} from {event.agent_name}")

    stream.on_event = on_event

    # 5. 启动并运行
    async with stream:
        # 发送音频
        agent.model.send_audio(audio_bytes, sample_rate=16000)
        await asyncio.sleep(60)

asyncio.run(main())
```

### WebSocket 服务器集成

```python
from fastapi import FastAPI, WebSocket
from agentscope.agent.realtime_voice_agent import (
    RealtimeVoiceAgent,
    DashScopeRealtimeModel,
    EventMsgStream,
    AgentEvent,
    AgentResponseDelta,
    AudioBlock,
)

app = FastAPI()

@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()

    # 创建组件
    model = DashScopeRealtimeModel(api_key="xxx")
    agent = RealtimeVoiceAgent(name="assistant", model=model)
    stream = EventMsgStream(agents=[agent])

    # 事件转发到 WebSocket
    def on_event(event: AgentEvent):
        asyncio.create_task(websocket.send_json({
            "type": event.type.value,
            "agent_name": event.agent_name,
            # ... 其他字段
        }))

    stream.on_event = on_event
    await stream.start()

    # 接收音频
    while True:
        data = await websocket.receive_json()
        if data["type"] == "audio":
            audio_bytes = base64.b64decode(data["data"])
            agent.model.send_audio(audio_bytes)
```

### 多 Agent 对话

```python
async def multi_agent_chat():
    # 创建两个 Agent
    model1 = DashScopeRealtimeModel(api_key="xxx", voice="Cherry")
    model2 = DashScopeRealtimeModel(api_key="xxx", voice="Serena")

    agent1 = RealtimeVoiceAgent(name="host", model=model1)
    agent2 = RealtimeVoiceAgent(name="guest", model=model2)

    # MsgStream 会自动分发事件
    stream = EventMsgStream(agents=[agent1, agent2])

    async with stream:
        # Agent 1 的音频会自动发送给 Agent 2
        # Agent 2 的音频会自动发送给 Agent 1
        await stream.join()
```
