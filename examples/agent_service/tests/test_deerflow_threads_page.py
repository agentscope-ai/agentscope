"""线程消息分页端点单测（threads.py 的 ``/messages/page``）。

覆盖：最新页、before_seq 向后翻页、中间件消息过滤、after_seq 422、
空会话、分页边界（has_more / next_before_seq）与 RunMessage 转换。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from agentscope.message import Msg, TextBlock
from bocomadp.deerflow.routers.threads import list_thread_messages_page

THREAD_ID = "thread-1"
USER_ID = "user-1"


class FakeStorage:
    """内存版 storage.list_messages：按 (created_at, msg_id) 字典序取更早页。

    与 SQL 后端的 before 游标语义一致（strictly older，升序返回）。
    """

    def __init__(self, messages: list[Msg]):
        self._messages = sorted(
            messages,
            key=lambda m: (m.created_at, m.id),
        )  # 旧 → 新

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        limit: int = 50,
        before: str | None = None,
        **kwargs: object,
    ) -> tuple[list[Msg], bool]:
        del user_id, session_id, kwargs
        rows = self._messages
        if before is not None:
            rows = [
                m
                for m in rows
                if (m.created_at, m.id) < self._cursor(before)
            ]
        page = rows[-limit:]
        return page, len(rows) > limit

    def _cursor(self, msg_id: str) -> tuple[str, str]:
        for m in self._messages:
            if m.id == msg_id:
                return m.created_at, m.id
        raise KeyError(msg_id)


def make_msg(role: str, text: str, i: int, caller: str | None = None) -> Msg:
    """构造第 i 条消息：id/created_at 单调递增，顺序即会话顺序。"""
    metadata = {"caller": caller} if caller else {}
    return Msg(
        name="tester",
        role=role,  # type: ignore[arg-type]
        content=[TextBlock(text=text)],
        id=f"msg_{i:04d}",
        created_at=f"2026-01-01T00:00:{i:02d}",
        metadata=metadata,
    )


def make_thread(n: int, middleware: set[int] | None = None) -> list[Msg]:
    """构造 n 条交替 user/assistant 消息；middleware 为中间件消息下标。"""
    middleware = middleware or set()
    return [
        make_msg(
            "user" if i % 2 == 0 else "assistant",
            f"msg-{i}",
            i,
            caller="middleware:audit" if i in middleware else None,
        )
        for i in range(1, n + 1)
    ]


async def _fetch(
    storage: FakeStorage,
    *,
    limit: int = 50,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> dict:
    return await list_thread_messages_page(
        THREAD_ID,
        limit=limit,
        before_seq=before_seq,
        after_seq=after_seq,
        user_id=USER_ID,
        storage=storage,
    )


def fetch(
    storage: FakeStorage,
    *,
    limit: int = 50,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> dict:
    """同步包装：与现有测试风格一致（asyncio.run）。"""
    return asyncio.run(
        _fetch(storage, limit=limit, before_seq=before_seq, after_seq=after_seq),
    )


def test_latest_page() -> None:
    """不带游标返回最新一页：seq 升序、has_more、next_before_seq。"""
    resp = fetch(FakeStorage(make_thread(10)), limit=3)
    assert resp["has_more"] is True
    assert resp["next_before_seq"] == 8
    assert [row["seq"] for row in resp["data"]] == [8, 9, 10]


def test_before_seq_backward_page() -> None:
    """before_seq 返回 seq < 游标 的最近一页（旧→新升序）。"""
    resp = fetch(FakeStorage(make_thread(10)), limit=3, before_seq=8)
    assert resp["has_more"] is True
    assert resp["next_before_seq"] == 5
    assert [row["seq"] for row in resp["data"]] == [5, 6, 7]


def test_full_page_when_limit_covers_all() -> None:
    """limit 覆盖全部消息：has_more=False 且无游标。"""
    resp = fetch(FakeStorage(make_thread(4)), limit=50)
    assert resp["has_more"] is False
    assert resp["next_before_seq"] is None
    assert [row["seq"] for row in resp["data"]] == [1, 2, 3, 4]


def test_two_page_roundtrip() -> None:
    """两页往返：第一页游标翻页后精确取到剩余消息。"""
    storage = FakeStorage(make_thread(4))
    first = fetch(storage, limit=2)
    assert first["has_more"] is True
    assert first["next_before_seq"] == 3
    second = fetch(storage, limit=2, before_seq=first["next_before_seq"])
    assert second["has_more"] is False
    assert second["next_before_seq"] is None
    assert [row["seq"] for row in second["data"]] == [1, 2]


def test_middleware_messages_filtered() -> None:
    """中间件消息不进历史页，且不占用 seq 编号。"""
    # 10 条消息中第 3、7 条是中间件消息 → 可见 8 条
    storage = FakeStorage(make_thread(10, middleware={3, 7}))
    resp = fetch(storage, limit=50)
    assert resp["has_more"] is False
    assert [row["seq"] for row in resp["data"]] == list(range(1, 9))
    assert all(
        row["metadata"]["caller"] != "middleware:audit"
        for row in resp["data"]
    )


def test_after_seq_rejected() -> None:
    """after_seq 不受支持 → 422（与 deer-flow 行为一致）。"""
    with pytest.raises(HTTPException) as exc:
        fetch(FakeStorage(make_thread(3)), after_seq=1)
    assert exc.value.status_code == 422


def test_empty_session_returns_empty_page() -> None:
    """会话不存在/无消息：空页而非 404（thread 懒创建是正常态）。"""
    resp = fetch(FakeStorage([]))
    assert resp == {"data": [], "has_more": False, "next_before_seq": None}


def test_run_message_shape() -> None:
    """RunMessage 转换：role 映射、稳定 id、run_id 取 thread_id。"""
    resp = fetch(FakeStorage(make_thread(2)), limit=50)
    first, second = resp["data"]
    assert first["run_id"] == "msg_0001"  # run_id 取原生 msg id
    assert first["content"]["type"] == "ai"  # i=1 assistant → ai
    assert first["content"]["id"] == "msg_0001"
    assert first["content"]["content"][0]["text"] == "msg-1"
    assert second["content"]["type"] == "human"  # i=2 user → human
    assert second["content"]["id"] == "msg_0002"
    assert second["created_at"] == "2026-01-01T00:00:02"
