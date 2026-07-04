# 权限审计 Agent Service 示例

[English](README.md) | 中文

一个可运行的 AgentScope 服务,证明 `extra_agent_middlewares` 能在真实服务中
观测权限决策和用户确认,同时复用既有的 `examples/web_ui` 前端,无需改动。

## 演示内容

- 每一次权限决策(ASK/DENY/ALLOW)及模式转换(BYPASS 压制、DONT_ASK 转换、
  allow rule 覆盖、用户确认复用)都以结构化 JSON 记录输出到服务控制台。
- 用户批准和拒绝作为独立的确认记录输出,通过 `tool_call_id` 与决策记录
  关联。
- 一个无副作用的演示工具(`PermissionAuditDemoTool`)让你能演练普通 ASK
  和 bypass-immune 安全 ASK,无需运行破坏性命令。

## 启动服务

### 前置依赖

安装带 service extras 的 AgentScope,并启动一个 Redis 实例:

```bash
pip install agentscope[full]
redis-server                 # 或:brew services start redis
```

启动审计示例服务:

```bash
cd examples/permission_audit_service
python main.py
```

然后启动既有 Web UI(无需改动):

```bash
cd examples/web_ui
pnpm install
pnpm dev
```

将 UI 指向 `http://localhost:8000`。权限交互照常出现在 UI 中;结构化审计
记录出现在服务控制台(通过 `permission_audit` logger,每行一个 JSON 对象)。

## 审计记录字段

决策记录:

```json
{
  "event": "permission_decision",
  "observed_at": "2026-07-03T12:34:56.123456+00:00",
  "user_id": "user-1",
  "agent_id": "agent-1",
  "session_id": "session-1",
  "reply_id": "reply-1",
  "tool_call_id": "call-1",
  "tool_name": "PermissionAuditDemoTool",
  "mode": "bypass",
  "resolution": "bypass_ask_suppressed",
  "effective": {"behavior": "allow", "reason": "...", "bypass_immune": false},
  "candidate": {"behavior": "ask", "reason": "...", "bypass_immune": true}
}
```

直接决策和 `USER_CONFIRMED` 时 `candidate` 为 `null`。

确认记录:

```json
{
  "event": "permission_confirmation",
  "observed_at": "2026-07-03T12:35:10.123456+00:00",
  "user_id": "user-1",
  "agent_id": "agent-1",
  "session_id": "session-1",
  "reply_id": "reply-1",
  "tool_call_id": "call-1",
  "tool_name": "PermissionAuditDemoTool",
  "confirmed": false,
  "accepted_rule_count": 0
}
```

## 场景

每个场景通过让 agent 调用 `PermissionAuditDemoTool` 并传入 `risk` 参数触发;
审计记录出现在服务控制台。`mode` 为会话的权限模式。

1. **DEFAULT ASK** —— 在 DEFAULT 模式下用 `risk=ordinary` 调用。演示工具返回
   普通 ASK;审计记录显示 `effective=ASK`、`candidate=null`、
   `resolution=DIRECT`。这是基线:即使没有任何模式转换,最终的 ASK 及其
   原因现在对中间件也可见。

   ![DEFAULT ASK —— Web UI 确认](img/scenario-1-default-ask-ui.png)
   ![DEFAULT ASK —— 控制台审计记录](img/scenario-1-default-ask-console.png)

2. **用户确认复用** —— 在场景 1 的待确认调用上点同意(可选"记住规则")。
   控制台先收到一条 `confirmed=true` 的 `permission_confirmation` 记录,
   再收到一条 `effective=ALLOW`、`resolution=USER_CONFIRMED` 的决策记录。
   两条记录通过 `tool_call_id` 关联:确认记录说明用户的选择,后续决策记录
   解释复用调用为何不再经过引擎评估。

   ![用户确认复用 —— Web UI](img/scenario-2-user-confirmed-ui.png)
   ![用户确认复用 —— 控制台记录](img/scenario-2-user-confirmed-console.png)

