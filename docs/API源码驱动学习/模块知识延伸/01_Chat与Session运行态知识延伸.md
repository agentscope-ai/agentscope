# Chat 与 Session 运行态知识延伸

> 关键词：异步运行、Session 运行态、事件驱动、分布式锁、状态恢复、优雅终止。

---

## 1. 产品问题

用户看到的是“发一句话，等助手回复”。但 Agent 产品里，一次回复可能包含：

```text
模型流式输出
多轮 reasoning
工具调用
人类确认
后台任务
RAG 检索
TTS 音频
多 Agent 消息
```

所以 Chat 不能设计成普通同步接口：

```text
POST /chat
  -> 等完整回答
  -> 返回 response
```

更合理的是：

```text
POST /chat
  -> 触发异步 run
  -> 立即返回
  -> 前端通过 SSE 接收事件
```

中文说明：这就是 AgentScope Chat 主链路的核心产品判断。

---

## 2. 通用知识延伸

### 2.1 为什么需要 Session

Session 是运行态边界，负责承载：

```text
历史消息
当前 reply_id
权限上下文
任务上下文
模型配置
TTS 配置
知识库配置
workspace_id
team_id
```

Agent 是“配置态”，Session 是“运行态”。

```text
Agent
中文：像一个模板，保存 system prompt、默认行为、context/react config。

Session
中文：像一次具体运行环境，保存这次会话用哪个模型、有哪些历史、当前是否等待确认。
```

### 2.2 为什么需要分布式锁

同一个 session 不能同时跑两个 ChatService.run，否则会出现：

```text
两个 reply 同时写历史
两个工具调用同时改状态
两个 SSE 流事件交错
上下文顺序错乱
HITL reply_id 对不上
```

所以需要 session-level lock：

```text
session_id -> lock
中文：同一时刻集群里最多一个 worker 跑这个 session。
```

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/app/_router/_chat.py
中文：POST /chat/，触发后台 run。

src/agentscope/app/_service/_chat.py
中文：ChatService.run / _run_impl，装配模型、工具、中间件并运行 Agent。

src/agentscope/app/message_bus/
中文：分布式锁、事件 log、pub/sub、queue。

src/agentscope/agent/_agent.py
中文：Agent.reply_stream 和 ReAct 循环。

examples/web_ui/frontend/src/hooks/useMessages.ts
中文：前端消息状态机和 SSE 事件处理。
```

核心链路：

```text
用户发送消息
  -> 前端乐观追加 UserMsg
  -> POST /chat/
  -> ChatRunRegistry.spawn(ChatService.run)
  -> ChatService._run_impl
  -> acquire session lock
  -> agent.reply_stream
  -> publish_session_event
  -> SSE
  -> useMessages appendEvent
```

---

## 4. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 为什么 Chat API 不直接返回回答？ | Agent run 是长任务且有事件流、工具和 HITL，适合异步触发 + SSE。 |
| Session 和 Agent 的区别？ | Agent 是配置模板，Session 是带历史和运行态的实例。 |
| 如何避免同一会话并发运行？ | session-level distributed lock。 |
| 前端刷新怎么恢复？ | 先拉 persisted messages，再接 SSE replay/live。 |
| 中断时如何保证状态不丢？ | Agent 清理 + ChatService finally + shield 持久化。 |

---

## 5. 可继续深挖

```text
1. MessageBus.session_run / acquire_lock 的 TTL 和续期策略。
2. ReplyStart/ReplyEnd 如何构成完整消息。
3. ChatService.run 吞异常的产品影响。
4. 前端 currentReplyRef 如何处理 continuation event。
```

