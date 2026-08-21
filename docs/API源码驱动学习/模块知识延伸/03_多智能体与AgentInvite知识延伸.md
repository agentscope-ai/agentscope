# 多智能体与 AgentInvite 知识延伸

> 关键词：leader/worker、团队协作、AgentCreate、AgentInvite、TeamSay、跨 session 消息、HITL 投影。

---

## 1. 产品问题

多 Agent 不是为了“看起来高级”，而是解决：

```text
复杂任务需要角色分工
已有 Agent 能力需要复用
不同 worker 可以有独立上下文
leader 需要协调和汇总
worker 等待确认时，用户应该在 leader 视图处理
```

---

## 2. AgentCreate 和 AgentInvite 的区别

| 能力 | AgentCreate | AgentInvite |
|---|---|---|
| 目标 | 新建一个团队成员 | 邀请已有 Agent 加入团队 |
| 资源来源 | 当前任务临时创建 | 复用已有配置态 Agent |
| 典型场景 | leader 需要一个新角色 | 用户已有专门 Agent，如代码审查、检索专家 |
| 面试亮点 | 动态创建 worker | 资源复用、权限和 session 绑定 |

中文说明：AgentInvite 的价值在于“把已有能力纳入团队”，而不是每次都生成新 worker。

---

## 3. 通用知识延伸

### 3.1 为什么 worker 要有独立 Session

如果所有 Agent 共用一个上下文，会出现：

```text
角色混乱
工具调用状态混乱
HITL reply_id 冲突
worker 历史不可恢复
leader 难以区分谁说了什么
```

独立 Session 的好处：

```text
隔离上下文
独立运行状态
独立权限/HITL
可持久化和恢复
```

### 3.2 为什么 TeamSay 走 inbox+wakeup

TeamSay 不是直接函数调用 worker，因为 worker 可能：

```text
不在当前进程
当前 idle
当前 running
当前 parked on HITL
```

所以用：

```text
message -> worker inbox -> wakeup -> worker ChatService.run
```

中文说明：这是分布式 Agent 协作，不是内存对象互调。

---

## 4. AgentScope 源码落地

核心入口：

```text
src/agentscope/app/_tool/_team_create.py
src/agentscope/app/_tool/_agent_create.py
src/agentscope/app/_tool/_agent_invite.py
src/agentscope/app/_tool/_team_say.py
src/agentscope/app/_tool/_team_delete.py
中文：团队工具。

src/agentscope/app/_service/_projectors.py
中文：SubagentHitlProjector。

examples/web_ui/frontend/src/hooks/useMessages.ts
中文：leader UI 接收 subagent HITL CustomEvent。
```

核心链路：

```text
leader 调 TeamSay
  -> 写 worker session inbox
  -> enqueue wakeup
  -> worker run
  -> worker 产生结果或 HITL
  -> TeamSay/Projector 把状态回到 leader UI
```

---

## 5. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 多 Agent 是怎么通信的？ | 通过 session inbox + wakeup，不是进程内直接调用。 |
| AgentInvite 为什么重要？ | 复用已有 Agent 能力，涉及资源访问和团队绑定。 |
| worker HITL 为什么投影到 leader？ | 用户当前在 leader 视图，需要统一处理团队确认。 |
| 多 Agent 的难点是什么？ | 独立上下文、消息投递、状态恢复、HITL 路由、权限隔离。 |

---

## 6. 可继续深挖

```text
1. AgentInvite 如何解析 handle / agent id。
2. TeamRecord 和 TeamMember 数据结构。
3. SubagentHitlProjector 的 reconcile-on-read。
4. TeamDelete 是否清理 worker session 和 pending projection。
```

