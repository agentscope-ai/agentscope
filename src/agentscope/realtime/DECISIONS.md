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
