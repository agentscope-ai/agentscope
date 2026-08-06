# -*- coding: utf-8 -*-
"""ACP Agent-role handlers and the stdio entrypoint (DESIGN.md §16).

One ACP turn == one (possibly resumed) ``Agent.reply_stream`` run,
driven in a *child* asyncio task so that ``session/cancel`` cancels the
turn, never the ``session/prompt`` request handler. Since #1995 the
core converts that cancellation into a graceful ending — interrupted
tool-result events plus ``ReplyEndEvent(finished_reason=INTERRUPTED)``
— so the stop reason is derived from ``finished_reason`` rather than
from exception plumbing.
"""
import asyncio
import contextlib
import posixpath
import sys
from typing import Any

from acp import (
    PROTOCOL_VERSION,
    Agent as AcpAgentBase,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    RequestError,
    run_agent,
)
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
)

from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import DataBlock, Base64Source, TextBlock, UserMsg
from agentscope.state import AgentState
from agentscope.types import ReplyFinishedReason

from . import __version__
from .agent import build_agent
from .bridge import OpRegistry, request_permission_for
from .config import Config, load_config
from .session import Session, SessionManager


def _prompt_to_msg(prompt: list[Any]) -> UserMsg:
    """Map ACP prompt ContentBlocks to an AgentScope user message (§11).

    PR1 advertises ``promptCapabilities`` all-false, so conforming
    clients send only ``text`` and ``resource_link`` blocks; the other
    shapes are still handled defensively rather than crashing the turn.
    """
    blocks: list[TextBlock | DataBlock] = []
    for block in prompt:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block_type == "resource_link":
            mime = f", {block.mime_type}" if block.mime_type else ""
            blocks.append(
                TextBlock(
                    text=f"[Linked resource: {block.name}{mime}]"
                    f"({block.uri})",
                ),
            )
        elif block_type == "resource":
            contents = block.resource
            text = getattr(contents, "text", None)
            if text is not None:
                blocks.append(
                    TextBlock(
                        text=f"<resource uri={contents.uri!r}>\n{text}\n"
                        "</resource>",
                    ),
                )
        elif block_type in ("image", "audio"):
            blocks.append(
                DataBlock(
                    source=Base64Source(
                        data=block.data,
                        media_type=block.mime_type,
                    ),
                ),
            )
    if not blocks:
        blocks = [TextBlock(text="")]
    return UserMsg(name="user", content=blocks)


def _stop_reason(finished: ReplyFinishedReason | None) -> str:
    """Map ``ReplyEndEvent.finished_reason`` onto an ACP stopReason."""
    if finished == ReplyFinishedReason.EXCEED_MAX_ITERS:
        return "max_turn_requests"
    if finished == ReplyFinishedReason.INTERRUPTED:
        return "cancelled"
    # COMPLETED, or a defensive fallback if the stream ended without a
    # terminal event.
    return "end_turn"


