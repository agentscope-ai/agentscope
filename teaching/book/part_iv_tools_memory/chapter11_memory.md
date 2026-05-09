# 第11章 Memory记忆系统

> **目标**：理解Memory如何为Agent提供持久化上下文

---

## 🎯 学习目标

学完之后，你能：
- 理解Memory的作用
- 使用不同Memory实现
- 配置RAG增强记忆
- 调试记忆相关问题

---

## 🚀 先跑起来

```python
from agentscope.memory import InMemoryMemory

# 创建记忆
memory = InMemoryMemory()

# 添加对话历史
memory.add(Msg(name="user", content="我叫张三", role="user"))
memory.add(Msg(name="assistant", content="你好，张三！", role="assistant"))

# 查询记忆
relevant = memory.retrieve("用户叫什么名字")
```

---

## 🔍 Memory类型

| 类型 | 说明 | 适用场景 |
|------|------|----------|
| InMemoryMemory | 内存存储 | 短期会话 |
| RedisMemory | Redis存储 | 分布式/持久化 |
| SQLMemory | 数据库存储 | 长期存储 |
| RAGMemory | 向量检索增强 | 知识库问答 |

---

★ **Insight** ─────────────────────────────────────
- **Memory = Agent的记忆**，存储对话历史
- **retrieve** = 语义搜索记忆
- **RAG** = 向量检索增强记忆
─────────────────────────────────────────────────
