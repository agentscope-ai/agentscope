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
from agentscope.agent import ReActAgent
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
    formatter=OpenAIChatFormatter(),
    knowledge=kb  # 注意是 knowledge= 不是 knowledgebases=
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

```python showLineNumbers
kb = KnowledgeBase([
    SimpleKnowledge(content="我们的产品支持Python 3.8+..."),
    SimpleKnowledge(content="技术支持邮箱：support@example.com"),
    SimpleKnowledge(content="产品版本：v2.1.0，发布日期：2024-01-15"),
])
```

**设计要点**：
- `KnowledgeBase` 是知识库的容器
- `SimpleKnowledge` 是单条知识
- 支持任意多条知识

---

### Agent使用知识库

```python showLineNumbers
agent = ReActAgent(
    name="CustomerService",
    model=OpenAIChatModel(...),
    sys_prompt="你是一个智能客服...",
    knowledgebases=[kb]  # 绑定知识库
)
```

**设计要点**：
- `knowledgebases=[kb]` 参数传入知识库
- Agent会自动检索相关知识
- 结合检索结果生成回答

---

## 🔬 项目实战思路分析

### 项目结构

```
customer_service/
├── P8-2_customer_service.py    # 主程序
├── knowledge.txt               # 知识库文件
└── README.md                 # 说明文档
```

### 开发步骤

```
Step 1: 准备知识库数据
        ↓
Step 2: 创建KnowledgeBase
        ↓
Step 3: 创建带知识库的Agent
        ↓
Step 4: 测试运行
```

### RAG工作流程

```
┌─────────────────────────────────────────────────────────────┐
│                    RAG（检索增强生成）                      │
│                                                             │
│  用户问题                                                    │
│  "支持Python吗？"                                           │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              检索（Retrieval）                        │  │
│  │   在知识库中搜索与问题相关的内容                      │  │
│  │   找到："我们的产品支持Python 3.8+..."               │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              增强（Augmentation）                    │  │
│  │   把检索到的内容加入Prompt                           │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              生成（Generation）                       │  │
│  │   LLM根据增强后的Prompt生成回答                       │  │
│  └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│  "是的，我们的产品支持Python 3.8及以上版本"                 │
└─────────────────────────────────────────────────────────────┘
```

### 调试技巧

```python
# 开启调试模式，查看检索过程
import logging
logging.basicConfig(level=logging.DEBUG)

# 查看Agent检索了哪些知识
agent = ReActAgent(..., verbose=True)
```

---

## 🚀 运行效果

```
用户输入: 你们的产品支持Python吗？

Agent检索知识库: "我们的产品支持Python 3.8+..."

Agent回复: 是的，我们的产品支持Python 3.8及以上版本。
```

---

---

## 🐛 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 检索不到相关内容 | 知识库内容不足 | 扩充知识库内容 |
| 回复不够准确 | 知识库信息过时 | 定期更新知识库 |
| 相似问题回复不同 | Embedding模型问题 | 调整相似度阈值 |

---

## 🎯 扩展思考

1. **如何支持多语言客服？**
   - 添加多语言知识库
   - Agent自动识别语言并切换

2. **如何实现意图识别？**
   - 添加意图分类器
   - 不同意图走不同流程

3. **如何添加人工客服转接？**
   - 检测无法回答的问题
   - 转接人工客服并传递上下文

4. **如何实现对话记忆？**
   - 添加Memory组件
   - 记住用户历史问题

---

★ **项目总结** ─────────────────────────────────────
- 学会了使用KnowledgeBase构建知识库
- 理解了RAG（检索增强生成）的原理
- 掌握了Agent与知识库的集成方式
- 完成了基于知识库的智能客服项目
─────────────────────────────────────────────────
