# P8-2 智能客服机器人

> **目标**：构建一个能回答产品问题的RAG客服

---

## 📋 需求分析

**我们要做一个**：基于知识库的智能客服，能回答产品相关问题

**核心功能**：
1. 接收用户问题
2. 在知识库中检索相关信息
3. 结合检索结果回答

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    智能客服架构                              │
│                                                             │
│  用户问题 ──► Agent ──► RAG(知识库) ──► 返回答案          │
│                              │                             │
│                              ├──► Embedding模型            │
│                              └──► Vector Store            │
└─────────────────────────────────────────────────────────────┘
```

---

## 💻 完整代码

```python showLineNumbers
# P8-2_customer_service.py
import agentscope
from agentscope import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.rag import KnowledgeBase, SimpleKnowledge

# 1. 创建知识库
kb = KnowledgeBase([
    SimpleKnowledge(
        content="我们的产品支持Python 3.8+，Java 11+，Node.js 16+"
    ),
    SimpleKnowledge(
        content="技术支持邮箱：support@example.com"
    ),
    SimpleKnowledge(
        content="产品版本：v2.1.0，发布日期：2024-01-15"
    ),
])

# 2. 初始化
agentscope.init(project="CustomerService")

# 3. 创建客服Agent
agent = ReActAgent(
    name="CustomerService",
    model=OpenAIChatModel(
        api_key="your-api-key",
        model="gpt-4"
    ),
    sys_prompt="你是一个智能客服。请根据知识库中的信息回答用户问题。",
    knowledgebases=[kb]
)

# 4. 运行
import asyncio

async def main():
    response = await agent("你们的产品支持Python吗？")
    print(f"Agent回复: {response.content}")

asyncio.run(main())
```

---

## 🔍 代码解读

### KnowledgeBase
```python
kb = KnowledgeBase([...])
```
- 管理知识库内容
- 支持检索功能

### Agent使用知识库
```python
agent = ReActAgent(
    ...,
    knowledgebases=[kb]  # 绑定知识库
)
```
- Agent自动检索知识库
- 结合检索结果回答

---

## 🚀 运行效果

```
用户输入: 你们的产品支持Python吗？

Agent检索知识库: "我们的产品支持Python 3.8+..."

Agent回复: 是的，我们的产品支持Python 3.8及以上版本。
```

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用KnowledgeBase构建知识库
- 理解了RAG（检索增强生成）
- 完成了基于知识库的智能客服
─────────────────────────────────────────────────
