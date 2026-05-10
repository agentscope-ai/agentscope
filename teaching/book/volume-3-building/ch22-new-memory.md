# 第二十二章：造一个新 Memory Backend——SQLite 原生异步记忆后端

**难度**：中级

> 第十章我们追踪了 MemoryBase 的接口和 InMemoryMemory 的实现。本章你将从零写出一个基于 `aiosqlite` 的 Memory 后端——不用 SQLAlchemy，直接操作 SQL。写完之后，你会对 MemoryBase 的抽象方法、mark 系统、错误处理有完整的实战经验。

---

## 1. 实战目标

完成本章后，你将：

1. 理解 `MemoryBase`（`_base.py` 第 11-168 行）的核心方法契约
2. 用 `aiosqlite` 实现 `SQLiteMemory`，覆盖全部五个抽象方法
3. 实现 `delete_by_mark` 和 `update_messages_mark`
4. 编写与 `tests/memory_test.py` 结构一致的单元测试
5. 在 ReActAgent 中完成端到端集成验证

---

## 2. 第一步：最小可用版本

### 2.1 MemoryBase 接口速查

`MemoryBase`（`_base.py` 第 11 行）继承自 `StateModule`，定义了五个抽象方法和两个可选方法：

| 方法 | 行号 | 必须 | 用途 |
|------|------|------|------|
| `add` | 第 32 行 | 是 | 添加消息，支持单条/批量 |
| `delete` | 第 50 行 | 是 | 按 ID 删除 |
| `size` | 第 92 行 | 是 | 返回消息数量 |
| `clear` | 第 101 行 | 是 | 清空所有消息 |
| `get_memory` | 第 105 行 | 是 | 按条件查询消息 |
| `delete_by_mark` | 第 66 行 | 否 | 按 mark 删除 |
| `update_messages_mark` | 第 134 行 | 否 | 增删改 mark |

构造函数（第 14-20 行）初始化 `_compressed_summary` 并注册到序列化系统。

### 2.2 表结构

两张表，对应 `AsyncSQLAlchemyMemory`（`_sqlalchemy_memory.py` 第 44-89 行）的 ORM 模型，但不引入 user/session 多租户：

```sql
CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY, msg_json TEXT NOT NULL, idx INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS message_mark (
    msg_id TEXT NOT NULL, mark TEXT NOT NULL,
    PRIMARY KEY (msg_id, mark),
    FOREIGN KEY (msg_id) REFERENCES message(id) ON DELETE CASCADE
);
```

### 2.3 最小实现

创建 `src/agentscope/memory/_working_memory/_sqlite_memory.py`：

