# Memory 与 RAG 模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [Message 消息结构设计](#2-message-消息结构设计)
3. [Memory 基类和实现](#3-memory-基类和实现)
4. [SQLAlchemy 异步实现](#4-sqlalchemy-异步实现)
5. [长期记忆实现](#5-长期记忆实现)
6. [RAG 模块架构](#6-rag-模块架构)
7. [向量存储实现](#7-向量存储实现)
8. [代码示例](#8-代码示例)
9. [练习题](#9-练习题)

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/memory/
├── __init__.py
├── _working_memory/
│   ├── __init__.py
│   ├── _base.py              # MemoryBase 基类
│   ├── _in_memory_memory.py  # 内存记忆实现
│   ├── _redis_memory.py      # Redis 记忆实现
│   ├── _sqlalchemy_memory.py  # SQLAlchemy 异步记忆
│   └── _tablestore_memory.py  # 阿里 Tablestore 记忆
└── _long_term_memory/
    ├── __init__.py
    ├── _long_term_memory_base.py  # 长期记忆基类
    └── _mem0/                 # Mem0 实现
    └── _reme/                 # ReMe 实现

src/agentscope/rag/
├── __init__.py
├── _knowledge_base.py        # 知识库基类
├── _document.py              # 文档类
├── _simple_knowledge.py     # 简单知识库
├── _reader/
│   ├── __init__.py
│   ├── _reader_base.py      # 读取器基类
│   ├── _text_reader.py       # 文本读取
│   ├── _pdf_reader.py        # PDF 读取
│   ├── _word_reader.py       # Word 读取
│   ├── _excel_reader.py      # Excel 读取
│   ├── _image_reader.py      # 图像读取
│   └── _ppt_reader.py        # PPT 读取
└── _store/
    ├── __init__.py
    ├── _store_base.py        # 存储基类
    ├── _milvuslite_store.py  # Milvus Lite
    ├── _qdrant_store.py      # Qdrant
    ├── _mongodb_store.py     # MongoDB
    └── _alibabacloud_mysql_store.py  # MySQL

src/agentscope/message/
├── __init__.py
└── ...
```

### 1.2 核心组件

| 组件 | 说明 |
|------|------|
| `MemoryBase` | 工作记忆抽象基类 |
| `InMemoryMemory` | 基于列表的内存实现 |
| `AsyncSQLAlchemyMemory` | 异步数据库记忆 |
| `LongTermMemoryBase` | 长期记忆抽象基类 |
| `KnowledgeBase` | RAG 知识库基类 |
| `VDBStoreBase` | 向量数据库存储基类 |

---

## 2. Message 消息结构设计

### 2.1 Msg 类

消息是 AgentScope 中信息传递的基本单元，位于 `src/agentscope/message/` 目录。

```python
class Msg:
    """Message class for agent communication."""

    def __init__(
        self,
        name: str,
        content: str | list[dict],
        role: str,
        metadata: dict | None = None,
        timestamp: float | None = None,
        invocation_id: str | None = None,
    ) -> None:
        self.name = name                    # 消息发送者名称
        self.content = content              # 消息内容
        self.role = role                    # 角色 (user/assistant/system)
        self.metadata = metadata            # 元数据
        self.timestamp = timestamp or time.time()  # 时间戳
        self.invocation_id = invocation_id  # 调用 ID
        self.id = shortuuid.uuid()          # 唯一标识
```

### 2.2 内容块类型

消息内容可以是字符串或内容块列表:

```python
# 文本块
TextBlock(type="text", text="Hello")

# 思考块
ThinkingBlock(type="thinking", thinking="Let me think...")

# 工具调用块
ToolUseBlock(
    type="tool_use",
    id="call_123",
    name="get_weather",
    input={"location": "Paris"}
)

# 工具结果块
ToolResultBlock(
    type="tool_result",
    id="call_123",
    name="get_weather",
    output=[...]
)

# 图像块
ImageBlock(type="image", source=Base64Source(...))

# 音频块
AudioBlock(type="audio", source=Base64Source(...))
```

---

## 3. Memory 基类和实现

### 3.1 MemoryBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_working_memory/_base.py:11`

```python
class MemoryBase(StateModule):
    """The base class for memory in agentscope."""

    def __init__(self) -> None:
        """Initialize the memory base."""
        super().__init__()
        self._compressed_summary: str = ""
        self.register_state("_compressed_summary")

    @abstractmethod
    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message(s) into the memory storage."""

    @abstractmethod
    async def delete(
        self,
        msg_ids: list[str],
        **kwargs: Any,
    ) -> int:
        """Remove message(s) from the storage by their IDs."""

    @abstractmethod
    async def size(self) -> int:
        """Get the number of messages in the storage."""

    @abstractmethod
    async def clear(self) -> None:
        """Clear the memory content."""

    @abstractmethod
    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get the messages from the memory."""
```

### 3.2 InMemoryMemory 实现

**文件**: `_working_memory/_in_memory_memory.py`

基于 Python 列表的简单内存记忆实现:

```python
class InMemoryMemory(MemoryBase):
    """Simple in-memory implementation using a list."""

    def __init__(self) -> None:
        """Initialize the in-memory memory."""
        super().__init__()
        self._memory: list[Msg] = []       # 消息列表
        self._marks: dict[str, list[str]] = {}  # 标记索引

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message(s) to memory."""
        if memories is None:
            return

        if not isinstance(memories, list):
            memories = [memories]

        for msg in memories:
            self._memory.append(msg)

            # 记录标记
            if marks:
                if isinstance(marks, str):
                    marks = [marks]
                for mark in marks:
                    if mark not in self._marks:
                        self._marks[mark] = []
                    self._marks[mark].append(msg.id)

    async def delete(self, msg_ids: list[str], **kwargs: Any) -> int:
        """Remove messages by IDs."""
        original_size = len(self._memory)
        self._memory = [m for m in self._memory if m.id not in msg_ids]

        # 清理标记
        for mark_ids in self._marks.values():
            mark_ids[:] = [mid for mid in mark_ids if mid not in msg_ids]

        return original_size - len(self._memory)

    async def size(self) -> int:
        """Get the number of messages."""
        return len(self._memory)

    async def clear(self) -> None:
        """Clear all messages."""
        self._memory.clear()
        self._marks.clear()

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get messages from memory."""
        # 根据标记过滤
        if mark:
            mark_ids = set(self._marks.get(mark, []))
            result = [m for m in self._memory if m.id in mark_ids]
        elif exclude_mark:
            exclude_ids = set(self._marks.get(exclude_mark, []))
            result = [m for m in self._memory if m.id not in exclude_ids]
        else:
            result = list(self._memory)

        # 添加压缩摘要
        if prepend_summary and self._compressed_summary:
            summary_msg = Msg(
                name="system",
                content=self._compressed_summary,
                role="system",
            )
            result = [summary_msg] + result

        return result
```

### 3.3 记忆标记系统

AgentScope 使用标记系统来组织记忆:

```python
# 示例：ReActAgent 中的记忆标记
class _MemoryMark(str, Enum):
    HINT = "hint"              # 提示消息（使用后删除）
    COMPRESSED = "compressed"  # 已压缩的消息

# 添加带标记的消息
await memory.add(msg, marks=_MemoryMark.HINT)

# 删除特定标记的消息
await memory.delete_by_mark(mark=_MemoryMark.HINT)

# 获取记忆（排除已压缩的）
await memory.get_memory(exclude_mark=_MemoryMark.COMPRESSED)
```

---

## 4. SQLAlchemy 异步实现

### 4.1 AsyncSQLAlchemyMemory

**文件**: `_working_memory/_sqlalchemy_memory.py`

基于 SQLAlchemy 的异步数据库记忆实现:

```python
class AsyncSQLAlchemyMemory(MemoryBase):
    """Asynchronous memory implementation using SQLAlchemy."""

    def __init__(
        self,
        url: str,
        table_name: str = "agent_memory",
        engine_kwargs: dict | None = None,
    ) -> None:
        """Initialize the async SQLAlchemy memory.

        Args:
            url: Database connection URL
            table_name: Name of the memory table
            engine_kwargs: Additional SQLAlchemy engine arguments
        """
        super().__init__()

        self._engine = create_async_engine(url, **(engine_kwargs or {}))
        self._table_name = table_name
        self._metadata = MetaData()

        # 定义表结构
        self._table = Table(
            table_name,
            self._metadata,
            Column("id", String, primary_key=True),
            Column("name", String),
            Column("content", JSON),
            Column("role", String),
            Column("metadata", JSON, nullable=True),
            Column("timestamp", Float),
            Column("invocation_id", String, nullable=True),
            Column("marks", JSON, default=[]),
        )

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add messages to the database."""
        if memories is None:
            return

        if not isinstance(memories, list):
            memories = [memories]

        async with self._engine.begin() as conn:
            await conn.execute(
                self._table.insert(),
                [
                    {
                        "id": msg.id,
                        "name": msg.name,
                        "content": msg.content,
                        "role": msg.role,
                        "metadata": msg.metadata,
                        "timestamp": msg.timestamp,
                        "invocation_id": msg.invocation_id,
                        "marks": marks if isinstance(marks, list) else [marks] if marks else [],
                    }
                    for msg in memories
                ],
            )

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Retrieve messages from the database."""
        async with self._engine.begin() as conn:
            query = self._table.select()

            if mark:
                query = query.where(self._table.c.marks.contains([mark]))
            if exclude_mark:
                query = query.where(~self._table.c.marks.contains([exclude_mark]))

            query = query.order_by(self._table.c.timestamp)

            result = await conn.execute(query)
            rows = result.fetchall()

            messages = [
                Msg(
                    name=row.name,
                    content=row.content,
                    role=row.role,
                    metadata=row.metadata,
                    timestamp=row.timestamp,
                    invocation_id=row.invocation_id,
                )
                for row in rows
            ]
            messages[0].id = rows[0].id if rows else None

        # 添加压缩摘要
        if prepend_summary and self._compressed_summary:
            summary_msg = Msg(
                name="system",
                content=self._compressed_summary,
                role="system",
            )
            messages = [summary_msg] + messages

        return messages
```

### 4.2 其他记忆实现

| 实现 | 说明 |
|------|------|
| `RedisMemory` | 基于 Redis 的分布式记忆 |
| `TablestoreMemory` | 阿里云 Tablestore 实现 |

---

## 5. 长期记忆实现

### 5.1 LongTermMemoryBase

**文件**: `src/agentscope/memory/_long_term_memory/_long_term_memory_base.py`

```python
class LongTermMemoryBase(MemoryBase):
    """Base class for long-term memory."""

    @abstractmethod
    async def retrieve(self, msg: Msg | list[Msg] | None) -> str:
        """Retrieve relevant information from long-term memory.

        Returns:
            Retrieved information as a string.
        """

    @abstractmethod
    async def record(
        self,
        memories: list[Msg],
    ) -> None:
        """Record messages to long-term memory."""

    @abstractmethod
    async def record_to_memory(self, query: str) -> ToolResponse:
        """Tool function to record to memory."""

    @abstractmethod
    async def retrieve_from_memory(self, query: str) -> ToolResponse:
        """Tool function to retrieve from memory."""
```

### 5.2 Mem0 长期记忆

```python
class Mem0LongTermMemory(LongTermMemoryBase):
    """Long-term memory implementation using Mem0."""

    def __init__(
        self,
        api_key: str | None = None,
        user_id: str = "default",
        model_name: str = "gpt-4",
    ) -> None:
        """Initialize Mem0 long-term memory."""
        import mem0

        self._client = mem0.Memory(api_key=api_key)
        self._user_id = user_id
        self._model_name = model_name

    async def retrieve(self, msg: Msg | list[Msg] | None) -> str:
        """Retrieve relevant memories."""
        query = self._extract_query(msg)

        result = self._client.search(
            query=query,
            user_id=self._user_id,
        )

        return "\n".join([r["text"] for r in result["results"]])

    async def record(self, memories: list[Msg]) -> None:
        """Record memories."""
        for msg in memories:
            if isinstance(msg.content, str):
                self._client.add(
                    text=msg.content,
                    user_id=self._user_id,
                )
```

### 5.3 ReMe 长期记忆

ReMe (Retrieve, Memorize, Evolve) 是另一种长期记忆实现:

```python
class ReMeTaskLongTermMemory(LongTermMemoryBase):
    """Task-oriented long-term memory."""

class ReMePersonalLongTermMemory(LongTermMemoryBase):
    """Personal information long-term memory."""

class ReMeToolLongTermMemory(LongTermMemoryBase):
    """Tool usage history long-term memory."""
```

---

## 6. RAG 模块架构

### 6.1 KnowledgeBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_knowledge_base.py:13`

```python
class KnowledgeBase:
    """The knowledge base abstraction for retrieval-augmented generation (RAG).

    The ``retrieve`` and ``add_documents`` methods need to be implemented
    in the subclasses.
    """

    embedding_store: VDBStoreBase
    """The embedding store for the knowledge base."""

    embedding_model: EmbeddingModelBase
    """The embedding model for the knowledge base."""

    def __init__(
        self,
        embedding_store: VDBStoreBase,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize the knowledge base."""
        self.embedding_store = embedding_store
        self.embedding_model = embedding_model

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents by the given query."""

    @abstractmethod
    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge base."""
```

### 6.2 Document 文档类

**文件**: `_document.py`

```python
class Document:
    """Document class for RAG."""

    def __init__(
        self,
        content: str,
        metadata: dict | None = None,
        score: float | None = None,
        embedding: list[float] | None = None,
    ) -> None:
        """Initialize a document.

        Args:
            content: Document text content
            metadata: Additional metadata (e.g., source, page)
            score: Relevance score from retrieval
            embedding: Pre-computed embedding vector
        """
        self.content = content
        self.metadata = metadata or {}
        self.score = score
        self.embedding = embedding
```

### 6.3 RAG 检索流程

```
┌─────────────────────────────────────────────────────────────┐
│                    用户查询                                  │
│                   query: str                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Query 重写（可选）                           │
│  - AgentScope 可以使用 LLM 重写查询以提高检索质量              │
│  - 参考: ReActAgent._retrieve_from_knowledge()              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  向量嵌入 Query                               │
│  - 使用 embedding_model 将查询转为向量                        │
│  - 调用 embedding_store.similarity_search()                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  向量相似度搜索                               │
│  - 在向量数据库中搜索最相似的文档                              │
│  - 应用 score_threshold 过滤                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  结果排序与返回                               │
│  - 按相似度分数排序                                          │
│  - 返回 top-k 文档                                           │
└─────────────────────────────────────────────────────────────┘
```

### 6.4 SimpleKnowledgeBase 实现

**文件**: `_simple_knowledge.py`

```python
class SimpleKnowledgeBase(KnowledgeBase):
    """Simple implementation of KnowledgeBase using in-memory storage."""

    def __init__(
        self,
        embedding_model: EmbeddingModelBase,
    ) -> None:
        """Initialize with an embedding model."""
        # 使用简单的内存向量存储
        store = InMemoryVectorStore()
        super().__init__(embedding_store=store, embedding_model=embedding_model)
        self._documents: list[Document] = []

    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge base."""
        # 计算嵌入向量
        texts = [doc.content for doc in documents]
        embeddings = await self.embedding_model(texts)

        # 更新文档的嵌入向量
        for doc, embedding in zip(documents, embeddings):
            doc.embedding = embedding
            self._documents.append(doc)

        # 添加到向量存储
        await self.embedding_store.add(documents)

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents."""
        # 嵌入查询
        query_embedding = (await self.embedding_model([query]))[0]

        # 搜索
        results = await self.embedding_store.search(
            query_embedding=query_embedding,
            limit=limit,
            score_threshold=score_threshold,
        )

        return results
```

---

## 7. 向量存储实现

### 7.1 VDBStoreBase 基类

**文件**: `src/agentscope/rag/_store/_store_base.py`

```python
class VDBStoreBase(ABC):
    """Base class for vector database stores."""

    @abstractmethod
    async def add(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the store."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search for similar documents."""

    @abstractmethod
    async def delete(
        self,
        ids: list[str],
        **kwargs: Any,
    ) -> None:
        """Delete documents by IDs."""
```

### 7.2 Milvus Lite 实现

```python
class MilvusLiteStore(VDBStoreBase):
    """Milvus Lite vector store implementation."""

    def __init__(
        self,
        collection_name: str = "agentscope_docs",
        dimension: int = 1536,
        **kwargs,
    ) -> None:
        """Initialize Milvus Lite store."""
        from pymilvus import MilvusClient

        self._client = MilvusClient(f"./{collection_name}.db")
        self._collection = collection_name
        self._dimension = dimension

        # 创建集合
        if not self._client.has_collection(collection_name):
            self._client.create_collection(
                collection_name=collection_name,
                dimension=dimension,
                **kwargs,
            )

    async def add(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to Milvus."""
        for doc in documents:
            self._client.insert(
                collection_name=self._collection,
                data={
                    "id": doc.id,
                    "content": doc.content,
                    "vector": doc.embedding,
                    "metadata": json.dumps(doc.metadata),
                },
            )

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search for similar documents."""
        results = self._client.search(
            collection_name=self._collection,
            data=[query_embedding],
            limit=limit,
        )

        documents = []
        for hit in results[0]:
            if score_threshold and hit["distance"] < score_threshold:
                continue

            documents.append(Document(
                content=hit["entity"]["content"],
                metadata=json.loads(hit["entity"]["metadata"]),
                score=hit["distance"],
                embedding=hit["entity"]["vector"],
            ))

        return documents
```

### 7.3 其他向量存储

| 存储 | 说明 |
|------|------|
| `QdrantStore` | Qdrant 向量数据库 |
| `MongoDBStore` | MongoDB 向量搜索 |
| `AlibabaCloudMySQLStore` | 阿里云 MySQL 向量存储 |
| `OceanBaseStore` | OceanBase 向量存储 |

### 7.4 文档读取器

**文件**: `src/agentscope/rag/_reader/`

| 读取器 | 支持格式 |
|--------|----------|
| `TextReader` | .txt, .md, .json |
| `PDFReader` | .pdf |
| `WordReader` | .docx |
| `ExcelReader` | .xlsx, .xls |
| `ImageReader` | .jpg, .png |
| `PPTReader` | .pptx |

```python
class PDFReader:
    """PDF document reader."""

    async def read(self, file_path: str) -> list[Document]:
        """Read a PDF file and return documents.

        Args:
            file_path: Path to the PDF file

        Returns:
            List of Document objects (one per page)
        """
        import pypdf

        documents = []
        reader = pypdf.PdfReader(file_path)

        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()

            documents.append(Document(
                content=text,
                metadata={
                    "source": file_path,
                    "page": page_num + 1,
                    "total_pages": len(reader.pages),
                },
            ))

        return documents
```

---

## 8. 代码示例

### 8.1 创建工作记忆

```python
from agentscope.memory import InMemoryMemory, AsyncSQLAlchemyMemory
from agentscope.message import Msg

# 简单内存记忆
memory = InMemoryMemory()

# 添加消息
await memory.add(Msg(name="user", content="Hello", role="user"))

# 批量添加
await memory.add([
    Msg(name="assistant", content="Hi there!", role="assistant"),
    Msg(name="user", content="How are you?", role="user"),
])

# 获取所有消息
all_messages = await memory.get_memory()

# 带标记的添加
await memory.add(
    Msg(name="system", content="Hint message", role="system"),
    marks="hint",
)

# 获取并排除特定标记
messages = await memory.get_memory(exclude_mark="compressed")

# 删除消息
await memory.delete([msg_id])

# 清空记忆
await memory.clear()
```

### 8.2 创建知识库

```python
from agentscope.rag import SimpleKnowledgeBase, Document
from agentscope.embedding import OpenAIEmbeddingModel

# 初始化嵌入模型
embedding_model = OpenAIEmbeddingModel(
    model_name="text-embedding-3-small",
)

# 创建知识库
kb = SimpleKnowledgeBase(embedding_model=embedding_model)

# 添加文档
docs = [
    Document(
        content="Python is a high-level programming language.",
        metadata={"source": "python.txt", "category": "programming"},
    ),
    Document(
        content="Machine learning is a subset of artificial intelligence.",
        metadata={"source": "ml.txt", "category": "ai"},
    ),
]

await kb.add_documents(docs)

# 检索
results = await kb.retrieve(
    query="What is Python?",
    limit=3,
    score_threshold=0.7,
)

for doc in results:
    print(f"[Score: {doc.score:.4f}] {doc.content}")
```

### 8.3 在 Agent 中使用记忆和知识库

```python
from agentscope import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIFormatter
from agentscope.memory import InMemoryMemory
from agentscope.rag import SimpleKnowledgeBase, Document
from agentscope.embedding import OpenAIEmbeddingModel

# 初始化组件
model = OpenAIChatModel(model_name="gpt-4")
formatter = OpenAIFormatter()
memory = InMemoryMemory()

embedding_model = OpenAIEmbeddingModel()
kb = SimpleKnowledgeBase(embedding_model=embedding_model)
await kb.add_documents([...])

# 创建 Agent
agent = ReActAgent(
    name="assistant",
    sys_prompt="You are a helpful assistant with knowledge of...",
    model=model,
    formatter=formatter,
    memory=memory,
    knowledge=kb,  # 关联知识库
    enable_rewrite_query=True,  # 启用查询重写
)

# 运行
result = await agent(Msg(name="user", content="What do you know?", role="user"))
```

### 8.4 配置长期记忆

```python
from agentscope.memory import Mem0LongTermMemory
from agentscope.rag import KnowledgeBase

# 创建长期记忆
long_term_memory = Mem0LongTermMemory(
    api_key="your-api-key",
    user_id="user_123",
)

# 创建 Agent
agent = ReActAgent(
    name="assistant",
    sys_prompt="You are a helpful assistant.",
    model=model,
    formatter=formatter,
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="both",  # 支持 Agent 控制和静态控制
)
```

---

## 9. 练习题

### 9.1 基础题

1. **分析 MemoryBase 基类的设计，参考 `_base.py:11-168`。**

2. **InMemoryMemory 如何实现消息的标记系统？**

3. **解释知识库检索的基本流程。**

### 9.2 进阶题

4. **设计一个新的记忆实现，支持基于时间窗口的自动清理。**

5. **分析 SQLAlchemy 异步实现的优缺点。**

6. **比较不同向量存储实现（Milvus、Qdrant、MongoDB）的适用场景。**

### 9.3 挑战题

7. **实现一个混合记忆系统，结合工作记忆和长期记忆的优势。**

8. **设计一个增量更新机制，当知识库文档更新时高效更新向量索引。**

9. **分析 AgentScope 的 RAG 实现与 LangChain 的 RetrievalQA 的异同。**

---

## 参考资料

- 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/`
- RAG 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/`
- Message 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/message/`

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
