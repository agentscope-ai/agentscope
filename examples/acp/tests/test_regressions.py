# -*- coding: utf-8 -*-
"""Regression tests for defects found in adversarial review (§19).

Covers: the ``*_always`` permission options (rule construction and
persistence), the Edit end-to-end path, URL-sourced tool-result data,
the exec_shell timeout sentinel, repeated ``session/cancel``, and a
cancel landing while the turn is blocked forwarding a session/update.
"""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
from typing import Any

import pytest

from acp import text_block

from agentscope.event import (
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import ToolResultState

from acp_example.bridge import ClientBackend, OpRegistry
from acp_example.translate import Translator

from mock_client import FakeClient
from test_server import setup, text_script, tool_call_script

REPLY = "reply-1"


# ── *_always permission options ───────────────────────────────────────


async def test_allow_always_installs_rule_no_reprompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_always must not crash, and the installed rule suppresses
    the prompt for an identical operation in the next turn."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["allow_always"],
    )
    target = tmp_path / "out.txt"
    write_call = tool_call_script(
        "Write",
        {"file_path": str(target), "content": "v1"},
    )
    model.set_responses([write_call, text_script("done")])
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write")],
    )
    assert resp.stop_reason == "end_turn"
    assert target.read_text() == "v1"
    assert len(client.permission_requests) == 1

    # Second turn: Read (required before overwriting), then the same
    # Write — covered by the installed rule, so no second prompt.
    model.set_responses(
        [
            tool_call_script(
                "Read",
                {"file_path": str(target)},
                call_id="call-r",
            ),
            tool_call_script(
                "Write",
                {"file_path": str(target), "content": "v2"},
                call_id="call-2",
            ),
            text_script("done again"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write again")],
    )
    assert resp.stop_reason == "end_turn"
    assert target.read_text() == "v2"
    assert len(client.permission_requests) == 1  # no second prompt


async def test_reject_always_installs_deny_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reject_always must not crash; the DENY rule goes onto the public
    permission context (ConfirmResult.rules is ignored for denials) and
    auto-denies the identical operation next turn without a prompt."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["reject_always"],
    )
    target = tmp_path / "out.txt"
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {"file_path": str(target), "content": "nope"},
            ),
            text_script("okay"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write")],
    )
    assert resp.stop_reason == "end_turn"
    assert not target.exists()
    assert len(client.permission_requests) == 1
    sess = server._sessions.get(session_id)
    deny = sess.agent.state.permission_context.deny_rules.get("Write")
    assert deny, "reject_always must install a DENY rule"

    # Second turn: the deny rule resolves without prompting the client.
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {"file_path": str(target), "content": "still no"},
                call_id="call-2",
            ),
            text_script("understood"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("write again")],
    )
    assert resp.stop_reason == "end_turn"
    assert not target.exists()
    assert len(client.permission_requests) == 1  # no second prompt
    denied = [
        u
        for u in client.updates_of("tool_call_update")
        if u.tool_call_id == "call-2"
        and u.field_meta
        and u.field_meta.get("agentscope", {}).get("result_state") == "denied"
    ]
    assert len(denied) == 1


# ── Edit end-to-end ───────────────────────────────────────────────────


async def test_edit_tool_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edit composes fs/read + fs/write + exec existence checks."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
        permission_answers=["allow_once"],
    )
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\n")
    model.set_responses(
        [
            tool_call_script(
                "Read",
                {"file_path": str(target)},
                call_id="call-r",
            ),
            tool_call_script(
                "Edit",
                {
                    "file_path": str(target),
                    "old_string": "x = 1",
                    "new_string": "x = 42",
                },
            ),
            text_script("edited"),
        ],
    )
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("bump x")],
    )
    assert resp.stop_reason == "end_turn"
    assert target.read_text() == "x = 42\ny = 2\n"
    assert len(client.permission_requests) == 1
    statuses = [
        u.status for u in client.updates_of("tool_call_update") if u.status
    ]
    assert statuses[-1] == "completed"


# ── URL-sourced tool-result data ──────────────────────────────────────


def test_url_data_delta_does_not_crash_translator() -> None:
    """The URL variant (data=None) must not crash; the URL surfaces as
    a resource_link content block on the final update."""
    tr = Translator(OpRegistry())
    tr.translate(
        ToolResultStartEvent(
            reply_id=REPLY,
            tool_call_id="tc",
            tool_call_name="fetch_image",
        ),
    )
    assert not tr.translate(
        ToolResultDataDeltaEvent(
            reply_id=REPLY,
            tool_call_id="tc",
            block_id="b1",
            media_type="image/png",
            url="https://example.com/pic.png",
        ),
    )
    done = tr.translate(
        ToolResultEndEvent(
            reply_id=REPLY,
            tool_call_id="tc",
            state=ToolResultState.SUCCESS,
        ),
    )
    assert done[0].status == "completed"
    links = [
        c.content
        for c in done[0].content
        if getattr(c.content, "type", None) == "resource_link"
    ]
    assert len(links) == 1
    assert links[0].uri == "https://example.com/pic.png"


