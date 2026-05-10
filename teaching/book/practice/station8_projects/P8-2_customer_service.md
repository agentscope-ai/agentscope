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
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.rag import SimpleKnowledge, QdrantStore, TextReader
from agentscope.embedding import DashScopeTextEmbedding

# 1. 创建知识库
kb = SimpleKnowledge(
    embedding_store=QdrantStore(
        location=":memory:",
        collection_name="faq_collection",
        dimensions=1024,
    ),
    embedding_model=DashScopeTextEmbedding(...)  # 需要实际的API key
)

# 使用TextReader读取文档
reader = TextReader(chunk_size=100, split_by="char")
documents = await reader("""我们的产品支持Python 3.8+，Java 11+，Node.js 16+。
技术支持邮箱：support@example.com。
产品版本：v2.1.0，发布日期：2024-01-15。""")
await kb.add_documents(documents)

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
    knowledge=kb
)

# 4. 运行
import asyncio

async def main():
    response = await agent(Msg(
        name="user",
        content="你们的产品支持Python吗？",
        role="user"
    ))
    print(f"Agent回复: {response.content}")

asyncio.run(main())
```

---

## 🔍 代码解读

### KnowledgeBase

```python showLineNumbers
kb = SimpleKnowledge(
    embedding_store=QdrantStore(
        location=":memory:",
        collection_name="faq_collection",
        dimensions=1024,
    ),
    embedding_model=DashScopeTextEmbedding(...)
)
```

**设计要点**：
- `SimpleKnowledge` 需要 embedding_store（向量存储）和 embedding_model（ embedding模型）
- 使用 `TextReader` 读取文档
- 使用 `add_documents()` 添加文档到知识库

---

### Agent使用知识库

```python showLineNumbers
agent = ReActAgent(
    name="CustomerService",
    model=OpenAIChatModel(...),
    sys_prompt="你是一个智能客服...",
    knowledge=[kb]  # 绑定知识库
)
```

**设计要点**：
- `knowledge=[kb]` 参数传入知识库
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

## 💡 Java开发者注意

```python
# Python Agent - 知识库绑定通过构造函数参数
agent = ReActAgent(
    name="CustomerService",
    model=OpenAIChatModel(...),
    sys_prompt="你是一个智能客服...",
    knowledge=[kb]  # 类似Spring的@Bean注入
)
```

**对比Java/Spring**：
| Python AgentScope | Java Spring |
|-------------------|-------------|
| `knowledge=[kb]` | `@Autowired KnowledgeBase kb` |
| `SimpleKnowledge(content=...)` | `new KnowledgeItem(content)` |
| 自动RAG检索 | 手动查询Repository |

---

## 🎯 思考题

<details>
<summary>1. 为什么RAG比直接让LLM回答更有优势？</summary>

**答案**：RAG（检索增强生成）的优势：
- **时效性**：知识库可以随时更新，不需要重新训练模型
- **准确性**：基于真实检索内容回答，减少幻觉
- **可控性**：明确知道答案的来源，便于审计
- **成本**：比微调模型便宜，比上下文学习（Context Learning）消耗更少token
</details>

<details>
<summary>2. 如何处理知识库中矛盾的信息？</summary>

**答案**：处理矛盾信息的策略：
- **优先级策略**：给知识库设置优先级，高优先级内容优先检索
- **时间策略**：使用最新添加的知识
- **置信度策略**：让LLM评估各来源的可信度
- **人工审核**：矛盾内容触发人工审核流程
</details>

<details>
<summary>3. RAG和微调（Fine-tuning）的区别是什么？何时用哪个？</summary>

**答案**：
| 维度 | RAG | 微调 |
|------|-----|------|
| 知识更新 | 实时，无需重新训练 | 需要重新训练 |
| 成本 | 低 | 高 |
| 适合场景 | 知识库问答、动态信息 | 特定风格、格式、领域 |
| 缺点 | 依赖检索质量 | 训练成本高 |

**选择建议**：
- 知识频繁变化 → RAG
- 需要特定领域专业能力 → 微调
- 两者可以结合使用</details>