class AgentScopeAcpAgent(AcpAgentBase):
    """The ACP Agent role wrapping AgentScope agents."""

    def __init__(self, config: Config | None = None) -> None:
        self._conn: Any = None
        self._client_caps: ClientCapabilities | None = None
        self._sessions = SessionManager()
        self._config = config or load_config()

    # ── lifecycle ─────────────────────────────────────────────────

    def on_connect(self, conn: Any) -> None:
        """Store the client-side connection handle."""
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        """Advertise capabilities; snapshot the client's (invariant b)."""
        self._client_caps = client_capabilities
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            # Minimal capabilities (§8): no session/load yet, prompt
            # capabilities all-false (text + resource_link baseline
            # only), no auth methods.
            agent_capabilities=AgentCapabilities(),
            agent_info=Implementation(
                name="agentscope-acp",
                title="AgentScope ACP Agent",
                version=__version__,
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        """Defensive no-op: no authMethods are advertised (§8)."""
        return None

    async def new_session(
        self,
        cwd: str,
        additional_directories: list | None = None,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Mint a session; ACP sessionId == AgentState.session_id."""
        if not posixpath.isabs(cwd):
            raise RequestError.invalid_params(
                {"cwd": f"cwd must be an absolute path, got {cwd!r}"},
            )
        # mcp_servers: stdio MCP wiring into Toolkit(mcps=...) is a
        # follow-on within the example (DESIGN.md §20); ignored in PR1.
        state = AgentState()
        ops = OpRegistry()
        agent = build_agent(
            cwd=cwd,
            state=state,
            conn=self._conn,
            caps=self._client_caps,
            ops=ops,
            config=self._config,
        )
        self._sessions.add(
            Session(
                id=state.session_id,
                agent=agent,
                state=state,
                cwd=cwd,
                caps=self._client_caps,
                authority=self._config.authority,
                ops=ops,
            ),
        )
        return NewSessionResponse(session_id=state.session_id)

    # ── the turn ──────────────────────────────────────────────────

    async def prompt(
        self,
        session_id: str,
        prompt: list,
        **kwargs: Any,
    ) -> PromptResponse:
        """Run one turn; the request stays open until the turn ends."""
        sess = self._sessions.get(session_id)
        if not sess.try_begin_turn():
            # Single-active-turn-per-session (§18): one Agent/AgentState
            # must not be driven by two concurrent reply_streams.
            raise RequestError(
                code=-32603,
                message="a turn is already in progress for this session",
            )
        try:
            user_msg = _prompt_to_msg(prompt)
            # Child task, so session/cancel cancels the *turn*, not this
            # request handler.
            sess.turn_task = asyncio.create_task(
                self._run_turn(sess, user_msg),
            )
            try:
                stop = await sess.turn_task
            except asyncio.CancelledError:
                if sess.turn_task.cancelled():
                    # session/cancel won the race before the turn could
                    # end gracefully; the stop reason MUST still be
                    # returned (never a JSON-RPC error).
                    stop = "cancelled"
                else:
                    raise
        finally:
            sess.end_turn()
        return PromptResponse(stop_reason=stop)

    async def _run_turn(self, sess: Session, inputs: Any) -> str:
        """One ACP turn: a reply_stream, resumed across permission gates."""
        stream = None
        try:
            while True:
                pending_confirm: RequireUserConfirmEvent | None = None
                finished: ReplyFinishedReason | None = None
                stream = sess.agent.reply_stream(inputs)
                async for event in stream:
                    if isinstance(event, ReplyStartEvent):
                        sess.last_reply_id = event.reply_id
                    if isinstance(event, RequireUserConfirmEvent):
                        pending_confirm = event
                        break
                    if isinstance(event, RequireExternalExecutionEvent):
                        # The fixed tool set has no external tools; a
                        # forker who adds one must extend this handler.
                        print(
                            "acp_example: external tool execution is not "
                            "supported by this example; interrupting.",
                            file=sys.stderr,
                        )
                        return await self._abort_parked(
                            sess,
                            event.reply_id,
                        )
                    if isinstance(event, ReplyEndEvent):
                        finished = event.finished_reason
                        if event.error is not None:
                            raise RequestError.internal_error(
                                {"error": str(event.error)},
                            )
                    for update in sess.translator.translate(event):
                        await self._conn.session_update(
                            session_id=sess.id,
                            update=update,
                        )
                stream = None
                if pending_confirm is None:
                    return _stop_reason(finished)
                # Resolve the gate at the client, bound to the exact
                # toolCallId (invariant d).
                confirm_results = await request_permission_for(
                    self._conn,
                    sess,
                    pending_confirm,
                )
                if confirm_results is None:
                    # Client answered {"outcome": "cancelled"}.
                    return await self._abort_parked(
                        sess,
                        pending_confirm.reply_id,
                    )
                inputs = UserConfirmResultEvent(
                    reply_id=pending_confirm.reply_id,
                    confirm_results=confirm_results,
                )
        except asyncio.CancelledError:
            # The cancellation landed while awaiting something *other*
            # than reply_stream (a session/update write, a permission
            # round-trip) — otherwise the agent itself would have ended
            # the stream gracefully with finished_reason=INTERRUPTED.
            if stream is not None:
                with contextlib.suppress(Exception):
                    await stream.aclose()
            elif sess.last_reply_id is not None:
                # Parked (awaiting permission): close the ASKING tool
                # calls through the public interrupt input.
                with contextlib.suppress(Exception):
                    await self._drain_interrupt(sess, sess.last_reply_id)
            return "cancelled"

    async def _abort_parked(self, sess: Session, reply_id: str) -> str:
        """Abort a parked reply and surface its close-out updates."""
        await self._drain_interrupt(sess, reply_id)
        return "cancelled"

    async def _drain_interrupt(self, sess: Session, reply_id: str) -> None:
        """Feed ``UserInterruptEvent`` and forward the close-out events.

        The core closes pending ASKING/SUBMITTED tool calls with
        INTERRUPTED results and ends the reply; the client sees the
        corresponding ``tool_call_update`` notifications.
        """
        async for event in sess.agent.reply_stream(
            UserInterruptEvent(reply_id=reply_id),
        ):
            for update in sess.translator.translate(event):
                await self._conn.session_update(
                    session_id=sess.id,
                    update=update,
                )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """``session/cancel``: cancel the child turn task (§18)."""
        sess = self._sessions.get(session_id)
        task = sess.turn_task
        if task is not None and not task.done():
            task.cancel()


async def main() -> None:
    """Serve the agent over stdio until the client disconnects."""
    # The kernel MUST NOT write non-ACP text to stdout; all logging in
    # this example goes to stderr.
    await run_agent(AgentScopeAcpAgent())
