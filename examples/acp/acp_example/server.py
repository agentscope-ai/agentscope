# -*- coding: utf-8 -*-
"""ACP Agent-role handlers and the stdio entrypoint (DESIGN.md §16).

One ACP turn == one (possibly resumed) ``Agent.reply_stream`` run.

Cancellation architecture: the generator is consumed by a dedicated
**driver task** whose only awaits are the generator's ``__anext__``
steps; events are handed to the request handler through a queue. A
``session/cancel`` therefore cancels the *driver*, which guarantees the
``CancelledError`` is delivered *inside* ``reply_stream`` — where the
core (#1995) catches it, closes running tools with ``INTERRUPTED``
results, and ends the stream with
``ReplyEndEvent(finished_reason=INTERRUPTED)``. Cancelling the
forwarding coroutine instead (or closing the generator with
``aclose()``, which raises ``GeneratorExit``) would bypass that cleanup
and orphan concurrent tool workers. The stop reason is then derived
from ``finished_reason`` rather than from exception plumbing.
"""
# pylint: disable=unused-argument  # the ACP handler signatures are fixed
import asyncio
import contextlib
import os
import posixpath
import sys
from typing import Any, AsyncGenerator

from acp import (
    PROTOCOL_VERSION,
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
    AgentEvent,
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

# Queue sentinel marking the end of one reply_stream run.
_STREAM_END = object()


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


def _is_abs(path: str) -> bool:
    """Absolute on either POSIX or the host platform (Windows drives)."""
    return posixpath.isabs(path) or os.path.isabs(path)


class AgentScopeAcpAgent:
    """The ACP Agent role wrapping AgentScope agents.

    Deliberately NOT a subclass of ``acp.Agent``: the SDK dispatches
    handlers structurally via ``getattr``, and inheriting the Protocol's
    placeholder method bodies would turn every unimplemented optional
    method (``session/load``, ``session/list``, …) into a bogus no-op
    success instead of the correct ``-32601`` method-not-found error.
    """

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
        if not _is_abs(cwd):
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
            # Child task, so a cancel aimed at the parked-permission
            # window cancels the *turn*, not this request handler.
            sess.turn_task = asyncio.create_task(
                self._run_turn(sess, user_msg),
            )
            try:
                stop = await sess.turn_task
            except asyncio.CancelledError:
                if sess.turn_task.cancelled():
                    # The cancel won a race before the turn could end
                    # gracefully; the stop reason MUST still be
                    # returned (never a JSON-RPC error).
                    stop = "cancelled"
                else:
                    raise
        finally:
            sess.end_turn()
        return PromptResponse(stop_reason=stop)

    @staticmethod
    async def _drive_stream(
        stream: AsyncGenerator[AgentEvent, None],
        queue: "asyncio.Queue[Any]",
    ) -> None:
        """Consume one reply_stream run in a task of its own.

        This task's only awaits are the generator's ``__anext__`` steps
        (``put_nowait`` never suspends), so cancelling it delivers the
        ``CancelledError`` inside the generator, triggering the core's
        graceful INTERRUPTED close-out. The close-out events still flow
        into the queue before the sentinel.
        """
        try:
            async for event in stream:
                queue.put_nowait(event)
        finally:
            queue.put_nowait(_STREAM_END)

    async def _run_turn(self, sess: Session, inputs: Any) -> str:
        """One ACP turn: a reply_stream, resumed across permission gates."""
        try:
            while True:
                pending_confirm: RequireUserConfirmEvent | None = None
                pending_external = False
                finished: ReplyFinishedReason | None = None
                error: Any = None
                queue: asyncio.Queue = asyncio.Queue()
                stream = sess.agent.reply_stream(inputs)
                sess.driver_task = asyncio.create_task(
                    self._drive_stream(stream, queue),
                )
                try:
                    while True:
                        event = await queue.get()
                        if event is _STREAM_END:
                            break
                        if isinstance(event, ReplyStartEvent):
                            sess.last_reply_id = event.reply_id
                        elif isinstance(event, RequireUserConfirmEvent):
                            # The reply is parking; the stream ends
                            # right after this event.
                            pending_confirm = event
                            continue
                        elif isinstance(
                            event,
                            RequireExternalExecutionEvent,
                        ):
                            # The fixed tool set has no external tools;
                            # a forker who adds one must extend this.
                            print(
                                "acp_example: external tool execution "
                                "is not supported by this example; "
                                "interrupting.",
                                file=sys.stderr,
                            )
                            pending_external = True
                            continue
                        elif isinstance(event, ReplyEndEvent):
                            finished = event.finished_reason
                            error = event.error
                        for update in sess.translator.translate(event):
                            await self._conn.session_update(
                                session_id=sess.id,
                                update=update,
                            )
                finally:
                    driver = sess.driver_task
                    sess.driver_task = None
                    if driver is not None:
                        if not driver.done():
                            driver.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            # Surfaces agent-side exceptions; a
                            # cancelled driver is the graceful path.
                            await driver
                if error is not None:
                    raise RequestError.internal_error(
                        {"error": str(error)},
                    )
                if pending_external:
                    return await self._abort_parked(
                        sess,
                        sess.last_reply_id,
                    )
                if pending_confirm is None:
                    return _stop_reason(finished)
                if sess.cancel_requested:
                    # session/cancel raced the park: don't prompt.
                    return await self._abort_parked(
                        sess,
                        pending_confirm.reply_id,
                    )
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
            # Only reachable when cancel() targeted the turn task: the
            # reply was parked (permission round-trip) or the cancel
            # raced a forwarding await after the stream had ended. The
            # generator itself is never suspended here (the driver owns
            # it), so closing out the parked state is safe.
            await self._close_out_cancelled(sess)
            return "cancelled"
        except Exception:
            # Never leave a parked reply behind — it would brick every
            # subsequent prompt on this session ("Agent is waiting for
            # N tool calls..."). Interrupting an idle agent is a no-op.
            await self._close_out_cancelled(sess)
            raise

    async def _close_out_cancelled(self, sess: Session) -> None:
        """Best-effort close-out of a parked reply after an abort.

        Runs under ``asyncio.shield`` so a repeated ``session/cancel``
        (or the surrounding task's cancellation) cannot abort the
        close-out halfway and leave dangling ASKING tool calls in the
        agent context. ``cancel()`` is additionally idempotent per
        turn, so no second cancellation targets this path.
        """
        if sess.last_reply_id is None:
            return
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(
                self._drain_interrupt(sess, sess.last_reply_id),
            )

    async def _abort_parked(self, sess: Session, reply_id: str | None) -> str:
        """Abort a parked reply and surface its close-out updates."""
        if reply_id is not None:
            await self._drain_interrupt(sess, reply_id)
        return "cancelled"

    async def _drain_interrupt(self, sess: Session, reply_id: str) -> None:
        """Feed ``UserInterruptEvent`` and forward the close-out events.

        The core closes pending ASKING/SUBMITTED tool calls with
        INTERRUPTED results and ends the reply; the client sees the
        corresponding ``tool_call_update`` notifications. On an idle
        agent (nothing parked) this yields nothing.
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
        """``session/cancel``: cancel the in-flight turn (§18).

        Prefers cancelling the stream **driver** (the graceful path:
        the core converts it into INTERRUPTED results and the turn
        resolves through the normal event flow). Only when no driver is
        running — the reply is parked at the permission round-trip —
        is the turn task itself cancelled. Idempotent per turn: repeated
        cancels while the close-out is in flight are no-ops, so they
        cannot abort the cleanup halfway.
        """
        try:
            sess = self._sessions.get(session_id)
        except RequestError:
            return  # cancelling an unknown session is a no-op
        if sess.cancel_requested:
            return
        sess.cancel_requested = True
        driver = sess.driver_task
        if driver is not None and not driver.done():
            driver.cancel()
            return
        task = sess.turn_task
        if task is not None and not task.done():
            task.cancel()


async def main() -> None:
    """Serve the agent over stdio until the client disconnects."""
    # The kernel MUST NOT write non-ACP text to stdout; all logging in
    # this example goes to stderr.
    await run_agent(AgentScopeAcpAgent())