# ── exec_shell timeout sentinel ───────────────────────────────────────


async def test_exec_timeout_returns_timed_out_sentinel(
    tmp_path: Path,
) -> None:
    """A timed-out command must report the LocalBackend convention
    (exit_code -1, stderr b"timed out") that Bash/Grep key on."""
    client = FakeClient()
    backend = ClientBackend(
        conn=client,
        session_id="s",
        cwd=str(tmp_path),
        ops=OpRegistry(),
    )
    result = await backend.exec_shell(["sleep", "30"], timeout=0.3)
    assert result.exit_code == -1
    assert result.stderr == b"timed out"


# ── cancellation robustness ───────────────────────────────────────────


async def test_repeated_cancel_does_not_brick_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flurry of session/cancel while parked at the permission gate
    must close out cleanly and leave the session usable."""
    server, client, model, session_id = await setup(
        tmp_path,
        monkeypatch,
    )
    client.permission_gate = asyncio.Event()  # never released
    model.set_responses(
        [
            tool_call_script(
                "Write",
                {"file_path": str(tmp_path / "x.txt"), "content": "x"},
            ),
        ],
    )
    turn = asyncio.create_task(
        server.prompt(session_id=session_id, prompt=[text_block("go")]),
    )
    while not client.permission_requests:
        await asyncio.sleep(0.01)
    # Users mash Escape: several cancels in quick succession.
    await server.cancel(session_id=session_id)
    await server.cancel(session_id=session_id)
    await asyncio.sleep(0)
    await server.cancel(session_id=session_id)
    resp = await asyncio.wait_for(turn, timeout=10)
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
    # The session accepts and completes a fresh turn.
    client.permission_gate = None
    model.set_responses([text_script("alive")])
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("hi")],
    )
    assert resp.stop_reason == "end_turn"


class _BlockingUpdateClient(FakeClient):
    """FakeClient whose session_update stalls once, like a backpressured
    transport, so a cancel can land mid-forward."""

    def __init__(self) -> None:
        super().__init__(permission_answers=["allow_once"])
        self.block_on_status: str | None = "in_progress"
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        await super().session_update(session_id, update, **kwargs)
        if (
            self.block_on_status is not None
            and getattr(update, "status", None) == self.block_on_status
        ):
            self.block_on_status = None
            self.blocked.set()
            await self.release.wait()


async def test_cancel_while_forwarding_update_still_closes_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel landing while the turn is blocked in session_update must
    still produce the core's graceful INTERRUPTED close-out (the driver
    owns the generator, so the cancellation reaches reply_stream)."""
    import acp_example.agent as agent_mod
    from acp_example.server import AgentScopeAcpAgent
    from mock_model import MockModel
    from test_server import make_config

    model = MockModel()
    monkeypatch.setattr(agent_mod, "_make_model", lambda config: model)
    client = _BlockingUpdateClient()
    server = AgentScopeAcpAgent(config=make_config())
    server.on_connect(client)
    from acp.schema import ClientCapabilities, FileSystemCapabilities

    await server.initialize(
        protocol_version=1,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(
                read_text_file=True,
                write_text_file=True,
            ),
            terminal=True,
        ),
    )
    session_id = (await server.new_session(cwd=str(tmp_path))).session_id
    model.set_responses(
        [tool_call_script("Bash", {"command": "sleep 30"})],
    )
    turn = asyncio.create_task(
        server.prompt(session_id=session_id, prompt=[text_block("go")]),
    )
    # The forwarder is now stalled on the in_progress update while the
    # tool runs in a worker; fire the cancel into that window.
    await asyncio.wait_for(client.blocked.wait(), timeout=10)
    await server.cancel(session_id=session_id)
    await asyncio.sleep(0.05)
    client.release.set()
    resp = await asyncio.wait_for(turn, timeout=10)
    assert resp.stop_reason == "cancelled"
    # The graceful close-out reached the wire: the running tool call
    # ended as interrupted, not stuck at in_progress.
    interrupted = [
        u
        for u in client.updates_of("tool_call_update")
        if u.field_meta
        and u.field_meta.get("agentscope", {}).get("result_state")
        == "interrupted"
    ]
    assert len(interrupted) == 1
    # And the agent context was repaired: a fresh turn works.
    model.set_responses([text_script("alive")])
    resp = await server.prompt(
        session_id=session_id,
        prompt=[text_block("hi")],
    )
    assert resp.stop_reason == "end_turn"
