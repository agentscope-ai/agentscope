# RAG 知识库工程知识延伸

> 关键词：文档解析、清洗、分块、embedding、向量库、异步索引、检索注入。

---

## 1. 产品问题

RAG 不是“把文件丢给模型”。真实产品要处理：

```text
用户上传多种格式文档
文档解析可能失败
长文档需要分块
分块需要 embedding
向量库写入可能慢
前端需要看到索引进度
聊天时需要按 query 检索相关片段
```

---

## 2. 通用知识延伸

### 2.1 为什么要异步索引

文档上传后立即解析和 embedding 会很慢：

```text
PDF/Word/Excel 解析耗时
embedding 调模型耗时
向量库写入耗时
大文件可能需要很多 chunk
```

所以产品上更合理：

```text
上传成功
  -> 返回 document_id
  -> 后台索引
  -> 前端轮询状态
  -> ready 后可检索
```

### 2.2 分块为什么重要

分块影响：

```text
召回准确率
上下文长度
embedding 成本
答案引用粒度
噪声比例
```

常见 trade-off：

```text
chunk 太小
  -> 语义不完整，召回碎片化。

chunk 太大
  -> embedding 表达变粗，塞进上下文成本高。
```

---

## 3. AgentScope 源码落地

核心入口：

```text
src/agentscope/app/_router/_knowledge_base.py
中文：知识库 HTTP 接口。

src/agentscope/app/_service/_index_worker.py
中文：异步索引 worker。

src/agentscope/rag/
中文：Document、Parser、Chunker、VectorStore、KnowledgeBase。

src/agentscope/middleware/_rag.py
中文：聊天运行时检索并注入上下文。
```

核心链路：

```text
上传文档
  -> 保存 blob
  -> 创建 document record
  -> enqueue index task
  -> worker 取任务并加 lease
  -> parser 解析成 sections
  -> chunker 分块
  -> embedding
  -> vector store 写入
  -> document status ready/failed
```

---

## 4. 面试延伸点

| 问题 | 回答方向 |
|---|---|
| 上传成功是否等于可检索？ | 不等于，上传只是进入异步索引流程。 |
| RAG 里最容易出问题的地方？ | 文档解析、chunk 粒度、embedding 模型、向量库一致性、状态轮询。 |
| 为什么需要 worker lease？ | 避免多个 worker 重复处理同一个索引任务。 |
| RAGMiddleware 做什么？ | 在 reasoning 前根据当前 query 检索知识片段并注入上下文。 |

---

## 5. 可继续深挖

```text
1. 各 parser 对 PDF/Word/Excel 的结构保留。
2. ApproxTokenChunker 的分块策略。
3. Qdrant/MongoDB/Milvus 适配差异。
4. 检索结果如何排序、截断和注入。
```

