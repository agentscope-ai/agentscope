# SOP：src/agentscope/session 模块

## 一、功能定义（Scope）
- 层次：状态持久化层。
- 作用：将 `StateModule` 状态保存/恢复（JSON、本地等）。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/session/_session_base.py
- 类：`SessionBase`

### 文件：src/agentscope/session/_json_session.py
- 类：`JSONSession`

## 三、与其他组件的交互关系
- Agent/Memory/Plan 等均可作为 `StateModule` 参与保存。

## 四、变更流程
同 AGENTS.md。
