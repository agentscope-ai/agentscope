"""DeerFlow 确认卡片刷新恢复单测（threads.py ``_pending_confirm_cards``）。

确认卡片只在流式阶段以 messages 帧下发、不持久化；刷新页面后 session
仍 park 在 ASKING，消息列表里却没有卡片——用户无法完成确认。本模块
覆盖三个读端点（state / history / messages/page）按 session state 重建
卡片的行为：

- 存在 ASKING 工具调用时，卡片以 tool 消息追加到末尾（结构与流式
  翻译一致：``artifact.human_input.kind == "human_input_request"``）。
- suggested_rules 存在时追加"同意并始终允许"选项。
- 无待确认工具调用时不追加；messages/page 向后翻页（before_seq）时
  不追加（卡片只属于最新页）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolCallState
from agentscope.permission import PermissionRule
from agentscope.state import AgentState

from bocomadp.deerflow.routers import threads as threads_module
from bocomadp.deerflow.routers.threads import threads_router

AGENT_ID = "test-agent"


class FakeStorage:
    """模拟 storage：消息列表 + 带 ASKING 工具调用的 session state。

    ``get_agent`` 恒返回 None → ``_pending_confirm_cards`` 的 agent_name
    回退为 agent_id（AGENT_ID），与 state 中 assistant 消息的 name 对齐。
    """

    def __init__(self, messages: list[Msg], state: AgentState) -> None:
        self._messages = list(messages)  # 正序（旧→新）
        self._state = state

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

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ):
        del user_id, agent_id, session_id
        return SimpleNamespace(state=self._state)

    async def get_agent(self, user_id: str, agent_id: str):
        del user_id, agent_id
        return None


def _asking_state(extra: dict | None = None) -> AgentState:
    """最后一条 assistant 消息带 ASKING 的 Bash 工具调用。"""
    block_kwargs: dict[str, Any] = {
        "id": "call-1",
        "name": "Bash",
        "input": '{"command": "ps -ef"}',
        "state": ToolCallState.ASKING,
    }
    if extra:
        block_kwargs.update(extra)
    return AgentState(
        context=[
            Msg(
                name=AGENT_ID,
                role="assistant",
                content=[ToolCallBlock(**block_kwargs)],
                id="assistant-1",
            ),
        ],
    )


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


def _messages(*roles: str) -> list[Msg]:
    """构造逐条消息：user→human 文本 / assistant→ai 文本。"""
    msgs = []
    for idx, role in enumerate(roles, start=1):
        msgs.append(
            Msg(
                name=AGENT_ID,
                role=role,
                content=[TextBlock(text=f"message-{idx}")],
                id=f"m{idx}",
            ),
        )
    return msgs


def _assert_card(card: dict[str, Any]) -> None:
    """断言重建卡片与流式翻译结构一致（前端可重新渲染）。"""
    assert card["type"] == "tool"
    assert card["id"] == "confirm-call-1"
    # 前端 isClarificationToolMessage 只认该 name 才会渲染确认卡片
    assert card["name"] == "ask_clarification"
    assert card["tool_call_id"] == "call-1"
    artifact = card["artifact"]["human_input"]
    assert artifact["kind"] == "human_input_request"
    assert artifact["source"] == "agent_scope_permission"
    assert artifact["request_id"] == "confirm-call-1"
    assert artifact["tool_call_id"] == "call-1"
    assert "ps -ef" in artifact["question"]
    assert artifact["input_mode"] == "single_choice"


def test_state_endpoint_appends_rebuilt_card(monkeypatch) -> None:
    """state 端点：ASKING 会话的消息末尾追加重建卡片。"""
    storage = FakeStorage(_messages("user", "assistant"), _asking_state())
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        response = client.get("/api/deerflow/threads/t1/state")

    assert response.status_code == 200
    messages = response.json()["values"]["messages"]
    assert [m["type"] for m in messages] == ["human", "ai", "tool"]
    _assert_card(messages[-1])
    # 无 suggested_rules → 仅"同意执行 / 拒绝"两个选项
    options = messages[-1]["artifact"]["human_input"]["options"]
    assert [o["value"] for o in options] == ["confirm", "reject"]


def test_history_endpoint_appends_rebuilt_card(monkeypatch) -> None:
    """history 端点：checkpoint 的 values.messages 末尾追加重建卡片。"""
    storage = FakeStorage(_messages("user"), _asking_state())
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        response = client.post("/api/deerflow/threads/t1/history", json={})

    assert response.status_code == 200
    checkpoints = response.json()
    assert len(checkpoints) == 1
    messages = checkpoints[0]["values"]["messages"]
    assert [m["type"] for m in messages] == ["human", "tool"]
    _assert_card(messages[-1])


def test_messages_page_appends_card_on_latest_page(monkeypatch) -> None:
    """messages/page 最新页：末尾追加 confirm-card 行。"""
    storage = FakeStorage(_messages("user"), _asking_state())
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        response = client.get("/api/deerflow/threads/t1/messages/page")

    assert response.status_code == 200
    body = response.json()
    assert [row["seq"] for row in body["data"]] == [1, 2]
    card_row = body["data"][-1]
    assert card_row["run_id"] == "confirm-card"
    assert card_row["metadata"] == {"caller": "agent_scope_permission"}
    _assert_card(card_row["content"])


def test_messages_page_skips_card_when_paging_backward(monkeypatch) -> None:
    """向后翻页（before_seq）只读历史：不追加重建卡片。"""
    storage = FakeStorage(_messages("user", "assistant"), _asking_state())
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        response = client.get("/api/deerflow/threads/t1/messages/page?before_seq=1")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_no_card_when_no_pending_confirmation(monkeypatch) -> None:
    """session 无 ASKING 工具调用：读端点不追加任何卡片。"""
    storage = FakeStorage(_messages("user"), AgentState(context=[]))
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        state_resp = client.get("/api/deerflow/threads/t1/state")
        page_resp = client.get("/api/deerflow/threads/t1/messages/page")

    assert [m["type"] for m in state_resp.json()["values"]["messages"]] == [
        "human",
    ]
    assert [row["seq"] for row in page_resp.json()["data"]] == [1]


def test_card_options_include_confirm_always_with_rules(monkeypatch) -> None:
    """suggested_rules 存在：追加"同意并始终允许"选项。"""
    from agentscope.permission import PermissionBehavior

    state = _asking_state(
        extra={
            "suggested_rules": [
                PermissionRule(
                    tool_name="Bash",
                    rule_content="ps",
                    behavior=PermissionBehavior.ASK,
                    source="userSettings",
                ),
            ],
        },
    )
    storage = FakeStorage(_messages("user"), state)
    _patch_agents(monkeypatch)
    with TestClient(_make_app(storage)) as client:
        response = client.get("/api/deerflow/threads/t1/state")

    card = response.json()["values"]["messages"][-1]
    options = card["artifact"]["human_input"]["options"]
    assert [o["value"] for o in options] == ["confirm", "reject", "confirm_always"]