3. **用户拒绝** —— 拒绝待处理的调用。控制台收到一条 `confirmed=false` 的
   `permission_confirmation` 记录;之后不会再出现 `USER_CONFIRMED` 或工具
   执行记录。拒绝可与未应答、被中断的 ASK 区分开。

   ![用户拒绝 —— 确认提示](img/scenario-3-user-rejection-prompt.png)
   ![用户拒绝 —— Web UI](img/scenario-3-user-rejection-ui.png)
   ![用户拒绝 —— 控制台记录](img/scenario-3-user-rejection-console.png)

4. **BYPASS 安全压制** —— 将会话切换到 BYPASS,用 `risk=safety` 调用。
   演示工具的 bypass-immune 安全 ASK 被压制成 ALLOW。记录显示
   `candidate` = 安全 ASK(`bypass_immune=true`)、`effective=ALLOW`、
   `resolution=BYPASS_ASK_SUPPRESSED`。没有这个 hook,最终 ALLOW 会被误读
   为干净的安全批准。

   ![BYPASS 安全压制 —— Web UI](img/scenario-4-bypass-suppression-ui.png)
   ![BYPASS 安全压制 —— 控制台记录](img/scenario-4-bypass-suppression-console.png)

5. **DONT_ASK 转换** —— 无人值守(DONT_ASK 模式)下用 `risk=ordinary` 调用。
   没有用户可应答,ASK 被转成 DENY。记录显示 `candidate=ASK`、
   `effective=DENY`、`resolution=ASK_CONVERTED_TO_DENY` —— 可与显式 deny
   rule 区分开。

   ![DONT_ASK 转换 —— Web UI](img/scenario-5-dont-ask-ui.png)
   ![DONT_ASK 转换 —— 控制台记录](img/scenario-5-dont-ask-console.png)

6. **Allow rule 覆盖** —— 在 DEFAULT 模式下,先对一次待确认调用点同意并
   "记住规则",加一条 tool-name 级 allow rule,再用 `risk=ordinary` 重新
   调用演示工具(一个新的 tool call)。新调用的 ASK 被 allow rule 覆盖。
   记录显示 `candidate=ASK`、`effective=ALLOW`、
   `resolution=ASK_OVERRIDDEN_BY_ALLOW_RULE`。区别于 `USER_CONFIRMED`:
   这里是**新调用**被规则预授权,而非同一调用确认后复用。

   ![Allow rule 覆盖 —— Web UI](img/scenario-6-allow-rule-ui.png)
   ![Allow rule 覆盖 —— 控制台记录](img/scenario-6-allow-rule-console.png)

## 隐私

记录刻意排除原始 `tool_input`、原始模型输入(`tool_call.input`)、原始
权限规则内容、文件内容、shell 命令文本和凭证。生产环境消费者可增加
schema 感知的脱敏或字段白名单;本示例展示的是安全的默认行为。

> **警告 —— `reason` 字段。** `effective.reason` / `candidate.reason`
> 字段原样携带 `PermissionDecision.decision_reason`。当决策由规则匹配
> 产生时,引擎会设置 `decision_reason=f"Rule: {rule_content}"`,因此
> `reason` 可能包含所匹配规则的内容——对 Bash 是命令子串模式,对
> Write/Read 是文件路径 glob。如果你的规则匹配敏感路径或命令,请在 sink
> 持久化审计记录前对 `reason` 字段脱敏或丢弃。

## 失败语义

middleware 不捕获 sink 异常,体现框架的 fail-closed observer 契约:若
必需的审计记录失败,工具调用不会执行。如需 best-effort 日志,在 sink
体中用 `try/except` 包裹并记录传输失败。

## 与 examples/agent_service 的关系

本服务遵循相同的运行形态(通过 `create_app` 创建 FastAPI 应用、Redis
存储、`InMemoryMessageBus`、`LocalWorkspaceManager`、CORS、8000 端口的
`uvicorn`、配套的 `examples/web_ui`),但省略 RAG 和可选 MCP,使权限
生命周期保持焦点。完整功能设置参见 `examples/agent_service`。