```python
# -*- coding: utf-8 -*-
"""A lightweight SQLite memory backend using aiosqlite."""
import asyncio
import json
from typing import Any

import aiosqlite

from ...message import Msg
from ._base import MemoryBase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY, msg_json TEXT NOT NULL, idx INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS message_mark (
    msg_id TEXT NOT NULL, mark TEXT NOT NULL,
    PRIMARY KEY (msg_id, mark),
    FOREIGN KEY (msg_id) REFERENCES message(id) ON DELETE CASCADE);
"""


class SQLiteMemory(MemoryBase):
    """A SQLite-backed memory using aiosqlite.

    Args:
        db_path (`str`, defaults to `":memory:"`):
            Path to the SQLite database file.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        super().__init__()
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Lazily create the connection and tables."""
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.executescript(_SCHEMA)
            await self._db.commit()
        return self._db

    async def add(
        self,
        memories: Msg | list[Msg] | None,
        marks: str | list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Add message(s) into the SQLite storage."""
        if memories is None:
            return
        if isinstance(memories, Msg):
            memories = [memories]
        if marks is None:
            marks = []
        elif isinstance(marks, str):
            marks = [marks]

        db = await self._ensure_db()
        async with self._lock:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(idx), -1) + 1 FROM message")
            next_idx = (await cursor.fetchone())[0]

            for i, msg in enumerate(memories):
                row = await (
                    await db.execute("SELECT 1 FROM message WHERE id=?",
                                     (msg.id,))
                ).fetchone()
                if row:
                    continue
                await db.execute(
                    "INSERT INTO message VALUES (?, ?, ?)",
                    (msg.id, json.dumps(msg.to_dict()), next_idx + i))
                for m in marks:
                    await db.execute(
                        "INSERT OR IGNORE INTO message_mark VALUES (?, ?)",
                        (msg.id, m))
            await db.commit()

    async def delete(self, msg_ids: list[str], **kwargs: Any) -> int:
        """Remove message(s) by their IDs."""
        if not msg_ids:
            return 0
        db = await self._ensure_db()
        async with self._lock:
            ph = ",".join("?" * len(msg_ids))
            cur = await db.execute(
                f"DELETE FROM message WHERE id IN ({ph})", msg_ids)
            await db.commit()
            return cur.rowcount

    async def get_memory(
        self,
        mark: str | None = None,
        exclude_mark: str | None = None,
        prepend_summary: bool = True,
        **kwargs: Any,
    ) -> list[Msg]:
        """Get messages from the SQLite storage."""
        db = await self._ensure_db()
        if mark is not None:
            query = ("SELECT m.msg_json FROM message m "
                     "JOIN message_mark mk ON m.id=mk.msg_id "
                     "WHERE mk.mark=? ORDER BY m.idx")
            params: list[Any] = [mark]
        else:
            query = "SELECT msg_json FROM message ORDER BY idx"
            params = []
        rows = (await (await db.execute(query, params)).fetchall())

        if exclude_mark is not None:
            ex = {r[0] for r in await (
                await db.execute(
                    "SELECT msg_id FROM message_mark WHERE mark=?",
                    (exclude_mark,))
            ).fetchall()}
            rows = [r for r in rows if json.loads(r[0])["id"] not in ex]

        msgs = [Msg.from_dict(json.loads(r[0])) for r in rows]
        if prepend_summary and self._compressed_summary:
            return [Msg("user", self._compressed_summary, "user"), *msgs]
        return msgs

    async def size(self) -> int:
        """Get the number of messages."""
        db = await self._ensure_db()
        return (await (await db.execute(
            "SELECT COUNT(*) FROM message")).fetchone())[0]

    async def clear(self) -> None:
        """Clear all messages."""
        db = await self._ensure_db()
        async with self._lock:
            await db.execute("DELETE FROM message_mark")
            await db.execute("DELETE FROM message")
            await db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
```

要点：`_ensure_db` 延迟建表（对应 `_sqlalchemy_memory.py` 第 226 行的 `_create_table`）；`_lock` 保护写操作（对应第 176 行的 `_write_session`）；`INSERT OR IGNORE` 去重与 `InMemoryMemory`（`_in_memory_memory.py` 第 130 行）用集合去重逻辑等价。

---

## 3. 第二步：注册并测试

### 3.1 注册到模块

编辑 `_working_memory/__init__.py` 添加 `from ._sqlite_memory import SQLiteMemory`，编辑 `memory/__init__.py` 在 `__all__` 中添加 `"SQLiteMemory"`。

### 3.2 单元测试

创建 `tests/sqlite_memory_test.py`，参照 `tests/memory_test.py`（第 17-501 行）中 `ShortTermMemoryTest` 的模式：

```python
# -*- coding: utf-8 -*-
"""Tests for the SQLite memory backend."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase
from agentscope.memory import SQLiteMemory
from agentscope.message import Msg


class SQLiteMemoryTest(IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self.msgs = [Msg("user", str(i), "user") for i in range(10)]
        for i, msg in enumerate(self.msgs):
            msg.id = str(i)
        _, self.db_path = tempfile.mkstemp(suffix=".db")
        self.memory = SQLiteMemory(db_path=self.db_path)

    async def test_basic(self) -> None:
        self.assertEqual(await self.memory.size(), 0)
        await self.memory.add(self.msgs[:5])
        self.assertEqual(await self.memory.size(), 5)
        ids = [m.id for m in await self.memory.get_memory()]
        self.assertEqual(ids, ["0", "1", "2", "3", "4"])

    async def test_delete(self) -> None:
        await self.memory.add(self.msgs[:5])
        self.assertEqual(await self.memory.delete(msg_ids=["2", "4"]), 2)
        self.assertEqual(
            [m.id for m in await self.memory.get_memory()], ["0", "1", "3"])

    async def test_delete_nonexistent(self) -> None:
        await self.memory.add(self.msgs[:5])
        self.assertEqual(await self.memory.delete(msg_ids=["nope"]), 0)

    async def test_clear_and_dedup(self) -> None:
        await self.memory.add(self.msgs[:8])
        await self.memory.add(self.msgs[5:])  # 重叠 ID 被跳过
        self.assertEqual(await self.memory.size(), 10)
        await self.memory.clear()
        self.assertEqual(await self.memory.size(), 0)

    async def test_summary(self) -> None:
        await self.memory.update_compressed_summary("hi")
        await self.memory.add(self.msgs[:3])
        self.assertEqual(len(await self.memory.get_memory()), 4)
        self.assertEqual(
            len(await self.memory.get_memory(prepend_summary=False)), 3)

    async def asyncTearDown(self) -> None:
        await self.memory.close()
        if hasattr(self, "db_path"):
            os.unlink(self.db_path)
```

