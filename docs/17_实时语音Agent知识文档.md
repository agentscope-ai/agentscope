# 实时语音 Agent 知识文档

> **版本**：v2.0.8（基于 `upstream/main`，截至 2026-09）
> **v2.0.8 新增**：实时语音 Agent 子系统（PR #2547 / #2556 / #2552 / #2553 / #2550）。此前 AgentScope 仅有文本/多模态消息交互，本版本引入「模型侧实时语音会话 + Agent 侧编排」的完整语音链路，支持 Gemini Live、OpenAI Realtime、xAI Grok Voice、DashScope 实时语音后端。

---

## 一、总览

实时语音（Realtime）把「语音输入 → 模型推理 → 语音输出」压进一个**常驻双向音频流**：用户边说模型边听，模型边想边说，靠 **VAD（语音活动检测）** 与 **turn-taking** 在「用户说话」和「模型说话」之间切换，而不是一轮一轮的请求-响应。

AgentScope 把实时语音拆成两层：

```
┌──────────────────────────────────────────────────────────────┐
│  agent/_realtime/_agent.py  →  RealtimeAgent（编排层）          │
│     · 绑定 RealtimeModelBase + TransportBase + VADBase          │
│     · connect / reply_stream / send / interrupt / close         │
└───────────────────────────┬──────────────────────────────────┘
                            │ 事件 + 音频帧
┌───────────────────────────┴──────────────────────────────────┐
│  realtime/  →  模型侧（音频 I/O + 工具调用）                     │
│     · RealtimeModelBase 子类：Gemini / OpenAI / xAI / DashScope│
│     · RealtimeModelCard（YAML 模型卡）                          │
│     · TransportBase / LocalAudioTransport（音频帧传输）          │
│     · VADBase（打断/轮次切换）                                  │
│     · ModelEvent 系列（音频/文本/工具调用事件）                  │
└──────────────────────────────────────────────────────────────┘
```

**设计要点** 🔴：模型侧只负责「把音频推进去、把音频/事件拉出来」，Agent 侧负责「何时打断、何时把工具结果喂回、何时结束」。两层通过 `TransportBase` 的音频帧与 `ModelEvent` 事件解耦，因此可以本地麦克风（LocalAudioTransport）或任意自定义传输。

---

## 二、模型侧：RealtimeModelBase 与后端

**文件**：`src/agentscope/realtime/_base.py`

```python
class RealtimeModelBase(ABC):
    class Parameters(BaseModel): ...          # 各后端自建参数子类
    async def connect(self) -> None: ...       # 建立 WebSocket 会话
    async def close(self) -> None: ...
    async def events(self) -> AsyncIterator[ModelEvent]: ...  # 模型事件流
    async def push_audio(self, pcm: bytes) -> None: ...      # 上行音频
    async def push_text(self, text: str) -> None: ...
    async def push_tool_result(self, block: ToolResultBlock) -> None: ...
    async def request_response(self) -> None: ...            # 主动触发模型说话
    async def cancel_response(self) -> None: ...
    async def truncate(self, ...) -> None: ...               # 打断后截断已生成音频
```

配套枚举与异常：

```python
class TruncationSupport(StrEnum): ...          # 后端是否支持截断已生成音频
class ModelDisconnectedError(ConnectionError): ...  # 连接断开
class RealtimeModelCard(BaseModel):            # 模型卡（YAML 描述）
    @classmethod
    def from_yaml(cls, path) -> "RealtimeModelCard": ...
    @classmethod
    def list_from_directory(cls, dir) -> list[...]: ...
```

### 2.1 四个后端实现

| 后端 | 类 | 模型卡示例 |
|------|----|-----------|
| Gemini Live | `GeminiRealtimeModel` | `gemini-2.5-flash-native-audio-preview`、`gemini-3.1-flash-live-preview` |
| OpenAI Realtime | `OpenAIRealtimeModel` | `gpt-realtime-1.5 / 2 / 2.1 / 2.1-mini` |
| xAI Grok Voice | `XAIRealtimeModel` | `grok-voice-latest`、`grok-voice-think-fast-2.0` |
| DashScope | `DashScopeRealtimeModel` / `DashScopeAudioRealtimeModel` | `qwen3-omni-flash-realtime`、`qwen-audio-3.0-realtime` 等 |

每个后端都提供 `Parameters` 子类（继承 `RealtimeModelBase.Parameters`）承载各自专属参数（如 voice、temperature、input modalities），并通过 `RealtimeModelCard.from_yaml` 从 `realtime/_*/_models/*.yaml` 加载默认配置。

---

## 三、传输层与 VAD

**文件**：`src/agentscope/realtime/_transport/_base.py`、`_local.py`、`_vad.py`

```python
class TransportBase(ABC): ...                 # 音频帧 / 控制帧收发抽象
class LocalAudioTransport(TransportBase): ...  # 本地麦克风 + 扬声器实现
class TransportFrame: ...
class AudioFrame: ...                          # PCM 音频帧
class ControlFrame: ...                        # 开始/结束说话等控制
class ControlFrameType(StrEnum): ...

class VADBase(ABC): ...                        # 语音活动检测
class SpeechTransition: ...                    # 说话状态切换（用户↔模型）
```

