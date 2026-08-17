"""DeerFlow 历史消息分页端点单测（threads.py /messages/page）。

覆盖：与 deer-flow 前端契约一致——data 升序（旧→新）、has_more、
next_before_seq 向后游标翻页、空会话容错、content 携带 id 供前端去重、
limit/before_seq 参数校验。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.message import Msg, TextBlock

from bocomadp.deerflow.routers.threads import threads_router


class FakeStorage:
    """模拟 AsyncSQLAlchemyStorage.list_messages 的游标分页语义。

    before 为消息 id 游标：返回该消息之前（不含）的消息；未知游标返回空。
    """

    def __init__(self, messages: list[Msg]) -> None:
        self._messages = list(messages)  # 正序（旧→新）

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        limit: int = 50,
        before: str | None = None,
        **kwargs: Any,
    ) -> tuple[list[Msg], bool]:
        rows = self._messages
        if before is not None:
            index = next(
                (i for i, m in enumerate(rows) if m.id == before), -1)
            rows = rows[:index] if index >= 0 else []
        page = rows[-limit:]
        return page, len(rows) > limit


def _make_app(messages: list[Msg]) -> FastAPI:
    # 与 main.py 一致：router 挂到子应用，再 mount 到 /api（对外路径不变）；
    # state 必须设在子应用上（挂载后 request.app 是子应用）。
    api = FastAPI()
    api.state.storage = FakeStorage(messages)
    api.include_router(threads_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def _msg(index: int) -> Msg:
    """构造一条消息：user 奇数 / assistant 偶数，文本内容带序号。"""
    role = "user" if index % 2 else "assistant"
    return Msg(name="test", role=role, content=[TextBlock(text=f"message-{index}")])


def _seqs(data: list[dict[str, Any]]) -> list[int]:
    return [row["seq"] for row in data]


def test_page_returns_latest_limit_ascending() -> None:
    messages = [_msg(i) for i in range(1, 7)]  # 6 条
    with TestClient(_make_app(messages)) as client:
        response = client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=3")

    assert response.status_code == 200
    body = response.json()
    assert _seqs(body["data"]) == [4, 5, 6]  # 升序，最新在前页
    assert body["has_more"] is True
    assert body["next_before_seq"] == 4


def test_page_before_seq_advances_backward() -> None:
    messages = [_msg(i) for i in range(1, 7)]
    with TestClient(_make_app(messages)) as client:
        response = client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=3&before_seq=4")

    body = response.json()
    assert _seqs(body["data"]) == [1, 2, 3]
    assert body["has_more"] is False
    assert body["next_before_seq"] is None


def test_page_exact_limit_has_no_more() -> None:
    messages = [_msg(i) for i in range(1, 3)]
    with TestClient(_make_app(messages)) as client:
        response = client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=2")

    body = response.json()
    assert _seqs(body["data"]) == [1, 2]
    assert body["has_more"] is False
    assert body["next_before_seq"] is None


def test_page_empty_session() -> None:
    with TestClient(_make_app([])) as client:
        response = client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=2")

    assert response.status_code == 200
    assert response.json() == {
        "data": [], "has_more": False, "next_before_seq": None}


def test_page_row_carries_content_id_for_dedupe() -> None:
    messages = [_msg(1)]
    with TestClient(_make_app(messages)) as client:
        response = client.get(
            "/api/deerflow/threads/thread-1/messages/page")

    row = response.json()["data"][0]
    assert row["run_id"] == row["content"]["id"]  # 前端以 content.id 去重
    assert row["content"]["type"] == "human"
    assert row["metadata"] == {"caller": ""}
    assert row["created_at"]


def test_page_rejects_invalid_params() -> None:
    messages = [_msg(i) for i in range(1, 4)]
    with TestClient(_make_app(messages)) as client:
        assert client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=0").status_code == 422
        assert client.get(
            "/api/deerflow/threads/thread-1/messages/page?limit=201").status_code == 422
        assert client.get(
            "/api/deerflow/threads/thread-1/messages/page?before_seq=0").status_code == 422
