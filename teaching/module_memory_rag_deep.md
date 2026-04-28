# 记忆与 RAG 模块深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [Memory 基类和实现](#2-memory-基类和实现)
   - 2.1 [MemoryBase 基类](#21-memorybase-基类)
   - 2.2 [InMemoryMemory 实现](#22-inmemorymemory-实现)
   - 2.3 [记忆标记系统](#23-记忆标记系统)
3. [SQLAlchemy 异步实现](#3-sqlalchemy-异步实现)
   - 3.1 [AsyncSQLAlchemyMemory](#31-asyncsqlalchemymemory)
   - 3.2 [其他记忆实现](#32-其他记忆实现)
4. [长期记忆实现](#4-长期记忆实现)
   - 4.1 [LongTermMemoryBase](#41-longtermmemorybase)
   - 4.2 [Mem0 长期记忆](#42-mem0-长期记忆)
   - 4.3 [ReMe 长期记忆](#43-reme-长期记忆)
5. [RAG 模块架构](#5-rag-模块架构)
   - 5.1 [KnowledgeBase 基类](#51-knowledgebase-基类)
   - 5.2 [Document 文档类](#52-document-文档类)
   - 5.3 [SimpleKnowledge 实现](#53-simpleknowledge-实现)
   - 5.4 [RAG 检索流程](#54-rag-检索流程)
6. [向量存储实现](#6-向量存储实现)
   - 6.1 [VDBStoreBase 基类](#61-vdbstorebase-基类)
   - 6.2 [MilvusLiteStore 实现](#62-milvuslitestore-实现)
   - 6.3 [QdrantStore 实现](#63-qdrantstore-实现)
   - 6.4 [其他向量存储](#64-其他向量存储)
7. [文档读取器架构](#7-文档读取器架构)
   - 7.1 [ReaderBase 基类](#71-readerbase-基类)
   - 7.2 [TextReader 实现](#72-textreader-实现)
   - 7.3 [PDFReader 实现](#73-pdfreader-实现)
   - 7.4 [其他读取器](#74-其他读取器)
8. [源码解析总结](#8-源码解析总结)
   - 8.1 [核心设计模式](#81-核心设计模式)
   - 8.2 [关键扩展点](#82-关键扩展点)
9. [代码示例](#9-代码示例)
   - 9.1 [创建工作记忆](#91-创建工作记忆)
   - 9.2 [SQLAlchemy 异步记忆](#92-sqlalchemy-异步记忆)
   - 9.3 [创建知识库](#93-创建知识库)
   - 9.4 [在智能体中使用记忆和知识库](#94-在-agent-中使用记忆和知识库)
   - 9.5 [配置 Mem0 长期记忆](#95-配置-mem0-长期记忆)
   - 9.6 [配置 ReMe 长期记忆](#96-配置-reme-长期记忆)
10. [练习题](#10-练习题)
    - 10.1 [基础题](#101-基础题)
    - 10.2 [进阶题](#102-进阶题)
    - 10.3 [挑战题](#103-挑战题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 AgentScope 提供的记忆实现类型（InMemory、SQLAlchemy、Redis 等）及其核心差异 | 列举、识别 |
| 理解 | 解释记忆标记系统（HINT、COMPRESSED 等）的设计意图和使用场景 | 解释、分类 |
| 应用 | 配置并使用 `SimpleKnowledge` 和向量存储构建一个可运行的 RAG 检索流程 | 配置、使用 |
| 分析 | 分析异步 SQLAlchemy 记忆实现中会话管理、表结构和并发控制的设计决策 | 分析、诊断 |
| 评价 | 评价不同向量存储（Milvus、Qdrant、MongoDB）在延迟、容量和部署复杂度方面的优劣 | 评价、比较 |
| 创造 | 设计一个结合长期记忆（Mem0/ReMe）和 RAG 的混合检索架构，用于多轮对话场景 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] SQLAlchemy 异步 ORM 基础 (`AsyncSession`、`select`)
- [ ] 向量检索的基本概念（余弦相似度、Embedding）
- [ ] Python 异步上下文管理器 (`asynccontextmanager`)
- [ ] 了解至少一种向量数据库的基本用法

**预计学习时间**: 40 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `InMemoryMemory` | `ConcurrentHashMap` | 内存级键值存储 |
| `AsyncSQLAlchemyMemory` | JPA + Hibernate | ORM 异步持久化 |
| Embedding 向量 | `float[]` / `Vector` | 高维向量表示 |
| `retrieval.top_k` | Elasticsearch `size` | 限制返回数量 |
| 分块(Chunking) | Lucene Analyzer | 文本预处理和切分 |

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/
├── memory/
│   ├── __init__.py
│   ├── _working_memory/
│   │   ├── __init__.py
│   │   ├── _base.py              # MemoryBase 基类
│   │   ├── _in_memory_memory.py  # 内存记忆实现
│   │   ├── _redis_memory.py      # Redis 记忆实现
│   │   ├── _sqlalchemy_memory.py  # SQLAlchemy 异步记忆
│   │   └── _tablestore_memory.py  # 阿里 Tablestore 记忆
│   └── _long_term_memory/
│       ├── __init__.py
│       ├── _long_term_memory_base.py  # 长期记忆基类
│       ├── _mem0/                 # Mem0 实现
│       └── _reme/                 # ReMe 实现
└── rag/
    ├── __init__.py
    ├── _knowledge_base.py        # 知识库基类
    ├── _document.py              # 文档类（包含 Document 和 DocMetadata）
    ├── _simple_knowledge.py      # SimpleKnowledge 实现
    ├── _reader/
    │   ├── __init__.py
    │   ├── _reader_base.py      # ReaderBase 基类，导出 Document
    │   ├── _text_reader.py       # 文本读取
    │   ├── _pdf_reader.py        # PDF 读取
    │   ├── _word_reader.py       # Word 读取
    │   ├── _excel_reader.py      # Excel 读取
    │   ├── _image_reader.py      # 图像读取
    │   └── _ppt_reader.py        # PPT 读取
    └── _store/
        ├── __init__.py
        ├── _store_base.py        # VDBStoreBase 基类
        ├── _milvuslite_store.py  # Milvus Lite
        ├── _qdrant_store.py      # Qdrant
        ├── _mongodb_store.py     # MongoDB
        ├── _oceanbase_store.py   # OceanBase
        └── _alibabacloud_mysql_store.py  # MySQL
```

### 1.2 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `MemoryBase` | `memory/_working_memory/_base.py:11` | 工作记忆抽象基类 |
| `InMemoryMemory` | `memory/_working_memory/_in_memory_memory.py` | 基于列表的内存实现 |
| `AsyncSQLAlchemyMemory` | `memory/_working_memory/_sqlalchemy_memory.py` | 异步数据库记忆 |
| `LongTermMemoryBase` | `memory/_long_term_memory/_long_term_memory_base.py` | 长期记忆抽象基类 |
| `KnowledgeBase` | `rag/_knowledge_base.py:13` | RAG 知识库基类 |
| `Document` | `rag/_document.py` | RAG 文档数据结构 |
| `DocMetadata` | `rag/_document.py` | 文档元数据结构 |
| `SimpleKnowledge` | `rag/_simple_knowledge.py` | 简单知识库实现 |
| `VDBStoreBase` | `rag/_store/_store_base.py` | 向量数据库存储基类 |
| `ReaderBase` | `rag/_reader/_reader_base.py` | 文档读取器基类 |

---

## 2. Memory 基类和实现

### 2.1 MemoryBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_working_memory/_base.py:11`

`MemoryBase` 是所有记忆实现的抽象基类，继承自 `StateModule`，提供了状态序列化和压缩摘要功能:

```python
# _base.py:11-30
class MemoryBase(StateModule):
    """The base class for memory in agentscope."""

    def __init__(self) -> None:
        """Initialize the memory base."""
        super().__init__()

        self._compressed_summary: str = ""  # 记忆压缩摘要

        # 注册压缩摘要为可序列化状态
        self.register_state("_compressed_summary")

    # _base.py:22-29
    async def update_compressed_summary(self, summary: str) -> None:
        """Update the compressed summary of the memory.

        Args:
            summary: The new compressed summary.
        """
        self._compressed_summary = summary
```

**核心抽象方法设计**（`_base.py:31-132`）:

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `add` | `memories`, `marks` | `None` | 添加消息，可关联标记 |
| `delete` | `msg_ids` | `int` | 按ID删除，返回删除数量 |
| `size` | - | `int` | 返回消息总数 |
| `clear` | - | `None` | 清空所有消息 |
| `get_memory` | `mark`, `exclude_mark`, `prepend_summary` | `list[Msg]` | 获取记忆 |

**记忆标记系统实现**（`_base.py:66-89`）:

```python
# _base.py:66-89
async def delete_by_mark(
    self,
    mark: str | list[str],
    *args: Any,
    **kwargs: Any,
) -> int:
    """Remove messages from the memory by their marks.

    Args:
        mark: The mark(s) of the messages to be removed.

    Returns:
        The number of messages removed.
    """
    raise NotImplementedError(
        "The delete_by_mark method is not implemented in "
        f"{self.__class__.__name__} class.",
    )
```

**统一的标记更新接口**（`_base.py:134-168`）:

```python
# _base.py:134-168
async def update_messages_mark(
    self,
    new_mark: str | None,
    old_mark: str | None = None,
    msg_ids: list[str] | None = None,
) -> int:
    """A unified method to update marks of messages.

    设计逻辑:
    - 若提供 msg_ids: 仅更新指定ID的消息
    - 若提供 old_mark: 仅更新有该标记的消息
    - 若 new_mark 为 None: 移除标记
    - 否则: 添加新标记（可替换旧标记）

    Returns:
        The number of messages updated.
    """
    raise NotImplementedError(...)
```

### 2.2 InMemoryMemory 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_working_memory/_in_memory_memory.py`

基于 Python 列表的内存记忆实现，采用 `(Msg, list[str])` 元组存储消息和标记:

```python
# _in_memory_memory.py:10-21
class InMemoryMemory(MemoryBase):
    """The in-memory implementation of memory storage."""

    def __init__(self) -> None:
        """Initialize the in-memory storage."""
        super().__init__()
        # 使用元组列表存储：(消息对象, 标记列表)
        self.content: list[tuple[Msg, list[str]]] = []

        # 注册 content 为可序列化状态
        self.register_state("content")
```

**add 方法实现**（`_in_memory_memory.py:93-135`）:

```python
# _in_memory_memory.py:93-135
async def add(
    self,
    memories: Msg | list[Msg] | None,
    marks: str | list[str] | None = None,
    allow_duplicates: bool = False,
    **kwargs: Any,
) -> None:
    """Add message(s) into the memory storage.

    Args:
        memories: 要添加的消息，支持单个 Msg 或列表
        marks: 可选的标记字符串或列表，与添加的消息关联
        allow_duplicates: 是否允许重复消息，默认 False 表示根据 ID 去重

    流程:
    1. 参数类型规范化（统一转为列表）
    2. 标记类型检查和转换
    3. 去重检查（可选，allow_duplicates=False 时生效）
    4. 深度拷贝后追加到 content 列表
    """
    if memories is None:
        return

    if isinstance(memories, Msg):
        memories = [memories]

    # 标记类型检查
    if marks is None:
        marks = []
    elif isinstance(marks, str):
        marks = [marks]
    elif not isinstance(marks, list) or not all(
        isinstance(m, str) for m in marks
    ):
        raise TypeError(
            f"The mark should be a string, a list of strings, or None, "
            f"but got {type(marks)}.",
        )

    # 可选去重
    if not allow_duplicates:
        existing_ids = {msg.id for msg, _ in self.content}
        memories = [msg for msg in memories if msg.id not in existing_ids]

    # 深度拷贝防止外部修改
    for msg in memories:
        self.content.append((deepcopy(msg), deepcopy(marks)))
```

**get_memory 实现**（`_in_memory_memory.py:22-91`）:

```python
# _in_memory_memory.py:66-91
# 过滤逻辑
filtered_content = [
    (msg, marks)
    for msg, marks in self.content
    if mark is None or mark in marks  # 包含指定标记
]

# 排除逻辑
if exclude_mark is not None:
    filtered_content = [
        (msg, marks)
        for msg, marks in filtered_content
        if exclude_mark not in marks  # 排除指定标记
    ]

# 前置压缩摘要
if prepend_summary and self._compressed_summary:
    return [
        Msg("user", self._compressed_summary, "user"),
        *[msg for msg, _ in filtered_content],
    ]

return [msg for msg, _ in filtered_content]
```

**delete_by_mark 实现**（`_in_memory_memory.py:160-197`）:

```python
# _in_memory_memory.py:160-197
async def delete_by_mark(
    self,
    mark: str | list[str],
    **kwargs: Any,
) -> int:
    """Remove messages from the memory by their marks."""
    if isinstance(mark, str):
        mark = [mark]

    initial_size = len(self.content)
    # 对每个标记进行过滤
    for m in mark:
        self.content = [
            (msg, marks) for msg, marks in self.content if m not in marks
        ]

    return initial_size - len(self.content)
```

**update_messages_mark 实现**（`_in_memory_memory.py:212-271`）:

```python
# _in_memory_memory.py:212-271
async def update_messages_mark(
    self,
    new_mark: str | None,
    old_mark: str | None = None,
    msg_ids: list[str] | None = None,
) -> int:
    """统一标记更新方法.

    三种操作模式:
    1. 添加标记: new_mark=xxx, old_mark=None
    2. 替换标记: new_mark=yyy, old_mark=xxx
    3. 移除标记: new_mark=None, old_mark=xxx
    """
    updated_count = 0

    for idx, (msg, marks) in enumerate(self.content):
        # 条件1: msg_ids 过滤
        if msg_ids is not None and msg.id not in msg_ids:
            continue

        # 条件2: old_mark 过滤
        if old_mark is not None and old_mark not in marks:
            continue

        # 移除旧标记
        if new_mark is None:
            if old_mark in marks:
                marks.remove(old_mark)
                updated_count += 1
        # 添加新标记
        else:
            if old_mark in marks:
                marks.remove(old_mark)
            if new_mark not in marks:
                marks.append(new_mark)
                updated_count += 1

        self.content[idx] = (msg, marks)

    return updated_count
```

**序列化支持**（`_in_memory_memory.py:273-305`）:

```python
# _in_memory_memory.py:273-305
def state_dict(self) -> dict:
    """Get the state dictionary for serialization."""
    return {
        **super().state_dict(),
        "content": [[msg.to_dict(), marks] for msg, marks in self.content],
    }

def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
    """Load the state dictionary for deserialization."""
    # 支持新旧两种格式兼容
    for item in state_dict.get("content", []):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            msg_dict, marks = item
            msg = Msg.from_dict(msg_dict)
            self.content.append((msg, marks))
        elif isinstance(item, dict):
            # 兼容旧版本格式
            msg = Msg.from_dict(item)
            self.content.append((msg, []))
```

### 2.3 记忆标记系统

AgentScope 使用标记系统来组织记忆，实现消息分类和条件过滤:

```python
# 标记类型定义示例
class _MemoryMark(str, Enum):
    HINT = "hint"              # 提示消息（使用后删除）
    COMPRESSED = "compressed"  # 已压缩的消息
    IMPORTANT = "important"    # 重要标记

# 添加带标记的消息
await memory.add(msg, marks=_MemoryMark.HINT)

# 删除特定标记的消息
await memory.delete_by_mark(mark=_MemoryMark.HINT)

# 获取记忆（排除已压缩的）
await memory.get_memory(exclude_mark=_MemoryMark.COMPRESSED)

# 替换标记
await memory.update_messages_mark(
    new_mark=_MemoryMark.COMPRESSED,
    old_mark=_MemoryMark.HINT,
)
```

---

## 3. SQLAlchemy 异步实现

### 3.1 AsyncSQLAlchemyMemory

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_working_memory/_sqlalchemy_memory.py`

基于 SQLAlchemy 的异步数据库记忆实现，支持多用户多会话隔离:

**表结构设计**（`_sqlalchemy_memory.py:44-118`）:

```python
# _sqlalchemy_memory.py:44-118
class AsyncSQLAlchemyMemory(MemoryBase):
    """SQLAlchemy-based async memory with multi-tenant support."""

    class MessageTable(Base):
        """消息表"""
        __tablename__ = "message"

        id = Column(String(255), primary_key=True)
        # 复合主键格式: "{user_id}-{session_id}-{message_id}"

        msg = Column(JSON, nullable=False)  # 消息内容JSON
        session_id = Column(String(255), ForeignKey("session.id"), nullable=False)
        index = Column(BigInteger, nullable=False, index=True)
        # 消息索引用于保证顺序

    class MessageMarkTable(Base):
        """消息标记表"""
        __tablename__ = "message_mark"

        msg_id = Column(String(255), ForeignKey("message.id", ondelete="CASCADE"), primary_key=True)
        mark = Column(String(255), primary_key=True)
        # 组合主键实现多标记支持

    class SessionTable(Base):
        """会话表"""
        __tablename__ = "session"

        id = Column(String(255), primary_key=True)
        user_id = Column(String(255), ForeignKey("users.id"), nullable=False)
        messages = relationship("MessageTable", back_populates="session")

    class UserTable(Base):
        """用户表"""
        __tablename__ = "users"

        id = Column(String(255), primary_key=True)
        sessions = relationship("SessionTable", back_populates="user")
```

**初始化与连接管理**（`_sqlalchemy_memory.py:120-174`）:

```python
# _sqlalchemy_memory.py:120-174
def __init__(
    self,
    engine_or_session: AsyncEngine | AsyncSession,
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    super().__init__()

    if isinstance(engine_or_session, AsyncEngine):
        self._session_factory = async_sessionmaker(
            bind=engine_or_session,
            expire_on_commit=False,
        )
    elif isinstance(engine_or_session, AsyncSession):
        self._session_factory = None
        self._db_session = engine_or_session

    self.session_id = session_id or "default_session"
    self.user_id = user_id or "default_user"
    self._initialized = False
    self._lock = asyncio.Lock()  # 写入锁保证并发安全
```

**会话管理与上下文管理器**（`_sqlalchemy_memory.py:175-224`）:

```python
# _sqlalchemy_memory.py:175-224
@asynccontextmanager
async def _write_session(self) -> AsyncIterator[None]:
    """获取写锁并自动提交/回滚."""
    async with self._lock:
        try:
            yield
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

@property
def session(self) -> AsyncSession:
    """获取当前数据库会话，必要时创建新会话."""
    if self._session_factory is None:
        return self._db_session

    # 检查会话有效性
    if self._db_session is None or not self._db_session.is_active:
        self._db_session = self._session_factory()
        self._initialized = False

    return self._db_session
```

**表创建逻辑**（`_sqlalchemy_memory.py:226-277`）:

```python
# _sqlalchemy_memory.py:226-277
async def _create_table(self) -> None:
    """创建数据库表和初始记录."""
    if self._initialized:
        return

    engine: AsyncEngine = self.session.bind

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 创建用户记录
    result = await self.session.execute(
        select(self.UserTable).filter(self.UserTable.id == self.user_id),
    )
    user_record = result.scalar_one_or_none()

    if user_record is None:
        user_record = self.UserTable(id=self.user_id)
        self.session.add(user_record)

    # 创建会话记录
    result = await self.session.execute(
        select(self.SessionTable).filter(self.SessionTable.id == self.session_id),
    )
    session_record = result.scalar_one_or_none()

    if session_record is None:
        session_record = self.SessionTable(
            id=self.session_id,
            user_id=self.user_id,
        )
        self.session.add(session_record)

    await self.session.commit()
    self._initialized = True
```

**get_memory 实现**（`_sqlalchemy_memory.py:279-374`）:

```python
# _sqlalchemy_memory.py:279-374
async def get_memory(
    self,
    mark: str | None = None,
    exclude_mark: str | None = None,
    prepend_summary: bool = True,
    **kwargs: Any,
) -> list[Msg]:
    """Get the messages from the memory by mark (if provided)."""
    # 类型检查
    if mark is not None and not isinstance(mark, str):
        raise TypeError(
            f"The mark should be a string or None, but got {type(mark)}.",
        )

    if exclude_mark is not None and not isinstance(exclude_mark, str):
        raise TypeError(
            f"The exclude_mark should be a string or None, but got "
            f"{type(exclude_mark)}.",
        )

    await self._create_table()

    # Step 1: 按 session_id 过滤（利用索引）
    base_query = select(self.MessageTable).filter(
        self.MessageTable.session_id == self.session_id,
    )

    # Step 2: 按 mark 过滤（JOIN 标记表）
    if mark:
        base_query = base_query.join(
            self.MessageMarkTable,
            self.MessageTable.id == self.MessageMarkTable.msg_id,
        ).filter(
            self.MessageMarkTable.mark == mark,
        )

    # Step 3: 排除指定 mark
    if exclude_mark:
        exclude_subquery = (
            select(self.MessageMarkTable.msg_id)
            .filter(
                self.MessageMarkTable.msg_id.in_(
                    select(self.MessageTable.id).filter(
                        self.MessageTable.session_id == self.session_id,
                    ),
                ),
                self.MessageMarkTable.mark == exclude_mark,
            )
            .scalar_subquery()
        )
        base_query = base_query.filter(
            self.MessageTable.id.notin_(exclude_subquery),
        )

    # Step 4: 按 index 排序
    query = base_query.order_by(self.MessageTable.index)

    result = await self.session.execute(query)
    results = result.scalars().all()

    msgs = [Msg.from_dict(result.msg) for result in results]
    if prepend_summary and self._compressed_summary:
        return [
            Msg("user", self._compressed_summary, "user"),
            *msgs,
        ]

    return msgs
```

### 3.2 其他记忆实现

| 实现 | 文件 | 说明 |
|------|------|------|
| `RedisMemory` | `_redis_memory.py` | 基于 Redis 的分布式记忆，支持跨进程共享 |
| `TablestoreMemory` | `_tablestore_memory.py` | 阿里云 Tablestore 实现，适合海量数据 |

---

## 4. 长期记忆实现

### 4.1 LongTermMemoryBase

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_long_term_memory/_long_term_memory_base.py`

长期记忆基类，继承自 `StateModule`，是时间序列记忆管理系统的抽象:

```python
# _long_term_memory_base.py:11-94
class LongTermMemoryBase(StateModule):
    """Base class for long-term memory.

    设计理念:
    - `record`/`retrieve`: 面向开发者的 API
    - `record_to_memory`/`retrieve_from_memory`: 智能体自主调用的工具函数
    """

    async def record(
        self,
        msgs: list[Msg | None],
        **kwargs: Any,
    ) -> Any:
        """开发者接口：记录消息到长期记忆."""
        raise NotImplementedError(...)

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """开发者接口：从长期记忆检索信息，返回字符串供系统提示词使用."""
        raise NotImplementedError(...)

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        **kwargs: Any,
    ) -> ToolResponse:
        """智能体工具：记录重要信息到记忆.

        Args:
            thinking: 思考和推理过程
            content: 要记住的内容列表
        """
        raise NotImplementedError(...)

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        limit: int = 5,
        **kwargs: Any,
    ) -> ToolResponse:
        """智能体工具：基于关键词检索记忆.

        Args:
            keywords: 搜索关键词列表
            limit: 每个关键词返回的记忆数量
        """
        raise NotImplementedError(...)
```

### 4.2 Mem0 长期记忆

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_long_term_memory/_mem0/_mem0_long_term_memory.py`

集成 Mem0 库的长期记忆实现，提供语义搜索和关系抽取:

**初始化流程**（`_mem0_long_term_memory.py:72-378`）:

```python
# _mem0_long_term_memory.py:72-378
class Mem0LongTermMemory(LongTermMemoryBase):
    """Mem0-based long-term memory."""

    def __init__(
        self,
        agent_name: str | None = None,
        user_name: str | None = None,
        run_name: str | None = None,
        model: ChatModelBase | None = None,
        embedding_model: EmbeddingModelBase | None = None,
        vector_store_config: VectorStoreConfig | None = None,
        mem0_config: MemoryConfig | None = None,
        default_memory_type: str | None = None,
        suppress_mem0_logging: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__()

        # 1. 注册 agentscope 提供商到 mem0
        self._register_agentscope_providers()

        # 2. 验证标识符
        self._validate_identifiers(agent_name, user_name, run_name)

        # 3. 存储标识符
        self.agent_id = agent_name
        self.user_id = user_name
        self.run_id = run_name

        # 4. 配置 mem0
        mem0_config = self._configure_mem0_config(...)

        # 5. 初始化异步 memory
        self.long_term_working_memory = mem0.AsyncMemory(mem0_config)
```

**三级记忆记录策略**（`_mem0_long_term_memory.py:426-485`）:

```python
# _mem0_long_term_memory.py:426-485
async def record_to_memory(
    self,
    thinking: str,
    content: list[str],
    **kwargs: Any,
) -> ToolResponse:
    """三级降级策略确保记忆持久化:

    1. 策略一 (Primary): 以 "user" 角色记录
       - mem0 默认从 user 消息提取记忆

    2. 策略二 (Fallback): 以 "assistant" 角色记录
       - 当策略一失败时尝试
       - 若提供 agent_id，使用 AGENT_MEMORY_EXTRACTION_PROMPT

    3. 策略三 (Last Resort): 直接记录不推理
       - bypass mem0 的推理机制
       - 直接存储原始内容
    """
    try:
        if thinking:
            content = [thinking] + content

        # 策略一
        results = await self._mem0_record([{
            "role": "user",
            "content": "\n".join(content),
            "name": "user",
        }], **kwargs)

        # 策略二
        if results and isinstance(results, dict) and len(results.get("results", [])) == 0:
            results = await self._mem0_record([{
                "role": "assistant",
                "content": "\n".join(content),
                "name": "assistant",
            }], **kwargs)

        # 策略三
        if results and isinstance(results, dict) and len(results.get("results", [])) == 0:
            results = await self._mem0_record([{
                "role": "assistant",
                "content": "\n".join(content),
                "name": "assistant",
            }], infer=False, **kwargs)

        return ToolResponse(content=[TextBlock(text=f"Successfully recorded...")])
```

**并发关键词检索**（`_mem0_long_term_memory.py:530-552`）:

```python
# _mem0_long_term_memory.py:530-552
async def retrieve_from_memory(
    self,
    keywords: list[str],
    limit: int = 5,
    **kwargs: Any,
) -> ToolResponse:
    """并发搜索多个关键词."""
    try:
        results = []

        # 创建所有搜索协程
        search_coroutines = [
            self.long_term_working_memory.search(
                query=keyword,
                agent_id=self.agent_id,
                user_id=self.user_id,
                run_id=self.run_id,
                limit=limit,
            )
            for keyword in keywords
        ]

        # 并发执行
        search_results = await asyncio.gather(*search_coroutines)

        for result in search_results:
            if result:
                # 提取记忆文本
                results.extend([item["memory"] for item in result["results"]])
                # 提取关系信息
                if "relations" in result.keys():
                    results.extend(self._format_relations(result))

        return ToolResponse(content=[TextBlock(text="\n".join(results))])
```

### 4.3 ReMe 长期记忆

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/_long_term_memory/_reme/`

ReMe (Retrieve, Memorize, Evolve) 是 ModelScope 开发的记忆框架:

**ReMeLongTermMemoryBase**（`_reme_long_term_memory_base.py:77-364`）:

```python
# _reme_long_term_memory_base.py:77-364
class ReMeLongTermMemoryBase(LongTermMemoryBase, metaclass=ABCMeta):
    """ReMe 长期记忆基类."""

    def __init__(
        self,
        agent_name: str | None = None,
        user_name: str | None = None,
        run_name: str | None = None,
        model: DashScopeChatModel | OpenAIChatModel | None = None,
        embedding_model: DashScopeTextEmbedding | OpenAITextEmbedding | None = None,
        reme_config_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()

        self.agent_name = agent_name
        self.workspace_id = user_name  # ReMe 的 workspace 概念
        self.run_name = run_name

        # 提取 LLM 配置
        if isinstance(model, DashScopeChatModel):
            llm_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            llm_api_key = model.api_key
        elif isinstance(model, OpenAIChatModel):
            llm_api_base = str(getattr(model.client, "base_url", None))
            llm_api_key = str(getattr(model.client, "api_key", None))

        # 提取 Embedding 配置
        if isinstance(embedding_model, DashScopeTextEmbedding):
            embedding_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            embedding_api_key = embedding_model.api_key
        elif isinstance(embedding_model, OpenAITextEmbedding):
            embedding_api_base = str(getattr(embedding_model.client, "base_url", None))
            embedding_api_key = str(getattr(embedding_model.client, "api_key", None))

        # 初始化 ReMeApp
        from reme_ai import ReMeApp
        self.app = ReMeApp(
            *config_args,
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            embedding_api_key=embedding_api_key,
            embedding_api_base=embedding_api_base,
            config_path=reme_config_path,
            **kwargs,
        )

    # 异步上下文管理器支持
    async def __aenter__(self) -> "ReMeLongTermMemoryBase":
        if self.app is not None:
            await self.app.__aenter__()
            self._app_started = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.app is not None:
            await self.app.__aexit__(exc_type, exc_val, exc_tb)
        self._app_started = False
```

**ReMeTaskLongTermMemory**（`_reme_task_long_term_memory.py:25-154`）:

```python
# _reme_task_long_term_memory.py:25-154
class ReMeTaskLongTermMemory(ReMeLongTermMemoryBase):
    """任务记忆实现，学习执行轨迹."""

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        **kwargs: Any,
    ) -> ToolResponse:
        """记录任务执行经验.

        使用场景:
        - 解决技术问题后
        - 发现有用的技巧
        - 实施特定步骤的解决方案
        - 学习最佳实践
        """
        if not self._app_started:
            raise RuntimeError(
                "ReMeApp context not started. "
                "Please use 'async with' to initialize the app.",
            )

        messages = []

        # 添加思考过程
        if thinking:
            messages.append({"role": "user", "content": thinking})

        # 添加内容项
        for item in content:
            messages.append({"role": "user", "content": item})
            messages.append({"role": "assistant", "content": "Task information recorded."})

        # 调用 ReMe 执行
        result = await self.app.async_execute(
            name="summary_task_memory",
            workspace_id=self.workspace_id,
            trajectories=[{"messages": messages, "score": kwargs.pop("score", 1.0)}],
            **kwargs,
        )

        return ToolResponse(
            content=[TextBlock(text=f"Successfully recorded {len(content)} task memory/memories.")],
            metadata={"result": result},
        )
```

**ReMe 家族记忆类型**:

| 类型 | 文件 | 用途 |
|------|------|------|
| `ReMeToolLongTermMemory` | `_reme_tool_long_term_memory.py` | 工具使用模式和指南 |
| `ReMeTaskLongTermMemory` | `_reme_task_long_term_memory.py` | 任务执行经验和学习 |
| `ReMePersonalLongTermMemory` | `_reme_personal_long_term_memory.py` | 用户偏好和个人信息 |

---

## 5. RAG 模块架构

### 5.1 KnowledgeBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_knowledge_base.py:13`

KnowledgeBase 是 RAG 知识库的抽象基类，定义了检索和添加文档的接口:

```python
# _knowledge_base.py:13-60
class KnowledgeBase:
    """The knowledge base abstraction for retrieval-augmented generation
    (RAG).

    The ``retrieve`` and ``add_documents`` methods need to be implemented
    in the subclasses. We also provide a quick method ``retrieve_knowledge``
    that enables the agent to retrieve knowledge easily.
    """

    embedding_store: VDBStoreBase
    """向量存储实例"""

    embedding_model: EmbeddingModelBase
    """嵌入模型实例"""

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
        """Retrieve relevant documents by query.

        Args:
            query: Search query string
            limit: Maximum number of results
            score_threshold: Minimum similarity score filter

        Returns:
            List of relevant documents ranked by similarity
        """

    @abstractmethod
    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge base.

        Args:
            documents: List of documents to add
        """
```

**便捷方法 retrieve_knowledge**（`_knowledge_base.py:63-96`）:

```python
# _knowledge_base.py:63-96
async def retrieve_knowledge(
    self,
    query: str,
    limit: int = 5,
    score_threshold: float | None = None,
    **kwargs: Any,
) -> ToolResponse:
    """Retrieve relevant documents from the knowledge base. Note the
    `query` parameter is directly related to the retrieval quality, and
    for the same question, you can try many different queries to get the
    best results. Adjust the `limit` and `score_threshold` parameters
    to get more or fewer results.

    Args:
        query: The query string, which should be specific and concise. For
            example, you should provide the specific name instead of
            "you", "my", "he", "she", etc.
        limit: The number of relevant documents to retrieve.
        score_threshold: A threshold in [0, 1] and only the relevance
            score above this threshold will be returned. Reduce this
            value to get more results.
    """

    docs = await self.retrieve(
        query=query,
        limit=limit,
        score_threshold=score_threshold,
        **kwargs,
    )

    if len(docs):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Score: {_.score}, "
                    f"Content: {_.metadata.content['text']}",
                )
                for _ in docs
            ],
        )
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="No relevant documents found. TRY to reduce the "
                "`score_threshold` parameter to get "
                "more results.",
            ),
        ],
    )
```

### 5.2 Document 文档类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_document.py`

Document 和 DocMetadata 是 RAG 系统的核心数据结构，使用 `@dataclass` 装饰器定义:

```python
# _document.py
from dataclasses import dataclass, field
import shortuuid
from dashscope.api_entities.dashscope_response import DictMixin
from ..message import TextBlock, ImageBlock, VideoBlock
from ..types import Embedding

@dataclass
class DocMetadata(DictMixin):
    """The metadata of the document."""

    content: TextBlock | ImageBlock | VideoBlock
    """The data content, e.g., text, image, video."""

    doc_id: str
    """The document ID."""

    chunk_id: int
    """The chunk ID."""

    total_chunks: int
    """The total number of chunks."""


@dataclass
class Document:
    """The data chunk."""

    metadata: DocMetadata
    """The metadata of the data chunk."""

    id: str = field(default_factory=shortuuid.uuid)
    """The unique ID of the data chunk."""

    # The fields that will be filled when the document is added to or
    # retrieved from the knowledge base.

    embedding: Embedding | None = field(default_factory=lambda: None)
    """The embedding of the data chunk."""

    score: float | None = None
    """The relevance score of the data chunk."""
```

**关键设计说明**:
- `Document` 使用 `@dataclass` 装饰器，自动生成 `__init__` 等方法
- `DocMetadata` 包含文档内容的类型化表示（TextBlock/ImageBlock/VideoBlock）
- `embedding` 和 `score` 字段在添加到知识库或检索返回时自动填充
- `DictMixin` 提供了字典转换能力

### 5.3 SimpleKnowledge 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_simple_knowledge.py`

SimpleKnowledge 是 KnowledgeBase 的简单内存实现:

```python
# _simple_knowledge.py
class SimpleKnowledge(KnowledgeBase):
    """A simple knowledge base implementation."""

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant documents by the given queries.

        TODO: handle the case when the query is too long.
        """
        # 使用 embedding_model 生成查询向量
        res_embedding = await self.embedding_model(
            [
                TextBlock(
                    type="text",
                    text=query,
                ),
            ],
        )
        # 调用向量存储搜索
        res = await self.embedding_store.search(
            res_embedding.embeddings[0],
            limit=limit,
            score_threshold=score_threshold,
            **kwargs,
        )
        return res

    async def add_documents(
        self,
        documents: list[Document],
        **kwargs: Any,
    ) -> None:
        """Add documents to the knowledge."""
        # 检查文档类型是否被嵌入模型支持
        for doc in documents:
            if (
                doc.metadata.content["type"]
                not in self.embedding_model.supported_modalities
            ):
                raise ValueError(
                    f"The embedding model {self.embedding_model.model_name} "
                    f"does not support {doc.metadata.content['type']} data.",
                )

        # 获取嵌入向量
        res_embeddings = await self.embedding_model(
            [_.metadata.content for _ in documents],
        )

        # 填充 embedding 字段
        for doc, embedding in zip(documents, res_embeddings.embeddings):
            doc.embedding = embedding

        # 添加到向量存储
        await self.embedding_store.add(documents)
```

### 5.4 RAG 检索流程

```
┌─────────────────────────────────────────────────────────────┐
│                         用户查询                              │
│                    query: str                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       Query 重写（可选）                       │
│  - AgentScope 可使用 LLM 重写查询以提高检索质量                │
│  - 参考: ReActAgent._retrieve_from_knowledge()              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       向量嵌入 Query                          │
│  - 使用 embedding_model 将查询转为向量                        │
│  - 调用 embedding_store.search()                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       向量相似度搜索                           │
│  - 在向量数据库中搜索最相似的文档                              │
│  - 应用 score_threshold 过滤                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       结果排序与返回                           │
│  - 按相似度分数排序                                          │
│  - 返回 top-k 文档                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. 向量存储实现

### 6.1 VDBStoreBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_store/_store_base.py`

向量数据库存储的抽象基类:

```python
# _store_base.py
class VDBStoreBase:
    """The vector database store base class, serving as a middle layer between
    the knowledge base and the actual vector database implementation."""

    @abstractmethod
    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Record the documents into the vector database."""

    @abstractmethod
    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Delete texts from the embedding store."""

    @abstractmethod
    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve relevant texts for the given queries.

        Args:
            query_embedding: The embedding of the query text.
            limit: The number of relevant documents to retrieve.
            score_threshold: The threshold of the score to filter results.
        """

    def get_client(self) -> Any:
        """Get the underlying vector database client."""
        raise NotImplementedError(
            "``get_client`` is not implemented for "
            f"{self.__class__.__name__}.",
        )
```

### 6.2 MilvusLiteStore 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_store/_milvuslite_store.py`

轻量级 Milvus 向量存储实现:

```python
# _milvuslite_store.py
class MilvusLiteStore(VDBStoreBase):
    """The Milvus Lite vector store implementation, supporting both local and
    remote Milvus instances."""

    def __init__(
        self,
        uri: str,
        collection_name: str,
        dimensions: int,
        distance: Literal["COSINE", "L2", "IP"] = "COSINE",
        token: str = "",
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Milvus Lite store.

        Args:
            uri: For Milvus Lite, use a local file path like
                "./milvus_demo.db". For remote Milvus server,
                use URI like "http://localhost:19530".
            collection_name: The name of the collection.
            dimensions: The dimension of the embeddings.
            distance: Distance metric - "COSINE", "L2", or "IP".
            token: Authentication token for remote Milvus.
        """
        from pymilvus import MilvusClient

        client_kwargs = client_kwargs or {}
        init_params = {"uri": uri, **client_kwargs}
        if token:
            init_params["token"] = token

        self._client = MilvusClient(**init_params)
        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance

    async def _validate_collection(self) -> None:
        """Validate the collection exists, if not, create it."""
        if not self._client.has_collection(self.collection_name):
            self._client.create_collection(
                collection_name=self.collection_name,
                dimension=self.dimensions,
                metric_type=self.distance,
                **self.collection_kwargs,
            )
```

**add 方法**（`_milvuslite_store.py`）:

```python
    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to the Milvus vector store."""
        await self._validate_collection()

        data = []
        for doc in documents:
            unique_string = json.dumps(
                {
                    "doc_id": doc.metadata.doc_id,
                    "chunk_id": doc.metadata.chunk_id,
                    "content": doc.metadata.content,
                },
                ensure_ascii=False,
            )

            id_type = self.collection_kwargs.get("id_type", "int")
            if id_type == "string":
                unique_id = _map_text_to_uuid(unique_string)[:6]
            else:
                unique_id = abs(hash(unique_string)) % (10**10)

            entry = {
                "id": unique_id,
                "vector": doc.embedding,
                "doc_id": doc.metadata.doc_id,
                "chunk_id": doc.metadata.chunk_id,
                "content": doc.metadata.content,
                "total_chunks": doc.metadata.total_chunks,
            }
            data.append(entry)

        self._client.insert(collection_name=self.collection_name, data=data)
```

**search 方法**（`_milvuslite_store.py`）:

```python
    async def search(
        self,
        query_embedding: Embedding,
        limit: int,
        score_threshold: float | None = None,
        **kwargs: Any,
    ) -> list[Document]:
        """Search relevant documents from the Milvus vector store."""
        if "output_fields" not in kwargs:
            kwargs["output_fields"] = ["doc_id", "chunk_id", "content", "total_chunks"]

        results = self._client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=limit,
            **kwargs,
        )

        collected_res = []
        for hits in results:
            for hit in hits:
                if score_threshold is not None and hit["distance"] < score_threshold:
                    continue

                entity = hit["entity"]
                doc_metadata = DocMetadata(
                    content=entity.get("content", ""),
                    doc_id=entity.get("doc_id", ""),
                    chunk_id=entity.get("chunk_id", 0),
                    total_chunks=entity.get("total_chunks", 0),
                )

                collected_res.append(
                    Document(
                        embedding=None,
                        score=hit["distance"],
                        metadata=doc_metadata,
                    ),
                )

        return collected_res
```

### 6.3 QdrantStore 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_store/_qdrant_store.py`

```python
# _qdrant_store.py
class QdrantStore(VDBStoreBase):
    """The Qdrant vector store implementation, supporting both local and
    remote Qdrant instances."""

    def __init__(
        self,
        location: Literal[":memory:"] | str,
        collection_name: str,
        dimensions: int,
        distance: Literal["Cosine", "Euclid", "Dot", "Manhattan"] = "Cosine",
        client_kwargs: dict[str, Any] | None = None,
        collection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize Qdrant store.

        Args:
            location: ":memory:" for in-memory, or URL like
                "http://localhost:6333"
            collection_name: Collection name
            dimensions: Embedding dimension
            distance: Distance metric
        """
        from qdrant_client import AsyncQdrantClient

        client_kwargs = client_kwargs or {}
        self._client = AsyncQdrantClient(location=location, **client_kwargs)
        self.collection_name = collection_name
        self.dimensions = dimensions
        self.distance = distance

    async def _validate_collection(self) -> None:
        """Create collection if not exists."""
        if not await self._client.collection_exists(self.collection_name):
            from qdrant_client import models

            await self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.dimensions,
                    distance=getattr(models.Distance, self.distance.upper()),
                ),
                **self.collection_kwargs,
            )

    async def add(self, documents: list[Document], **kwargs: Any) -> None:
        """Add embeddings to Qdrant."""
        await self._validate_collection()

        from qdrant_client.models import PointStruct

        await self._client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=_map_text_to_uuid(json.dumps({...})),
                    vector=_.embedding,
                    payload=_.metadata,
                )
                for _ in documents
            ],
        )
```

### 6.4 其他向量存储

| 存储 | 文件 | 适用场景 |
|------|------|----------|
| `MongoDBStore` | `_mongodb_store.py` | 已使用 MongoDB 的场景 |
| `AlibabaCloudMySQLStore` | `_alibabacloud_mysql_store.py` | 阿里云 MySQL 用户 |
| `OceanBaseStore` | `_oceanbase_store.py` | OceanBase 数据库用户 |

---

## 7. 文档读取器架构

### 7.1 ReaderBase 基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_reader/_reader_base.py`

ReaderBase 是所有文档读取器的抽象基类:

```python
# _reader_base.py
class ReaderBase:
    """The reader base class, which is responsible for reading the original
    data, splitting it into chunks, and converting each chunk into a `Document`
    object."""

    @abstractmethod
    async def __call__(self, *args: Any, **kwargs: Any) -> list[Document]:
        """The async call function that takes the input files and returns the
        vector records"""

    @abstractmethod
    def get_doc_id(self, *args: Any, **kwargs: Any) -> str:
        """Get a unique document ID for the input data.

        Returns:
            A unique document ID for the input data.
        """
```

**Document 导出**: 在 `_reader/__init__.py` 中，Document 被重新导出供外部使用:

```python
# _reader/__init__.py
from ._reader_base import ReaderBase, Document
# ...
__all__ = [
    "Document",  # 从 _reader_base 导出
    "ReaderBase",
    # ...
]
```

### 7.2 TextReader 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_reader/_text_reader.py`

TextReader 支持多种文本分割策略:

```python
# _text_reader.py
class TextReader(ReaderBase):
    """The text reader that splits text into chunks by a fixed chunk size
    and chunk overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
    ) -> None:
        """Initialize the text reader.

        Args:
            chunk_size: The size of each chunk, in number of characters.
            split_by: The unit to split the text - "char", "sentence", or
                "paragraph". Note that "sentence" uses nltk library.
        """
        if chunk_size <= 0:
            raise ValueError(f"The chunk_size must be positive, got {chunk_size}")

        if split_by not in ["char", "sentence", "paragraph"]:
            raise ValueError(f"split_by must be 'char', 'sentence' or 'paragraph'")

        self.chunk_size = chunk_size
        self.split_by = split_by

    async def __call__(self, text: str) -> list[Document]:
        """Read a text string, split it into chunks, and return Documents."""
        # 支持从文件路径读取
        if os.path.exists(text) and os.path.isfile(text):
            with open(text, "r", encoding="utf-8") as file:
                text = file.read()

        # 根据 split_by 策略分割
        splits = []
        if self.split_by == "char":
            for i in range(0, len(text), self.chunk_size):
                splits.append(text[i:i + self.chunk_size])

        elif self.split_by == "sentence":
            import nltk
            nltk.download("punkt", quiet=True)
            nltk.download("punkt_tab", quiet=True)
            sentences = nltk.sent_tokenize(text)
            # 处理每个句子...

        elif self.split_by == "paragraph":
            paragraphs = [_ for _ in text.split("\n") if len(_)]
            for para in paragraphs:
                if len(para) <= self.chunk_size:
                    splits.append(para)
                else:
                    splits.extend([para[k:k+self.chunk_size] for k in range(0, len(para), self.chunk_size)])

        doc_id = self.get_doc_id(text)

        return [
            Document(
                id=doc_id,
                metadata=DocMetadata(
                    content=TextBlock(type="text", text=_),
                    doc_id=doc_id,
                    chunk_id=idx,
                    total_chunks=len(splits),
                ),
            )
            for idx, _ in enumerate(splits)
        ]

    def get_doc_id(self, text: str) -> str:
        """Get the document ID based on content hash."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

### 7.3 PDFReader 实现

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/_reader/_pdf_reader.py`

PDFReader 利用 TextReader 进行文本分块:

```python
# _pdf_reader.py
class PDFReader(ReaderBase):
    """The PDF reader that splits text into chunks by a fixed chunk size."""

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"The chunk_size must be positive, got {chunk_size}")

        if split_by not in ["char", "sentence", "paragraph"]:
            raise ValueError(f"split_by must be 'char', 'sentence' or 'paragraph'")

        self.chunk_size = chunk_size
        self.split_by = split_by

        # 复用 TextReader 进行分块
        self._text_reader = TextReader(self.chunk_size, self.split_by)

    async def __call__(self, pdf_path: str) -> list[Document]:
        """Read a PDF file, split it into chunks, and return Documents."""
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)

        # 提取所有页面的文本
        gather_texts = []
        for page in reader.pages:
            gather_texts.append(page.extract_text())

        doc_id = self.get_doc_id(pdf_path)

        # 使用 TextReader 分块
        docs = await self._text_reader("\n\n".join(gather_texts))
        for doc in docs:
            doc.id = doc_id

        return docs

    def get_doc_id(self, pdf_path: str) -> str:
        """Get the document ID based on file path hash."""
        return hashlib.sha256(pdf_path.encode("utf-8")).hexdigest()
```

### 7.4 其他读取器

| 读取器 | 文件 | 支持格式 | 关键依赖 |
|--------|------|----------|----------|
| `WordReader` | `_word_reader.py` | .docx | python-docx |
| `ExcelReader` | `_excel_reader.py` | .xlsx, .xls | openpyxl |
| `ImageReader` | `_image_reader.py` | .jpg, .png | PIL |
| `PowerPointReader` | `_ppt_reader.py` | .pptx | python-pptx |

---

## 8. 源码解析总结

### 8.1 核心设计模式

**策略模式**:

```python
# MemoryBase 是策略模式的抽象策略
# 具体的 InMemoryMemory、SQLAlchemyMemory 等是具体策略
# 智能体在运行时可以选择不同的记忆策略

class 智能体:
    def __init__(self, memory: MemoryBase):  # 依赖抽象而非具体
        self.memory = memory
```

**模板方法模式**:

```python
# LongTermMemoryBase 定义了 record/retrieve 的模板
# 子类实现具体的 _mem0_record、_reme_execute 等步骤

class Mem0LongTermMemory(LongTermMemoryBase):
    async def record(self, msgs, **kwargs):
        # 使用三级降级策略模板
        await self._mem0_record(...)
```

**状态模式**:

```python
# StateModule 提供了状态序列化和恢复能力
# Memory 模块的 content、_compressed_summary 都支持持久化

class InMemoryMemory(MemoryBase):
    def state_dict(self) -> dict:
        return {"content": [[msg.to_dict(), marks] for msg, marks in self.content]}

    def load_state_dict(self, state_dict: dict, strict: bool = True):
        # 从持久化状态恢复
```

**适配器模式**:

```python
# VDBStoreBase 作为中间层，适配不同的向量数据库
# 对上层（KnowledgeBase）提供统一接口
# 对下层封装具体数据库的客户端 API
```

### 8.2 关键扩展点

| 扩展点 | 基类 | 实现方式 | 示例 |
|--------|------|----------|------|
| 新记忆类型 | `MemoryBase` | 继承并实现抽象方法 | `class MyMemory(MemoryBase)` |
| 新向量存储 | `VDBStoreBase` | 继承并实现 add/search/delete | `class MyVectorStore(VDBStoreBase)` |
| 新文档读取 | `ReaderBase` | 继承并实现 `__call__` 和 `get_doc_id` | `class MyReader(ReaderBase)` |
| 新长期记忆 | `LongTermMemoryBase` | 继承并实现 record/retrieve | `class MyLongTermMemory(LongTermMemoryBase)` |
| 新知识库 | `KnowledgeBase` | 继承并实现 retrieve/add_documents | `class MyKnowledge(KnowledgeBase)` |

---

## 9. 代码示例

### 9.1 创建工作记忆

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

# 允许重复消息（默认 allow_duplicates=False）
await memory.add(
    Msg(name="user", content="Same content", role="user"),
    allow_duplicates=True,  # 跳过 ID 去重检查
)

# 获取并排除特定标记
messages = await memory.get_memory(exclude_mark="compressed")

# 删除消息
await memory.delete([msg_id])

# 清空记忆
await memory.clear()
```

### 9.2 SQLAlchemy 异步记忆

```python
from sqlalchemy.ext.asyncio import create_async_engine
from agentscope.memory import AsyncSQLAlchemyMemory
from agentscope.message import Msg

# 创建异步引擎
engine = create_async_engine("sqlite+aiosqlite:///./memory.db")

# 创建记忆实例
memory = AsyncSQLAlchemyMemory(
    engine_or_session=engine,
    session_id="user_session_001",
    user_id="user_123",
)

# 添加消息
await memory.add(Msg(name="user", content="Hello", role="user"))

# 获取消息
messages = await memory.get_memory()
```

### 9.3 创建知识库

```python
from agentscope.rag import SimpleKnowledge, Document, DocMetadata, TextReader
from agentscope.embedding import OpenAIEmbeddingModel
from agentscope.message import TextBlock

# 初始化嵌入模型
embedding_model = OpenAIEmbeddingModel(model_name="text-embedding-3-small")

# 创建知识库
kb = SimpleKnowledge(embedding_model=embedding_model)

# 使用 TextReader 读取文本文件
reader = TextReader(chunk_size=512, split_by="sentence")
docs = await reader("/path/to/document.txt")

# 添加文档到知识库
await kb.add_documents(docs)

# 检索
results = await kb.retrieve(
    query="What is Python?",
    limit=3,
    score_threshold=0.7,
)

for doc in results:
    print(f"[Score: {doc.score:.4f}] {doc.metadata.content}")
```

### 9.4 在智能体中使用记忆和知识库

```python
from agentscope import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIFormatter
from agentscope.memory import InMemoryMemory
from agentscope.rag import SimpleKnowledge, Document
from agentscope.embedding import OpenAIEmbeddingModel

# 初始化组件
model = OpenAIChatModel(model_name="gpt-4")
formatter = OpenAIFormatter()
memory = InMemoryMemory()

embedding_model = OpenAIEmbeddingModel()
kb = SimpleKnowledge(embedding_model=embedding_model)
await kb.add_documents([...])

# 创建智能体
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

### 9.5 配置 Mem0 长期记忆

```python
from agentscope.memory import Mem0LongTermMemory
from agentscope.models import OpenAIChatModel
from agentscope.embedding import OpenAITextEmbedding

# 创建长期记忆
long_term_memory = Mem0LongTermMemory(
    agent_name="my_agent",
    user_id="user_123",
    model=OpenAIChatModel(model_name="gpt-4"),
    embedding_model=OpenAITextEmbedding(model_name="text-embedding-3-small"),
)

# 创建智能体
agent = ReActAgent(
    name="assistant",
    sys_prompt="You are a helpful assistant.",
    model=model,
    formatter=formatter,
    memory=InMemoryMemory(),
    long_term_memory=long_term_memory,
    long_term_memory_mode="both",
)
```

### 9.6 配置 ReMe 长期记忆

```python
from agentscope.memory import ReMeTaskLongTermMemory
from agentscope.models import OpenAIChatModel
from agentscope.embedding import OpenAITextEmbedding

# 创建任务记忆
task_memory = ReMeTaskLongTermMemory(
    agent_name="my_agent",
    user_name="user_123",
    model=OpenAIChatModel(model_name="gpt-4"),
    embedding_model=OpenAITextEmbedding(model_name="text-embedding-3-small"),
)

# 使用异步上下文管理器
async with task_memory:
    # 记录任务经验
    await task_memory.record_to_memory(
        thinking="This approach worked well for database optimization",
        content=[
            "Add indexes on WHERE clause columns",
            "Use EXPLAIN ANALYZE to identify slow queries",
        ],
    )

    # 检索相关经验
    result = await task_memory.retrieve_from_memory(
        keywords=["database optimization"],
        limit=5,
    )
```

---

## 本章关联

### 与其他模块的关系

| 关联模块 | 关联内容 | 参考位置 |
|----------|----------|----------|
| [Agent 模块深度剖析](module_agent_deep.md) | Agent 如何使用 `memory` 和 `knowledge` 参数集成记忆与知识库，记忆压缩如何影响 Agent 的上下文窗口 | 第 3.3 节 `observe()`、第 3.5 节订阅发布 |
| [Model 模块深度剖析](module_model_deep.md) | Embedding 模型在 RAG 检索中的核心作用，Token 计数在记忆压缩中的应用 | 第 9 章 Embedding、第 8 章 Token 计数 |
| [Tool/MCP 模块深度剖析](module_tool_mcp_deep.md) | RAG 检索工具的开发与注册，知识库检索结果如何作为工具返回给 Agent | 第 7 章自定义工具开发 |
| [Pipeline/基础设施模块深度剖析](module_pipeline_infra_deep.md) | Session 如何持久化记忆状态，Formatter 如何处理检索结果的消息格式 | 第 5 章 Session、第 3 章 Formatter |
| [最佳实践参考](reference_best_practices.md) | RAG 优化策略（分块、重排序、混合检索）、记忆管理最佳实践 | RAG 优化章节 |

### 前置知识

- **SQLAlchemy 异步 ORM**: 如不熟悉 `AsyncSession` 和 `select`，建议先阅读 [SQLAlchemy async 文档](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- **向量检索**: 需要理解 Embedding、余弦相似度的基本概念
- **数据库基础**: 需要了解表设计、索引、外键关系

### 后续学习建议

1. 完成本模块练习题后，建议继续学习 [Model 模块](module_model_deep.md) 的 Embedding 章节，深入理解向量生成机制
2. 如需构建生产级 RAG 应用，建议参考 [最佳实践](reference_best_practices.md) 中的 RAG 优化策略
3. 如需实现多轮对话记忆，建议结合 [Agent 模块](module_agent_deep.md) 的 Hook 机制，设计自定义记忆压缩策略

---

## 10. 练习题

### 10.1 基础题

1. **分析 KnowledgeBase 基类的设计**（参考 `_knowledge_base.py:13-60`）
   - KnowledgeBase 有哪些核心属性？
   - `retrieve_knowledge` 方法与 `retrieve` 方法的区别是什么？

2. **分析 Document 数据结构**（参考 `_document.py`）
   - Document 和 DocMetadata 的关系是什么？
   - 哪些字段在检索结果返回时会被填充？

3. **TextReader 分词策略**（参考 `_text_reader.py`）
   - 三种 split_by 策略的区别是什么？
   - 当段落本身超过 chunk_size 时如何处理？

4. **MilvusLiteStore 的 ID 生成策略**（参考 `_milvuslite_store.py`）
   - 如何保证文档 ID 的唯一性？
   - id_type 参数的作用是什么？

### 10.2 进阶题

5. **设计一个新的向量存储实现**
   - 参考 VDBStoreBase 接口
   - 实现 ChromaDB 向量存储

6. **分析 SQLAlchemy 异步实现的优缺点**
   - 对比 InMemoryMemory 和 AsyncSQLAlchemyMemory

7. **比较不同向量存储实现**
   - Milvus Lite、Qdrant 的适用场景差异

8. **分析 Mem0 的三级降级策略**
   - 为什么需要降级策略？
   - 每级策略的适用场景是什么？

### 10.3 挑战题

9. **实现一个混合记忆系统**
   - 结合工作记忆和长期记忆的优势
   - 实现自动记忆压缩和归档

10. **设计增量更新机制**
    - 当知识库文档更新时高效更新向量索引

11. **分析 ReMe 与 Mem0 的异同**
    - 两种长期记忆实现的设计理念对比

12. **扩展文档读取器**
    - 实现一个新的文档格式读取器（如 MarkdownReader）

---

## 参考答案

### 10.1 基础题

**第1题：KnowledgeBase 基类设计**

核心属性：`_embedding_model`（Embedding 模型实例）、`_chunk_size`（分块大小）、`_chunk_overlap`（重叠大小）。`retrieve_knowledge` 是面向用户的高级接口，支持 query + top_k 参数；`retrieve` 是内部检索接口，由子类实现具体向量匹配逻辑。

**第2题：Document 数据结构**

Document 包含 `content`（文本内容）和 `metadata`（DocMetadata 实例）。DocMetadata 存储 `doc_id`、`chunk_id`、`source`、`created_at` 等。检索时 `score`（相似度分数）和 `chunk_id` 会被填充返回。

**第3题：TextReader 分词策略**

- `split_by="sentence"`: 按句号等标点切分，粒度最细
- `split_by="paragraph"`: 按空行切分，保持段落完整性
- `split_by="character"`: 按固定字符数切分
当段落超过 chunk_size 时，递归切分为更小的块直到满足大小限制。

**第4题：MilvusLiteStore ID 生成**

使用 `uuid4()` 或基于内容的哈希生成 ID。`id_type` 参数控制策略：`"uuid"` 随机生成，`"hash"` 基于内容确定性生成（相同内容相同 ID，支持去重）。

### 10.2 进阶题

**第5题：ChromaDB 向量存储**

```python
class ChromaDBStore(VDBStoreBase):
    def __init__(self, collection_name, embedding_model, **kwargs):
        super().__init__(embedding_model, **kwargs)
        import chromadb
        self.client = chromadb.Client()
        self.collection = self.client.get_or_create_collection(collection_name)

    async def _store_nodes(self, nodes):
        embeddings = await self._embedding_model([n.content for n in nodes])
        self.collection.add(
            ids=[n.metadata.chunk_id for n in nodes],
            embeddings=embeddings,
            documents=[n.content for n in nodes],
        )

    async def _retrieve_nodes(self, query, top_k):
        query_emb = await self._embedding_model([query])
        results = self.collection.query(query_embeddings=query_emb, n_results=top_k)
        return results
```

**第6题：InMemory vs AsyncSQLAlchemy**

InMemoryMemory 优势：零配置、测试友好、无外部依赖。劣势：进程退出即丢失、无持久化、不支持并发写入。
AsyncSQLAlchemy 优势：持久化存储、支持并发、支持大型记忆库。劣势：需要数据库、配置复杂、异步调试困难。

**第8题：Mem0 三级降级**

1. **精确匹配**: 直接从 mem0_gin 索引查找已有记忆
2. **语义搜索**: 使用 Embedding + pgvector 向量相似度检索
3. **LLM 提取**: 当以上两种都无结果时，用 LLM 从对话中提取新记忆
降级策略确保在向量索引不完善时仍能获取有效记忆。

### 10.3 挑战题

**第9题：混合记忆系统**

```python
class HybridMemory:
    def __init__(self, working_memory, long_term_memory, model, threshold=50):
        self.working = working_memory      # InMemoryMemory
        self.long_term = long_term_memory  # AsyncSQLAlchemyMemory
        self.model = model
        self.threshold = threshold

    async def add(self, msg):
        await self.working.add(msg)
        if len(self.working.get_memory()) > self.threshold:
            await self._archive_old_messages()

    async def _archive_old_messages(self):
        msgs = self.working.get_memory()
        summary = await self.model(Msg("system", f"总结以下对话: {msgs}"))
        await self.long_term.add(summary)
        # 保留最近 10 条
        for m in msgs[:-10]:
            await self.working.delete(m.id)
```

**第12题：MarkdownReader**

```python
class MarkdownReader(DocumentReaderBase):
    def read(self, file_path: str) -> list[Document]:
        with open(file_path) as f:
            content = f.read()
        # 按标题切分
        sections = re.split(r'(^#{1,6}\s+.+$)', content, flags=re.MULTILINE)
        return [Document(content=s.strip(), metadata=DocMetadata(source=file_path))
                for s in sections if s.strip()]
```

---

## 小结

| 组件 | 类型 | 核心功能 |
|------|------|----------|
| InMemoryMemory | 工作记忆 | 零配置内存存储，适合测试 |
| AsyncSQLAlchemyMemory | 工作记忆 | 持久化数据库存储，支持并发 |
| Mem0LongTermMemory | 长期记忆 | pgvector 语义检索 + 三级降级 |
| ReMeLongTermMemory | 长期记忆 | 反思记忆 + 渐进式反思 |
| SimpleKnowledge | RAG 知识库 | 向量存储 + 文档分块 + 检索 |

## 章节关联

| 关联模块 | 关联点 |
|----------|--------|
| [智能体模块](module_agent_deep.md) | Agent 的 `observe()` 触发记忆存储，Hook 驱动压缩 |
| [模型模块](module_model_deep.md) | Embedding 模型生成向量，Token 计数控制压缩阈值 |
| [工具模块](module_tool_mcp_deep.md) | RAG 检索工具注册为 MCP 工具 |

---

## 参考资料

- Memory 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/memory/`
- RAG 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/rag/`

---

*文档版本: 1.1*
*最后更新: 2026-04-28*
*基于源码版本: agentscope-1.0.19*