- **Transport** 负责把本地采集的 `AudioFrame` 喂给 `model.push_audio`，并把模型下行的 `AudioFrame` 播放出来。
- **VAD** 决定轮次（turn-taking）：检测到用户开始说话 → 触发 `interrupt()` 打断模型；用户说完 → 允许模型继续。

---

## 四、事件体系（ModelEvent）

**文件**：`src/agentscope/realtime/_events.py`

模型下行的事件统一为 `ModelEvent` 子类，Agent 侧据此驱动 UI 与工具：

| 事件 | 含义 |
|------|------|
| `SpeechStartedEvent` / `SpeechEndedEvent` | 模型开始/结束说话（用于前端「正在讲话」指示） |
| `AudioDeltaEvent` | 增量音频（PCM） |
| `TranscriptDeltaEvent` / `InputTranscriptionEvent` | 模型文本增量 / 用户语音转写文本 |
| `ResponseCreatedEvent` / `ResponseDoneEvent` | 一轮模型响应开始/结束 |
| `ToolCallEvent` | 模型请求调用工具（Agent 执行后 `push_tool_result` 回灌） |
| `SessionEndedEvent` | 会话结束 |
| `ModelErrorEvent` | 模型侧错误 |

---

## 五、RealtimeAgent（编排层）

**文件**：`src/agentscope/agent/_realtime/_agent.py`

```python
class RealtimeAgent:
    def __init__(self, model: RealtimeModelBase, ...) -> None: ...

    async def __aenter__(self) -> "RealtimeAgent": ...   # 上下文管理器
    async def __aexit__(self, *exc) -> None: ...

    async def connect(self) -> None: ...                 # 建立模型连接
    async def close(self) -> None: ...
    async def reply_stream(self, inputs, transport: TransportBase, ...) \
        -> AsyncGenerator[AgentEvent, None]: ...          # 主循环：音频+事件流
    async def send(self, ...) -> None: ...               # 主动发文本/音频
    async def interrupt(self) -> None: ...               # 打断模型当前发言
    @property
    def last_turn_metrics(self) -> TurnMetrics: ...      # 上一轮指标（时长/令牌）
```

### 5.1 主循环语义

`reply_stream` 把 `transport` 收到的上行音频经 `model.push_audio` 推给模型，同时把模型下行的 `ModelEvent` 翻译成统一 `AgentEvent`（文本块、数据块、工具调用等）流式吐出。工具调用通过 `ToolCallEvent` 进入 Agent 既有工具执行链路，结果用 `model.push_tool_result` 回灌，模型据此继续说话。

### 5.2 可中断性

`interrupt()` 利用后端的 `TruncationSupport`：在用户插话时截断模型已生成但未播出的音频，并让模型从「说话态」切回「聆听态」。这与 `02_智能体知识文档.md` 中的 `UserInterruptEvent` 机制同源——只不过从文本域搬到了音频域。

---

## 六、面试考点

**Q：实时语音 Agent 为什么要把模型侧和 Agent 侧分开？**
答：模型侧处理各厂商不同的 WebSocket 音频协议（Gemini/OpenAI/xAI/DashScope 各不相同），Agent 侧处理通用的「编排 + 工具 + 打断」。通过 `TransportBase` 与 `ModelEvent` 解耦后，新增一个语音后端只需实现 `RealtimeModelBase`，而交互逻辑（VAD、打断、工具回灌）可复用。

**Q：turn-taking 怎么实现？为什么需要 VAD？**
答：音频是连续的，模型不能等用户说完一整句再响应，否则延迟过高。VAD 实时检测「谁在说话」：用户起音 → 打断模型；用户停音 → 放行模型。没有 VAD 就只能靠显式「发送」按钮，失去自然对话感。

**Q：和普通的「语音识别 + 文本 Agent + 语音合成」流水线比，实时语音的优势与代价？**
答：优势是端到端低延迟、天然支持打断与插话、模型能直接消费音频特征；代价是后端必须为支持实时双向流的专用模型（如 GPT-Realtime、Gemini Live），且音频帧的传输、重连、截断比文本请求-响应复杂得多。

---

## 七、知识图谱

```
实时语音 Agent
├── 模型侧 realtime/
│   ├── RealtimeModelBase（抽象）
│   │   ├── GeminiRealtimeModel
│   │   ├── OpenAIRealtimeModel
│   │   ├── XAIRealtimeModel
│   │   └── DashScopeRealtimeModel / DashScopeAudioRealtimeModel
│   ├── RealtimeModelCard（YAML 模型卡）
│   ├── TransportBase / LocalAudioTransport（音频帧）
│   ├── VADBase（轮次切换）
│   └── ModelEvent 系列（AudioDelta / ToolCall / Speech* …）
└── 编排层 agent/_realtime/_agent.py
    └── RealtimeAgent（connect / reply_stream / interrupt / send）
```
