# 权限审计 Agent Service 示例

[English](README.md) | 中文

一个可运行的 AgentScope 服务，用于展示应用中间件如何观测工具调用的最终
权限决策。示例复用既有的 `examples/web_ui` 前端，无需修改前端代码。

## 演示内容

- `on_check_permission` 在工具查找和输入校验之后、Agent 消费权限决策之前
  运行。
- 审计中间件调用一次 `next_handler(**input_kwargs)`，记录最终的
  ASK/DENY/ALLOW 决策，并原样返回同一个决策对象。
- 审计记录包含应用身份和关联字段，但不包含原始工具输入。
- 一个无副作用的演示工具可确定性地触发三种最终决策行为。

这个 hook 是洋葱式拦截点，并非只能观察的通知接口。应用中间件也可以替换
返回的决策，或短路内置权限引擎，从而实现基于用户、角色、租户、环境、预算
或外部策略服务的应用级策略。使用这些能力的中间件会成为应用可信授权边界的
一部分；本示例刻意只展示“不改变决策”的审计用途。

## 启动服务

安装带 service extras 的 AgentScope，并启动 Redis：

```bash
pip install agentscope[full]
redis-server                 # 或：brew services start redis
```

启动本示例服务：

```bash
cd examples/permission_audit_service
python main.py
```

再启动既有 Web UI：

```bash
cd examples/web_ui
pnpm install
pnpm dev
```

将 UI 指向 `http://localhost:8000`。让 Agent 调用
`PermissionAuditDemoTool`，并传入 `decision=allow`、`decision=ask` 或
`decision=deny`。权限交互会照常显示在 UI 中；每次经过权限检查的调用都会在
服务控制台输出一条 JSON 审计记录。

## 审计记录

```json
{
  "event": "permission_decision",
  "observed_at": "2026-07-17T12:34:56.123456+00:00",
  "user_id": "user-1",
  "agent_id": "agent-1",
  "session_id": "session-1",
  "reply_id": "reply-1",
  "tool_call_id": "call-1",
  "tool_name": "PermissionAuditDemoTool",
  "mode": "default",
  "decision": {
    "behavior": "ask",
    "reason": "Mode: default",
    "bypass_immune": false
  }
}
```

这条记录代表完整中间件链最终返回的决策。它不提供权限引擎内部规则求值
trace，也不提供被压制的中间候选决策。原始用户确认输入属于另一个生命周期
阶段，应用如需观测可通过 `on_reply` 处理。用户批准后的调用恢复执行时，会跳过
内置引擎的重复求值，但仍经过 `on_check_permission`，因此应用策略和最终决策
审计仍然有效。

## 场景

1. **ASK** —— 在 DEFAULT 模式使用 `decision=ask`。Agent 发出用户确认请求
   之前，控制台记录最终 ASK。如果用户批准，恢复执行前还会为确认后的 ALLOW
   输出第二条记录。
2. **DENY** —— 使用 `decision=deny`。Agent 写入被拒绝的工具结果之前，记录
   最终 DENY；演示工具主体不会执行。
3. **ALLOW** —— 使用 `decision=allow`。Agent 执行无副作用的演示工具之前，
   记录最终 ALLOW。

对于尚未确认的请求，审计中间件通过 `next_handler(**input_kwargs)` 委托给
内置权限引擎。结果可能受 permission mode 和已配置规则影响，因此可能不同于
工具最初给出的建议。其他中间件还可以替换或短路该结果；本示例记录完整链条
最终返回的决策。

## 隐私与失败行为

记录刻意排除 `tool_input` 和 `tool_call.input`。`reason` 字段来自
`PermissionDecision.decision_reason`，可能包含匹配到的规则内容。如果规则中
包含敏感命令或路径模式，生产环境的 sink 应脱敏或丢弃该字段。

本示例不捕获 sink 异常，因此必需审计写入失败会在 Agent 消费决策前向上传播。
如果应用需要 best-effort 日志，可在 sink 内部捕获传输异常。

## 与 `examples/agent_service` 的关系

本服务沿用 `examples/agent_service` 的 FastAPI、Redis 和 Web UI 运行形态，
但省略 RAG 与可选 MCP 集成，使示例聚焦于权限 hook。
