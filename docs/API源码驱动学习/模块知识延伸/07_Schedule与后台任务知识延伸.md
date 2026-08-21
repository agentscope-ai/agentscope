# Schedule 与后台任务知识延伸

> 关键词：无人值守、cron、APScheduler、wakeup、ToolOffload、ToolStop、后台任务 registry。

---

## 1. 产品问题

用户可能希望 Agent：

```text
每天自动巡检
定时总结报告
定期检索知识库
后台执行长工具
完成后自动继续会话
```

这要求系统支持无人值守和后台运行。

---

## 2. Schedule 与 ToolOffload 的区别

| 能力 | Schedule | ToolOffload |
|---|---|---|
| 触发来源 | 时间 | 工具执行超时 |
| 目标 | 定时启动 Agent run | 不阻塞当前 Agent run |
| 后续动作 | 创建/唤醒 session | 完成后 inbox+wakeup |
| 面试关键词 | cron、无人值守 | 长耗时工具、后台结果回灌 |

---

## 3. 通用知识延伸

### 3.1 无人值守的难点

```text
权限不能等待人工
失败要可追踪
运行记录要可查看
重复触发要避免并发污染
结果要能回到 session
```

### 3.2 后台任务 registry

后台任务跨进程取消需要：

```text
本地 task handle
中文：真正 cancel asyncio.Task。

全局 registry
中文：让其他 worker 知道 task_id 存在。

pub/sub cancel channel
中文：把取消请求广播给 owning worker。
```

---

## 4. AgentScope 源码落地

核心入口：

```text
src/agentscope/app/_manager/_scheduler/
中文：定时任务管理。

src/agentscope/app/middleware/_tool_offload_middleware.py
中文：工具超时后台运行。

src/agentscope/app/_manager/_background_task_manager.py
中文：后台任务注册和 ToolStop。

src/agentscope/app/_manager/_wakeup_dispatcher.py
中文：wakeup 后触发 ChatService.run。
```

---

## 5. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| Schedule 怎么避免无人值守卡住？ | 使用合适权限模式，避免需要 ASK 的操作。 |
| 后台工具完成后怎么通知 Agent？ | 结果写入 inbox，并 enqueue wakeup。 |
| ToolStop 怎么跨进程取消？ | 查 registry，发 task_cancel_channel，owning worker cancel。 |
| Schedule 和 ChatService 什么关系？ | Schedule 最终仍触发同一套 ChatService.run。 |

---

## 6. 可继续深挖

```text
1. SchedulerManager 如何持久化和 restore job。
2. schedule session 列表如何展示历史运行。
3. ToolOffload 超时阈值如何配置。
4. 后台任务失败后是否应该生成用户可见事件。
```

