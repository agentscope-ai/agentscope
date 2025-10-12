# SOP：src/agentscope/plan 模块

## 一、功能定义（Scope）
- 层次：计划与子任务管理层。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/plan/_plan_notebook.py
- 类：`PlanNotebook`

### 文件：src/agentscope/plan/_plan_model.py
- 类：`Plan`、`SubTask`

## 三、与其他组件的交互关系
- Agent：作为计划工具组接入，提供 ReAct 的“先计划后执行”。

## 四、变更流程
同 AGENTS.md。
