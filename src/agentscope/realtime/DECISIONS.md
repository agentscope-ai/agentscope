# Realtime 设计决策记录

已定的事项不因单个提问重开；改变需要说明新事实。

## 已定

| 决策 | 依据 |
|---|---|
| 三层事实划分：类属性 = adapter 实现；card = 模型是什么；property = 各家归属不同的（采样率、turn detection） | 四家 API 调研：xAI 采样率可调、DashScope 固定 |
| `TruncationSupport` 三态 NONE / SERVER / EXPLICIT | OpenAI(WS)=EXPLICIT、Gemini=SERVER、DashScope/xAI=NONE |
| card 四个上下文限制字段并存，全部可选 | DashScope 按轮数+秒数、OpenAI/Gemini 按 token、同时生效 |
| 公开方法是离散输入的唯一入口，`ControlFrame` 是其线上形态 | livekit / openai SDK / pipecat / vocode 四家一致 |
| `_barge_in` 串行化，三个调用点共用一把锁 | pipecat CHANGELOG:4712 记录的双队列插队 bug |
| 复用 `AgentState`，不另建 `VoiceContext` | `append_context` 已覆盖进行中回合的追加语义 |
| 打断截断留在 `RealtimeAgent._truncate_reply`，不进 `AgentState` | 用户决定，不扩散到 `state/` |
| `SessionConfig` 二分：`chat`（Agent 驱动）/ `voice`（RealtimeAgent 驱动），内层再按 backend 判别 | 分类依据是驱动的运行时类，不是字段形状 |
| `TurnAggregator` 在 M1 做上下文合并那一半 | SERVER 模式无提交权，但脏历史会进持久化/重连/压缩 |
| M1 只做 DashScope，不做 OpenAI | 打断闭环除 provider RPC 外整条链 DashScope 都会跑到 |
| 不复用 `Agent` 实现级联 | 用户决定 |
| VAD 归 agent（`RealtimeAgent(vad=)`），不归 transport；无客户端上报的 speech 字段 | livekit 放 session 层、pipecat 主动从 transport 搬出、无一家采信浏览器 speech |
| 给了 `vad=` 就关 provider 的 turn detection，一个来源判回合；`TurnMode` 枚举删除，由 `vad is None` 推导 | livekit `_resolve_rt_turn_detection_enabled`；pipecat 警告双源发重复帧 |
| transport 由创建者拥有，`run(transport)` 只借用，不 start/close | livekit `start(room=)` 不关 room；三者平级：agent / transport / run |
| 下行泵归 agent（`connect()` 起），上行泵归 `run()`；两次 run 之间模型队列照常消费 | 否则空档里工具结果、模型帧无人处理 |
| 模型会话被 provider 关掉后**不主动重连**，下一帧用户音频触发 `connect()`（可重入，带退避，攒帧） | 静默时挂着 API 连接没必要 |
| 离散输入统一为 `send(inputs)`，镜像 `Agent.reply(inputs=)`；`interrupt()` 为快捷方式 | `send_text` / `send_confirm` 合并；`ExternalExecutionResultEvent` 入口留着，M1 抛 NotImplementedError |
| 流式接口叫 `run`，async generator，起动即迭代 | 与 `reply_stream` 结束条件不同（连接断才结束），不复用其名 |
| `TurnAggregator` 作为实体注入（`aggregator=`），不经 config；pydantic `TurnConfig` 只存在于 app 层 | 它不依赖 agent 内部件，可子类化（pipecat 的 turn strategy 家族） |
| `fade_ms` 归 transport 构造参数 | 淡出由 transport 执行 |
| provider 连接异常由 adapter 翻译成 `ModelDisconnectedError`，agent 只认它、不 import websockets；发送失败 = 断开，攒帧等下一帧重连，上行泵不死 | 真机复现：DashScope 180s 空闲关闭后 `push_audio` 撞上已关 socket，比下行泵察觉更早 |
| `run()` 入口校验 transport / VAD 与 model 的采样率，不一致直接拒绝；agent 不做重采样 | Copilot review；重采样归 transport |
| 工具结果只取 `ToolResponse.content`，`ToolChunk` 仅用于展示 | `ToolResponse` 是完整结果（与 `Agent` 一致），否则流式工具的文本会重复 |
| `RealtimeAgent` / `TurnAggregator` / `TurnMetrics` 放 `agent/_realtime/`；`realtime/` 只放模型侧（model、card、事件、transport、VAD） | 与 `Agent` 同目录；`agent/` 单向依赖 `realtime/` |
| 模型事件统一 `Event` 后缀（`AudioDeltaEvent` 等），基类 `ModelEvent` | 对齐 `agentscope.event` 的命名 |
| 命名保留 `realtime`，不改 `live`；agent 层将来挂级联后端时再评估 `VoiceAgent` | 六家里五家叫 Realtime，M1 的 provider 产品名就是 Qwen-Omni-Realtime |
| DashScope 的 Omni 和 Audio-3.0 两个 S2S API 用两个类，不在一个类里按模型名路由 | `supports_text_input` 等是 per-API 事实；`tts/_dashscope/` 一个 provider 三个类的先例 |
| 反向查找靠 card 上的 `model_type`（产出它的 class 的 `type` tag），服务层建 `{cls.type: cls}` dict，不扫名字 | 同名模型可出现在两个 class 的 card 里（OpenAI chat/responses）|
| `TurnMetrics` 是 agent 的**产出**（四个时刻），不是配置，不进构造函数；`last_turn_metrics` 读取 | 每回合的指标事件（livekit `metrics_collected`）待前端需要时再加 |
| `PlayoutPosition.first_played_at` 由 transport 在音频线程记录，agent 在回合结束时读取 | e2e_latency 的唯一真实来源；随 `LocalAudioTransport` 落地 |
| 用户打字先打断当前回复 | livekit / pipecat 默认；不打断需要额外的待处理状态 |

## 已推迟（保留概念，届时形态如下）

| 事项 | 阶段 | 预期形态 | 为什么现在不做 |
|---|---|---|---|
| filler（工具执行期间的填充语） | M2 | 本地 TTS 播放，不走 provider | DashScope 不收文本输入 |
| 独立的打断判定（叠在 VAD 之上） | M2 | 词数门槛（pipecat）或独立模型（livekit） | SERVER 模式下 provider 已决定；M1 无本地 VAD |
| `update_session(instructions, tools)` | M2 | OpenAI/DashScope/xAI 发 `session.update`；Gemini 重连+resumption handle；不改 `voice` | 见 `_agent.py` connect() 处 TODO |
| `ModelEvent.SessionResumption` | Gemini 接入时 | 随 Gemini adapter 一起回来 | Gemini 唯一的改 instructions 路径 |
| 语义端点检测 | M3 之后 | 先接 pipecat smart-turn ONNX | OpenAI/DashScope 已有 `semantic_vad` |
| 上下文压缩 | 重连功能时 | `summarize(messages) -> str` 灌进新 session | S2S 下压缩本地历史不影响 provider |
| WebSocket transport 兜底 | Safari 支持面确认后 | 同 9 字节头 + 二进制 Opus | 优先 WebTransport |

## 待定

- `SessionConfig` 二分改造单独 PR 的时机
- 模型重连后 UI 提示事件（M1 只记日志）
- `AudioDelta` 事件的 base64 惰性编码（local 场景全部白做）
