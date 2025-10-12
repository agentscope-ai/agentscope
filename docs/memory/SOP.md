# SOP：src/agentscope/memory 模块

## 一、功能定义（Scope）
- 层次：记忆层，管理对话短期与长期记忆（Mem0）。
- 作用：短期记忆：轮次消息存取；长期记忆：Mem0 记录/检索、工具化接口。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/memory/_memory_base.py
- 类：`MemoryBase`

### 文件：src/agentscope/memory/_in_memory_memory.py
- 类：`InMemoryMemory`

### 文件：src/agentscope/memory/_mem0_long_term_memory.py
- 类：`Mem0LongTermMemory`

（其余补全）

## 三、与其他组件的交互关系
- Agent：在推理前/后注入/记录信息；可暴露为工具。

## 四、变更流程
同 AGENTS.md。
