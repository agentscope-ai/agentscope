# SOP：src/agentscope/formatter 模块

## 一、功能定义（Scope）
- 层次：提示/消息编排层，适配不同提供方 API 的消息与工具格式（含多模态）。
- 作用：将 Msg/ToolResult 转换为提供方可接受的结构，必要时做截断/计数。
- 非目标：不做复杂 Prompt 生成策略。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/formatter/_openai_formatter.py
- 类：`OpenAIChatFormatter`、`OpenAIMultiAgentFormatter`

### 文件：src/agentscope/formatter/_formatter_base.py
- 类：`FormatterBase`

（其余补全）

## 三、与其他组件的交互关系
- Agent/Model：双向契约（消息/工具 schema/音视频）。
- Token：可选计数支持。

## 四、变更流程
同 AGENTS.md。

