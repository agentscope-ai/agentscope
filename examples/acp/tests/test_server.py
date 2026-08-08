# -*- coding: utf-8 -*-
"""End-to-end tests driving the ACP handlers with a mock client (§19).

The mock client serves fs/* against a temp dir and terminal/* with real
subprocesses, so the builtin tools execute the full shell-delegation
path: content via fs/*, existence/dir/mkdir checks via terminal/*.
"""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from acp import RequestError, text_block
from acp.schema import ClientCapabilities, FileSystemCapabilities

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.types import ReplyFinishedReason

import acp_example.agent as agent_mod
from acp_example.config import Config
from acp_example.server import AgentScopeAcpAgent, _stop_reason

from mock_client import FakeClient
from mock_model import MockModel

FULL_CAPS = ClientCapabilities(
    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
    terminal=True,
)


def make_config() -> Config:
    """A shell-authority config that never reads the environment."""
    return Config(
        model_provider="dashscope",
        model_name=None,
        authority="shell",
        permission_mode="default",
        agent_name="agentscope-acp",
        workspace_backend="local",
    )


async def setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    permission_answers: list[str] | None = None,
    caps: ClientCapabilities = FULL_CAPS,
) -> tuple[AgentScopeAcpAgent, FakeClient, MockModel, str]:
    """Build a server+mock-client pair with one fresh session."""
    model = MockModel()
    monkeypatch.setattr(agent_mod, "_make_model", lambda config: model)
    client = FakeClient(permission_answers)
    server = AgentScopeAcpAgent(config=make_config())
    server.on_connect(client)
    await server.initialize(protocol_version=1, client_capabilities=caps)
    resp = await server.new_session(cwd=str(tmp_path))
    return server, client, model, resp.session_id


def tool_call_script(
    name: str,
    arguments: dict,
    call_id: str = "call-1",
) -> list[Any]:
    """One scripted model call that emits a single tool call."""
    block = ToolCallBlock(id=call_id, name=name, input=json.dumps(arguments))
    return [
        ChatResponse(content=[block], is_last=False),
        ChatResponse(content=[block], is_last=True),
    ]


def text_script(text: str) -> list[Any]:
    """One scripted model call that streams a text answer."""
    return [
        ChatResponse(content=[TextBlock(text=text)], is_last=False),
        ChatResponse(content=[TextBlock(text=text)], is_last=True),
    ]


# ── session lifecycle ─────────────────────────────────────────────────


async def test_session_id_is_agent_state_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariants a and b: stable session id + stored snapshot."""
    server, _, _, session_id = await setup(tmp_path, monkeypatch)
    sess = server._sessions.get(session_id)
    # Receipt invariant a: one stable id shared end-to-end.
    assert session_id == sess.state.session_id
    # Invariant b: the capability/authority snapshot is stored.
    assert sess.caps is FULL_CAPS
    assert sess.authority == "shell"


async def test_new_session_rejects_relative_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACP mandates absolute cwd paths."""
    server, _, _, _ = await setup(tmp_path, monkeypatch)
    with pytest.raises(RequestError):
        await server.new_session(cwd="relative/path")


# ── plain turns ───────────────────────────────────────────────────────


async def test_text_only_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A streamed text answer maps to correlated message chunks."""
    server, client, model, session_id = await setup(tmp_path, monkeypatch)
    model.set_responses([text_script("Hello world")])
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("Hi")],
    )
    assert resp.stop_reason == "end_turn"
    chunks = client.updates_of("agent_message_chunk")
    assert "".join(c.content.text for c in chunks) == "Hello world"
    # All chunks of one streamed block share one messageId.
    assert len({c.message_id for c in chunks}) == 1


async def test_busy_session_rejects_second_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-active-turn guard: a busy session rejects prompts."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["allow_once"],
    )
    # Park the turn at the permission gate so it stays in flight.
    client.permission_gate = asyncio.Event()
    (tmp_path / "f.txt").write_text("x")
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {"file_path": str(tmp_path / "f.txt"), "content": "y"},
            ),
            text_script("done"),
        ],
    )
    turn = asyncio.create_task(
        server.prompt(session_id=session_id, prompt=[text_block("go")]),
    )
    # Wait until the turn is parked at the permission request.
    while not client.permission_requests:
        await asyncio.sleep(0.01)
    with pytest.raises(RequestError):
        await server.prompt(session_id=session_id, prompt=[text_block("2nd")])
    client.permission_gate.set()
    resp = await turn
    assert resp.stop_reason == "end_turn"


# ── tool calls: read-only fast path ───────────────────────────────────


async def test_read_tool_shell_delegation_no_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read runs under DEFAULT with no permission prompt (#2117 fast
    path) and its content flows through fs/read_text_file."""
    server, client, model, session_id = await setup(tmp_path, monkeypatch)
    target = tmp_path / "notes.txt"
    target.write_text("the answer is 42\n")
    model.set_responses(
        [
            tool_call_script("Read", {"file_path": str(target)}),
            text_script("It says 42."),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("read notes.txt")],
    )
    assert resp.stop_reason == "end_turn"
    # The read-only fast path means no client prompt was raised.
    assert client.permission_requests == []

    starts = client.updates_of("tool_call")
    assert len(starts) == 1
    assert starts[0].tool_call_id == "call-1"
    assert starts[0].kind == "read"
    assert starts[0].status == "pending"

    progress = client.updates_of("tool_call_update")
    statuses = [u.status for u in progress if u.status]
    assert statuses[0] == "in_progress"
    assert statuses[-1] == "completed"
    final = [u for u in progress if u.status == "completed"][-1]
    assert "42" in final.raw_output

    # Receipt invariant c: the fs read (and the exec_shell existence
    # checks) are bound to the tool call id by the middleware.
    sess = server._sessions.get(session_id)
    fs_reads = [r for r in sess.ops.records if r.kind == "fs/read"]
    assert fs_reads and all(r.tool_call_id == "call-1" for r in fs_reads)
    term_ops = [r for r in sess.ops.records if r.kind == "terminal"]
    assert term_ops and all(r.tool_call_id == "call-1" for r in term_ops)


