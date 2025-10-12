# SOP：src/agentscope/message 模块

## 一、功能定义（Scope）
- 层次：统一消息与内容块定义。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/message/_message_base.py
- 类：`Msg`

### 文件：src/agentscope/message/_message_block.py
- TypedDict：`TextBlock`、`ThinkingBlock`、`ImageBlock`、`AudioBlock`、`VideoBlock`、`ToolUseBlock`、`ToolResultBlock`

## 三、与其他组件的交互关系
- Agent/Formatter/Model：共同依赖的核心数据结构。

## 四、变更流程
同 AGENTS.md。