运行：`pytest tests/sqlite_memory_test.py -v`

---

## 4. 第三步：进阶功能——mark 系统

### 4.1 实现 delete_by_mark 和 update_messages_mark

在 `SQLiteMemory` 类中添加：

```python
    async def delete_by_mark(
        self, mark: str | list[str], **kwargs: Any,
    ) -> int:
        """Remove messages by their marks."""
        if isinstance(mark, str):
            mark = [mark]
        db = await self._ensure_db()
        async with self._lock:
            ph = ",".join("?" * len(mark))
            ids = [r[0] for r in await (
                await db.execute(
                    f"SELECT DISTINCT msg_id FROM message_mark "
                    f"WHERE mark IN ({ph})", mark)
            ).fetchall()]
            if not ids:
                return 0
            mph = ",".join("?" * len(ids))
            await db.execute(
                f"DELETE FROM message_mark WHERE msg_id IN ({mph})", ids)
            cur = await db.execute(
                f"DELETE FROM message WHERE id IN ({mph})", ids)
            await db.commit()
            return cur.rowcount

    async def update_messages_mark(
        self, new_mark: str | None, old_mark: str | None = None,
        msg_ids: list[str] | None = None,
    ) -> int:
        """Update marks on messages."""
        db = await self._ensure_db()
        async with self._lock:
            if msg_ids is not None:
                targets = msg_ids
            else:
                targets = [r[0] for r in await (
                    await db.execute("SELECT id FROM message")).fetchall()]
            if old_mark is not None:
                marked = {r[0] for r in await (
                    await db.execute(
                        "SELECT msg_id FROM message_mark WHERE mark=?",
                        (old_mark,))
                ).fetchall()}
                targets = [t for t in targets if t in marked]
            if not targets:
                return 0
            count = 0
            for mid in targets:
                if new_mark is None:
                    if old_mark is not None:
                        await db.execute(
                            "DELETE FROM message_mark "
                            "WHERE msg_id=? AND mark=?", (mid, old_mark))
                        count += 1
                else:
                    if old_mark is not None:
                        await db.execute(
                            "DELETE FROM message_mark "
                            "WHERE msg_id=? AND mark=?", (mid, old_mark))
                    await db.execute(
                        "INSERT OR IGNORE INTO message_mark VALUES (?, ?)",
                        (mid, new_mark))
                    count += 1
            await db.commit()
            return count
```

### 4.2 mark 测试

在 `SQLiteMemoryTest` 中追加：

```python
    async def test_marks(self) -> None:
        await self.memory.add(self.msgs[:5])
        await self.memory.add(self.msgs[5:7], marks=["important", "todo"])
        await self.memory.add(self.msgs[7:], marks="important")

        imp = await self.memory.get_memory(mark="important")
        self.assertEqual([m.id for m in imp], [str(i) for i in range(5, 10)])
        todo = await self.memory.get_memory(mark="todo")
        self.assertEqual([m.id for m in todo], ["5", "6"])
        no_todo = await self.memory.get_memory(exclude_mark="todo")
        self.assertEqual(
            [m.id for m in no_todo],
            [str(i) for i in [0, 1, 2, 3, 4, 7, 8, 9]])

    async def test_update_mark(self) -> None:
        await self.memory.add(self.msgs[:5])
        await self.memory.update_messages_mark(
            new_mark="review", msg_ids=["0", "1", "2"])
        self.assertEqual(
            [m.id for m in await self.memory.get_memory(mark="review")],
            ["0", "1", "2"])
        await self.memory.update_messages_mark(
            new_mark="done", old_mark="review", msg_ids=["0", "1"])
        self.assertEqual(await self.memory.get_memory(mark="review"), [])
        self.assertEqual(
            [m.id for m in await self.memory.get_memory(mark="done")],
            ["0", "1"])

    async def test_delete_by_mark(self) -> None:
        await self.memory.add(self.msgs[:5])
        await self.memory.add(self.msgs[5:], marks="important")
        self.assertEqual(await self.memory.delete_by_mark("important"), 5)
        self.assertEqual(
            [m.id for m in await self.memory.get_memory()],
            [str(i) for i in range(5)])
```

