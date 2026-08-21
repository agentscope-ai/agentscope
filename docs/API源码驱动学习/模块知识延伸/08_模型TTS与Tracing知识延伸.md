# 模型、TTS 与 Tracing 知识延伸

> 关键词：ModelCard、多模态、TTS、DataBlock、OpenTelemetry、可观测性。

---

## 1. 产品问题

Agent 产品需要同时回答：

```text
这个模型能不能传图片？
能不能输出 thinking？
能不能语音播报？
模型调用花了多少 token？
工具调用慢在哪里？
HITL 暂停恢复怎么追踪？
```

这对应三个能力：

```text
ModelCard
中文：能力发现。

TTSMiddleware
中文：语音输出。

TracingMiddleware
中文：可观测性。
```

---

## 2. 通用知识延伸

### 2.1 能力发现

能力发现比硬编码更适合多模型系统：

```text
模型能力变化快
不同 provider 字段不同
前端需要动态渲染参数
上传类型需要跟模型能力一致
```

### 2.2 流式音频

音频不能简单当文本处理：

```text
二进制数据大
需要 chunk
需要 media_type
需要播放资源管理
需要避免新旧音频重叠
```

### 2.3 Tracing

Agent tracing 不只是 HTTP tracing：

```text
一次用户请求
  -> 多次 LLM call
  -> 多次 tool call
  -> HITL 暂停
  -> 外部执行恢复
```

所以要记录 Agent/LLM/Tool span。

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/model/_model_card.py
中文：聊天模型能力卡。

src/agentscope/tts/_tts_model_card.py
中文：TTS 模型能力卡。

src/agentscope/middleware/_tts_middleware.py
中文：文本事件转音频 DataBlock。

src/agentscope/middleware/_tracing/
中文：OpenTelemetry span。

examples/web_ui/frontend/src/utils/streamingAudio.ts
中文：前端音频播放管理。
```

---

## 4. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 模型能力为什么用 ModelCard？ | 避免前端硬编码，让 provider 能力驱动 UI 和参数。 |
| TTS 为什么走 DataBlock 事件？ | 复用 AgentEvent/SSE，并支持流式二进制内容。 |
| Tracing 记录哪些 span？ | invoke_agent、chat、execute_tool。 |
| HITL 怎么追踪？ | span 写 reply_id 和 pending tools，恢复时记录 incoming_event_type。 |

---

## 5. 可继续深挖

```text
1. ModelCard YAML 如何定义 input/output types。
2. TTS realtime 与 non-realtime 差异。
3. StreamingAudioManager 的 wav 实时播放。
4. OpenTelemetry GenAI semantic convention 属性。
```

