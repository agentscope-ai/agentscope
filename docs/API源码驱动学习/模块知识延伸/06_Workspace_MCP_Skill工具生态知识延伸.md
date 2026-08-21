# Workspace / MCP / Skill 工具生态知识延伸

> 关键词：执行环境、工具生态、MCP、Skill、workspace 隔离、文件 offload。

---

## 1. 产品问题

Agent 要完成真实任务，不能只有语言模型，还需要：

```text
读写文件
执行命令
调用外部工具
加载技能说明
访问 MCP server
保存长上下文或大工具结果
```

Workspace 是这些能力的运行环境边界。

---

## 2. 通用知识延伸

### 2.1 为什么需要 workspace

没有 workspace，会出现：

```text
文件路径混乱
不同 session 互相污染
工具执行无边界
长结果无法 offload
权限上下文难管理
```

Workspace 提供：

```text
工作目录
工具列表
MCP 配置
Skill 配置
文件 offload
DataBlock offload
```

### 2.2 MCP 和 Skill 的区别

```text
MCP
中文：外部工具协议，给 Agent 增加可调用工具。

Skill
中文：能力说明和使用知识，帮助 Agent 理解如何完成某类任务。
```

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/workspace/
中文：Workspace 抽象和具体实现。

src/agentscope/app/workspace_manager/
中文：Web app 侧 workspace 生命周期和隔离策略。

src/agentscope/app/_router/_workspace.py
中文：MCP / Skill 配置接口。

src/agentscope/app/_service/_toolkit.py
中文：把 workspace tools / MCP / Skill 装配到 Toolkit。
```

---

## 4. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| Workspace 解决什么问题？ | 给 Agent 工具执行和文件操作提供边界。 |
| MCP 怎么进入 Agent？ | workspace 配置 MCP，get_toolkit 装配成工具。 |
| Skill 是不是工具？ | 不完全是，更多是能力知识和说明，可辅助工具使用。 |
| offload 有什么价值？ | 大上下文/大工具结果写入 workspace 文件，减少上下文压力。 |

---

## 5. 可继续深挖

```text
1. Local/Docker/E2B/Daytona/K8S/OpenSandbox workspace 差异。
2. workspace_id 如何绑定 session。
3. DataBlock offload 去重。
4. MCP client 的 SSE / streamable HTTP 实现。
```