# ── permission round-trip ─────────────────────────────────────────────


async def test_write_permission_allow_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_once lets the write land through fs/write_text_file."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["allow_once"],
    )
    target = tmp_path / "out.txt"
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {
                    "file_path": str(target),
                    "content": "written by the kernel",
                },
            ),
            text_script("done"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write out.txt")],
    )
    assert resp.stop_reason == "end_turn"
    # Invariant d: the permission request is bound to the toolCallId.
    assert len(client.permission_requests) == 1
    assert client.permission_requests[0].tool_call_id == "call-1"
    assert client.permission_requests[0].raw_input["file_path"] == str(target)
    # The write went through the client's fs/write_text_file.
    assert target.read_text() == "written by the kernel"
    statuses = [
        u.status for u in client.updates_of("tool_call_update") if u.status
    ]
    assert statuses[-1] == "completed"


async def test_write_permission_reject_marks_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reject_once denies the call; the turn continues."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["reject_once"],
    )
    target = tmp_path / "out.txt"
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {
                    "file_path": str(target),
                    "content": "nope",
                },
            ),
            text_script("okay, not writing"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write out.txt")],
    )
    # The turn continues after a denial (the model sees the DENIED
    # result and answers in text).
    assert resp.stop_reason == "end_turn"
    assert not target.exists()
    denied = [
        u
        for u in client.updates_of("tool_call_update")
        if u.field_meta
        and u.field_meta.get("agentscope", {}).get("result_state") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0].status == "failed"


async def test_permission_cancelled_outcome_cancels_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled permission prompt aborts the parked reply."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["cancelled"],
    )
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {
                    "file_path": str(tmp_path / "x.txt"),
                    "content": "x",
                },
            ),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write")],
    )
    assert resp.stop_reason == "cancelled"
    # The parked ASKING call was closed out as interrupted.
    interrupted = [
        u
        for u in client.updates_of("tool_call_update")
        if u.field_meta
        and u.field_meta.get("agentscope", {}).get("result_state")
        == "interrupted"
    ]
    assert len(interrupted) == 1


# ── cancellation ──────────────────────────────────────────────────────


async def test_session_cancel_mid_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session/cancel during a running Bash tool yields stopReason
    cancelled (never a JSON-RPC error) and an interrupted tool result."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["allow_once"],
    )
    model.set_responses(
        [
            tool_call_script("Bash", {"command": "sleep 30"}),
        ],
    )
    turn = asyncio.create_task(
        server.prompt(session_id=session_id, prompt=[text_block("sleep")]),
    )
    # Wait for the client-owned terminal to actually start, then cancel.
    await asyncio.wait_for(client.terminal_started.wait(), timeout=10)
    await server.cancel(session_id=session_id)
    resp = await asyncio.wait_for(turn, timeout=10)
    assert resp.stop_reason == "cancelled"
    interrupted = [
        u
        for u in client.updates_of("tool_call_update")
        if u.field_meta
        and u.field_meta.get("agentscope", {}).get("result_state")
        == "interrupted"
    ]
    assert len(interrupted) == 1
    # A fresh turn works after the cancelled one.
    model.set_responses([text_script("still alive")])
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("hi")],
    )
    assert resp.stop_reason == "end_turn"


# ── capability gating ─────────────────────────────────────────────────


async def test_no_terminal_capability_disables_all_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without terminal, no client-delegated tool can run (§13)."""
    caps = ClientCapabilities(
        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
        terminal=False,
    )
    server, _, _, session_id = await setup(tmp_path, monkeypatch, caps=caps)
    sess = server._sessions.get(session_id)
    assert await sess.agent.toolkit.get_tool_schemas() == []


async def test_no_fs_capability_keeps_exec_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without fs.*, only the exec-backed tools survive (§13)."""
    caps = ClientCapabilities(
        fs=FileSystemCapabilities(
            read_text_file=False,
            write_text_file=False,
        ),
        terminal=True,
    )
    server, _, _, session_id = await setup(tmp_path, monkeypatch, caps=caps)
    sess = server._sessions.get(session_id)
    schemas = await sess.agent.toolkit.get_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"Grep", "Glob", "Bash"}


# ── stop-reason mapping ───────────────────────────────────────────────


def test_stop_reason_mapping() -> None:
    """finished_reason maps onto the ACP stopReason values."""
    assert _stop_reason(ReplyFinishedReason.COMPLETED) == "end_turn"
    assert (
        _stop_reason(ReplyFinishedReason.EXCEED_MAX_ITERS)
        == "max_turn_requests"
    )
    assert _stop_reason(ReplyFinishedReason.INTERRUPTED) == "cancelled"
    assert _stop_reason(None) == "end_turn"
