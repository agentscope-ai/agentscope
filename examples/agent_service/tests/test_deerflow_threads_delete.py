"""DeerFlow 删除会话（DELETE /threads/{tid}）端点单测。

覆盖：定位归属 agent 后删除 session、删除前中断活跃 run（RunManager
置 interrupted + 原生 interrupt）、未找到会话时幂等返回成功——对齐
deer-flow 原 ``ThreadDeleteResponse``（success / message）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bocomadp.deerflow.routers import threads as threads_module
from bocomadp.deerflow.routers.threads import threads_router

AGENT_ID = "test-agent"


class FakeStorage:
    """模拟 storage：get_session 命中归属 agent，delete_session 记录调用。"""

    def __init__(self, session_exists: bool = True) -> None:
        self._session_exists = session_exists
        self.deleted: list[tuple[str, str, str]] = []

    async def get_session(self, user_id: str, agent_id: str, session_id: str):
        del user_id
        if self._session_exists and agent_id == AGENT_ID:
            return SimpleNamespace(state=SimpleNamespace(context=[]))
        return None

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        del user_id
        self.deleted.append((agent_id, session_id))
        return self._session_exists


class FakeChatService:
    """记录 interrupt 调用（无活跃 run 场景下不抛异常）。"""

    def __init__(self) -> None:
        self.interrupt_calls: list[tuple[str, str, str]] = []

    async def interrupt(self, user_id: str, session_id: str, agent_id: str):
        self.interrupt_calls.append((user_id, session_id, agent_id))


class FakeRunManager:
    """模拟 RunManager：get_by_session 返回活跃/无 run 两种情形。"""

    def __init__(self, active_record=None) -> None:
        self._record = active_record
        self.finished: list[tuple[str, str]] = []

    def get_by_session(self, session_id: str):
        del session_id
        return self._record

    def mark_finished(self, run_id: str, status: Any) -> None:
        self.finished.append((run_id, str(status)))


def _make_app(
    storage: FakeStorage,
    chat_service: FakeChatService,
    run_manager: FakeRunManager,
) -> FastAPI:
    # 与 main.py 一致：router 挂到子应用，再 mount 到 /api（对外路径不变）；
    # state 必须设在子应用上（挂载后 request.app 是子应用）。
    api = FastAPI()
    api.state.storage = storage
    api.state.chat_service = chat_service
    api.state.run_manager = run_manager
    api.include_router(threads_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def _patch_agents(monkeypatch) -> None:
    """固定 agent 遍历列表与默认 agent，不依赖真实 config.yaml。"""
    monkeypatch.setattr(threads_module, "load_agents_from_yaml", lambda: [])
    monkeypatch.setattr(threads_module, "_default_agent_id", lambda: AGENT_ID)


def test_delete_thread_removes_session(monkeypatch) -> None:
    storage = FakeStorage(session_exists=True)
    chat_service = FakeChatService()
    run_manager = FakeRunManager()
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage, chat_service, run_manager)) as client:
        response = client.delete("/api/deerflow/threads/t1")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "thread t1 deleted",
    }
    assert storage.deleted == [(AGENT_ID, "t1")]


def test_delete_thread_interrupts_active_run(monkeypatch) -> None:
    """有活跃 run 时：RunManager 置 interrupted + 原生 interrupt。"""
    record = SimpleNamespace(run_id="run-1", active=True)
    storage = FakeStorage(session_exists=True)
    chat_service = FakeChatService()
    run_manager = FakeRunManager(active_record=record)
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage, chat_service, run_manager)) as client:
        response = client.delete("/api/deerflow/threads/t1")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert run_manager.finished == [("run-1", "interrupted")]
    assert chat_service.interrupt_calls == [("default", "t1", AGENT_ID)]
    assert storage.deleted == [(AGENT_ID, "t1")]


def test_delete_thread_skips_interrupt_without_active_run(monkeypatch) -> None:
    """无活跃 run：仍调用原生 interrupt（尽力而为），删除照常。"""
    storage = FakeStorage(session_exists=True)
    chat_service = FakeChatService()
    run_manager = FakeRunManager(active_record=None)
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage, chat_service, run_manager)) as client:
        response = client.delete("/api/deerflow/threads/t1")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert run_manager.finished == []  # 无 run 记录 → 不标记
    assert storage.deleted == [(AGENT_ID, "t1")]


def test_delete_thread_idempotent_when_missing(monkeypatch) -> None:
    """session 不存在：幂等返回成功，不抛 404。"""
    storage = FakeStorage(session_exists=False)
    chat_service = FakeChatService()
    run_manager = FakeRunManager()
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage, chat_service, run_manager)) as client:
        response = client.delete("/api/deerflow/threads/ghost")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "thread ghost not found",
    }
    assert storage.deleted == []
