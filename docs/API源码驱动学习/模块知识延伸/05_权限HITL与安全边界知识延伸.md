# 权限 HITL 与安全边界知识延伸

> 关键词：PermissionEngine、allow/deny/ask、人类确认、外部执行、安全边界、无人值守模式。

---

## 1. 产品问题

Agent 会调用工具，工具可能：

```text
读文件
写文件
执行命令
访问网络
调用 MCP
修改状态
创建子 Agent
删除团队
```

如果完全相信模型，会有风险：

```text
误删文件
执行危险命令
泄露凭证
越权访问
无人值守任务做高风险操作
```

所以必须有权限引擎和 HITL。

---

## 2. 通用知识延伸

### 2.1 allow / deny / ask

```text
ALLOW
中文：低风险或已授权，直接执行。

DENY
中文：明确禁止，工具不执行。

ASK
中文：需要人类确认，Agent 暂停等待。
```

这是 Agent 安全里的核心三态。

### 2.2 HITL 不是弹窗

HITL 是一个协议：

```text
ToolCallBlock(state=ASKING)
  -> RequireUserConfirmEvent
  -> 前端确认卡
  -> UserConfirmResultEvent
  -> Agent resume
```

中文说明：弹窗只是 UI 表现，真正重要的是可恢复的事件和状态。

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/permission/_engine.py
中文：权限决策。

src/agentscope/agent/_agent.py
中文：工具调用前后、HITL parked 和 resume。

examples/web_ui/frontend/src/hooks/useMessages.ts
中文：确认结果发送和 continuation 处理。

src/agentscope/app/_service/_projectors.py
中文：子智能体 HITL 投影。
```

---

## 4. 无人值守的安全延伸

Schedule 场景下没有人实时盯着确认卡，所以需要权限模式：

```text
default
中文：常规交互模式，风险操作可 ASK。

dont_ask
中文：无人值守时不能 ASK，否则任务会卡住；需要预先定义允许/拒绝策略。
```

面试表达：

```text
无人值守 Agent 的关键不是让它什么都能做，而是把可做范围提前收窄。
```

---

## 5. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 为什么不只靠 prompt 约束工具？ | prompt 不能作为安全边界，必须在执行前有权限引擎。 |
| HITL 的状态怎么恢复？ | pending tool call 存在 AgentState/Msg，确认事件恢复同一个 reply。 |
| 多 Agent worker 要确认怎么办？ | 投影到 leader UI 统一处理。 |
| Schedule 无人值守如何避免卡住？ | 使用 dont_ask 或预授权策略，避免等待人确认。 |

---

## 6. 可继续深挖

```text
1. PermissionMode 的具体规则。
2. Bash parser 如何识别危险命令。
3. UserConfirmResultEvent 如何改变 ToolCallBlock 状态。
4. 外部执行和人类确认的差异。
```

