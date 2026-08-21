# Plan 计划模式知识延伸

> 关键词：任务拆解、状态外显、计划工具、Agent 可解释性、复杂任务管理。

---

## 1. 产品问题

复杂任务不能只靠一段 prompt 记忆：

```text
帮我调研、分析、写方案、改代码、验证、总结
```

如果没有结构化计划，容易出现：

```text
漏步骤
重复做
用户不知道进度
中断后难恢复
多个 Agent 协作时不知道谁负责什么
```

Plan 的作用是把“隐性的推理计划”变成“显性的任务状态”。

---

## 2. 通用知识延伸

### 2.1 计划不是推理文本

```text
推理文本
中文：模型内部思考或输出的一段自然语言。

计划状态
中文：可读、可更新、可展示、可恢复的数据结构。
```

面试时可以说：

```text
Plan 的工程价值不是让模型“想得更多”，而是让任务过程可管理。
```

### 2.2 Plan 为什么适合做工具

Plan 可以作为工具暴露给模型：

```text
TaskCreate
中文：创建任务。

TaskUpdate
中文：推进状态。

TaskList / TaskGet
中文：读取当前任务。
```

这样模型在需要计划时主动调用，而不是前端强制打开一个模式。

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/tool/_task_create_task.py
src/agentscope/tool/_task_update_task.py
src/agentscope/tool/_task_list_task.py
src/agentscope/tool/_task_get_task.py
中文：Plan 工具。

src/agentscope/app/_service/_toolkit.py
中文：把任务工具装配进 Toolkit。

examples/web_ui/frontend/src/components/panel/TaskPanel.tsx
中文：前端计划面板展示任务上下文。
```

核心链路：

```text
复杂用户目标
  -> 模型看到 Task 工具 schema
  -> 调用 TaskCreate 创建任务
  -> 执行过程中 TaskUpdate 推进状态
  -> StateChangeMiddleware 发 state_updated
  -> 前端 TaskPanel 更新
```

---

## 4. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 什么时候开启 Plan？ | 不是开关，任务工具始终可用，模型在复杂任务中主动调用。 |
| 为什么不让模型直接输出 checklist？ | checklist 不可持久化、不易更新、不易被 UI 精准渲染。 |
| Plan 和上下文压缩有什么关系？ | Plan 是结构化任务状态，压缩后仍能保留任务进度。 |
| Plan 对多 Agent 有什么帮助？ | 可以把任务拆分给 worker，并让 leader 跟踪整体进度。 |

---

## 5. 可继续深挖

```text
1. TaskContext 数据结构。
2. TaskUpdate 状态流转约束。
3. Plan 工具描述如何影响模型主动调用。
4. 前端 TaskPanel 如何处理 state_updated。
```

