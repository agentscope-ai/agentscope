"""DeerFlow 会话列表（search）端点单测。

左侧历史会话列表的数据源：storage session 记录聚合（thread_id ==
session_id），按 updated_at 降序 + offset/limit 分页，响应结构对齐
deer-flow 原 ``ThreadResponse``。覆盖：

- 降序排列与字段结构（thread_id / status / created_at / updated_at /
  metadata / values.title / interrupts）
- 分页（offset/limit）
- 标题取最近一条用户消息（截断 50 字符），无消息时回退 config.name
- 空会话容错
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.message import Msg, TextBlock

from bocomadp.deerflow.routers import threads as threads_module
from bocomadp.deerflow.routers.threads import threads_router

AGENT_ID = "test-agent"
BASE = datetime(2026, 8, 14, 10, 0, 0)


def _record(
    thread_id: str,
    updated_at: datetime,
    user_text: str | None = None,
    config_name: str = "Untitled",
):
    """构造一条 session 记录（仅 search 端点用到的字段）。"""
    state_ctx = []
    if user_text:
        state_ctx.append(
            Msg(
                name="user",
                role="user",
                content=[TextBlock(text=user_text)],
                id=f"{thread_id}-u1",
            ),
        )
    return SimpleNamespace(
        id=thread_id,
        created_at=updated_at - timedelta(minutes=1),
        updated_at=updated_at,
        state=SimpleNamespace(context=state_ctx),
        config=SimpleNamespace(name=config_name),
    )


class FakeStorage:
    """模拟 storage.list_sessions（(user, agent) 分片 → session 列表）。"""

    def __init__(self, sessions_by_agent: dict[str, list]) -> None:
        self._sessions = sessions_by_agent

    async def list_sessions(self, user_id: str, agent_id: str):
        del user_id
        return self._sessions.get(agent_id, [])


def _make_app(storage: FakeStorage) -> FastAPI:
    # 与 main.py 一致：router 挂到子应用，再 mount 到 /api（对外路径不变）；
    # state 必须设在子应用上（挂载后 request.app 是子应用）。
    api = FastAPI()
    api.state.storage = storage
    api.include_router(threads_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def _patch_agents(monkeypatch) -> None:
    """固定 agent 遍历列表与默认 agent，不依赖真实 config.yaml。"""
    monkeypatch.setattr(threads_module, "load_agents_from_yaml", lambda: [])
    monkeypatch.setattr(threads_module, "_default_agent_id", lambda: AGENT_ID)


def _search(client: TestClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    response = client.post("/api/deerflow/threads/search", json=body)
    assert response.status_code == 200
    return response.json()


def test_search_lists_sessions_newest_first(monkeypatch) -> None:
    storage = FakeStorage(
        {
            AGENT_ID: [
                _record("t-old", BASE - timedelta(hours=2), "老会话"),
                _record("t-new", BASE, "新会话"),
                _record("t-mid", BASE - timedelta(hours=1), "中间会话"),
            ],
        },
    )
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        threads = _search(client, {"limit": 10})

    assert [t["thread_id"] for t in threads] == ["t-new", "t-mid", "t-old"]
    first = threads[0]
    assert first["status"] == "idle"
    assert first["values"] == {"title": "新会话"}
    assert first["metadata"] == {}
    assert first["interrupts"] == {}
    # ISO 时间戳（前端直接展示 / 排序）
    assert first["updated_at"] == BASE.isoformat()
    assert first["created_at"] == (BASE - timedelta(minutes=1)).isoformat()


def test_search_pagination(monkeypatch) -> None:
    sessions = {
        AGENT_ID: [
            _record(f"t{i}", BASE - timedelta(minutes=i), f"会话{i}")
            for i in range(5)
        ],
    }
    storage = FakeStorage(sessions)
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        page1 = _search(client, {"limit": 2, "offset": 0})
        page2 = _search(client, {"limit": 2, "offset": 2})

    assert [t["thread_id"] for t in page1] == ["t0", "t1"]
    assert [t["thread_id"] for t in page2] == ["t2", "t3"]


def test_search_title_takes_last_user_message(monkeypatch) -> None:
    """标题取最近一条用户消息，而非更早的消息。"""
    record = _record("t1", BASE, None, "2026-08-14 10:00:00")
    record.state.context = [
        Msg(
            name="user",
            role="user",
            content=[TextBlock(text="第一条消息")],
            id="t1-u1",
        ),
        Msg(
            name="user",
            role="user",
            content=[TextBlock(text="最后一条消息")],
            id="t1-u2",
        ),
    ]
    storage = FakeStorage({AGENT_ID: [record]})
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        threads = _search(client, {"limit": 10})

    assert threads[0]["values"]["title"] == "最后一条消息"


def test_search_title_truncates_long_user_message(monkeypatch) -> None:
    storage = FakeStorage(
        {AGENT_ID: [_record("t1", BASE, "很" * 80)]},
    )
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        threads = _search(client, {"limit": 10})

    assert threads[0]["values"]["title"] == "很" * 50


def test_search_title_falls_back_to_config_name(monkeypatch) -> None:
    """无用户消息时回退 session 展示名（默认创建时间戳）。"""
    storage = FakeStorage(
        {
            AGENT_ID: [
                _record("t1", BASE, None, "2026-08-14 10:00:00"),
            ],
        },
    )
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        threads = _search(client, {"limit": 10})

    assert threads[0]["values"]["title"] == "2026-08-14 10:00:00"


def test_search_empty(monkeypatch) -> None:
    storage = FakeStorage({})
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        threads = _search(client, {})

    assert threads == []
