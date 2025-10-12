# SOP：src/agentscope/rag 模块

## 一、功能定义（Scope）
- 层次：RAG 能力抽象层，含文档读取器、向量库封装与知识库接口。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/rag/_knowledge_base.py
- 类：`KnowledgeBase`

### 文件：src/agentscope/rag/_store/_qdrant_store.py
- 类：`QdrantStore`

（读者/文档/存储基类等按需补全）

## 三、与其他组件的交互关系
- Agent：在推理开始前检索注入 `<retrieved_knowledge>` 提示。
- Embedding：用于构建与查询向量。

## 四、变更流程
同 AGENTS.md。