---

## 5. 第四步：错误处理

SQLite 常见异常：`OperationalError`（表不存在）、`IntegrityError`（主键冲突）、`DatabaseError`（文件损坏）。参考 `AsyncSQLAlchemyMemory`（`_sqlalchemy_memory.py` 第 176-184 行）的 `_write_session`，添加错误包装：

```python
    async def _write_op(self, coro):
        """Execute a write with rollback on error."""
        db = await self._ensure_db()
        async with self._lock:
            try:
                result = await coro
                await db.commit()
                return result
            except aiosqlite.Error as e:
                await db.rollback()
                raise RuntimeError(
                    f"Database error in {self.__class__.__name__}: {e}"
                ) from e
```

然后用 `_write_op` 替换各方法中手动的 `async with self._lock` + `commit`。测试：

```python
    async def test_errors(self) -> None:
        await self.memory.close()
        with self.assertRaises(RuntimeError):
            await self.memory.add(self.msgs[0])
        mem = SQLiteMemory(db_path="/nonexistent/dir/test.db")
        with self.assertRaises(RuntimeError):
            await mem.add(self.msgs[0])
```

---

## 6. 第五步：集成测试

```python
import asyncio, os, sqlite3, tempfile
from agentscope.agents import ReActAgent
from agentscope.memory import SQLiteMemory
from agentscope.models import ModelManager, ModelConfig
from agentscope.message import Msg


async def test_with_agent() -> None:
    _, db_path = tempfile.mkstemp(suffix=".db")
    memory = SQLiteMemory(db_path=db_path)
    ModelManager.add_model_config(ModelConfig(
        config_name="test_model", model_type="openai_chat",
        model_name="gpt-4o-mini"))
    agent = ReActAgent(name="assistant", model_config_name="test_model")
    agent.memory = memory  # 替换默认 InMemoryMemory

    await agent.memory.add(Msg("user", "你好", "user"))
    await agent.memory.add(Msg("assistant", "你好！有什么可以帮你的？", "assistant"))
    assert len(await agent.memory.get_memory()) == 2

    await agent.memory.close()
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 2
    conn.close()
    os.unlink(db_path)

asyncio.run(test_with_agent())
```

多 Agent 共享数据库——通过 mark 隔离：

```python
async def test_shared_db() -> None:
    _, db_path = tempfile.mkstemp(suffix=".db")
    a, b = SQLiteMemory(db_path=db_path), SQLiteMemory(db_path=db_path)
    await a.add(Msg("user", "A", "user"), marks="agent_a")
    await b.add(Msg("user", "B", "user"), marks="agent_b")
    assert len(await a.get_memory(mark="agent_a")) == 1
    await a.close(); await b.close()
    os.unlink(db_path)
```

---

## 7. PR 检查清单

- [ ] 子类 `MemoryBase`，实现五个抽象方法
- [ ] `add` 处理 `None`、单条 `Msg`、`list[Msg]`（参考 `_in_memory_memory.py` 第 112-116 行）
- [ ] `get_memory` 支持 `mark`、`exclude_mark`、`prepend_summary`
- [ ] 写操作用 `asyncio.Lock` 保护，异常时 rollback
- [ ] 实现了 `delete_by_mark` 和 `update_messages_mark`
- [ ] 所有方法有 Google 风格 docstring
- [ ] 通过 `pytest tests/ -v` 和 `pre-commit run --all-files`
- [ ] 在 `__init__.py` 中注册新类

---

## 8. 下一章预告

下一章我们将造一个 RAG 管道——从文档读取、向量化、检索到生成，把知识库接入 AgentScope 的 RAG 模块。
