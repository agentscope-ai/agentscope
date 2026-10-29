# -*- coding: utf-8 -*-
"""Session and permission adapter for ACP clients."""

import asyncio
from collections.abc import Callable
from contextlib import (
    AbstractAsyncContextManager,
    AsyncExitStack,
    aclosing,
    suppress,
)
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

from acp import PROTOCOL_VERSION, RequestError
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    AllowedOutcome,
    AudioContentBlock,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    McpCapabilities,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    ResourceLink,
    SessionCapabilities,
    SessionCloseCapabilities,
    TextContentBlock,
    ToolCallUpdate,
)

from ._events import EventProjector, parse_input
from ._mcp import MCPServer, build_mcp_clients
from .._logging import logger
from .._version import __version__
from ..agent import Agent
from ..event import (
    ConfirmResult,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg, UserMsg
from ..types import ReplyFinishedReason

_FACTORY_CLOSE_TIMEOUT = 30.0


@dataclass(frozen=True)
class ACPSessionContext:
    """Factory input; cwd is a project path, not a storage directory."""

    session_id: str
    cwd: Path


# Create a fresh Agent in an async context that owns its resources.
# Exit must propagate asyncio.CancelledError and must not block the event loop.
# Swallowing cancellation prevents the exit timeout from enforcing its limit.
# Timeout cancellation can interrupt cleanup before all resources are released.
AgentFactory = Callable[
    [ACPSessionContext],
    AbstractAsyncContextManager[Agent],
]


class _PermissionCancelled(Exception):
    """The client dismissed a permission request and aborted the turn."""


@dataclass
class _Session:
    """Keep resource ownership separate from cancellable prompt tasks."""

    context: ACPSessionContext
    ready: asyncio.Future[None]
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    agent: Agent | None = None
    owner: asyncio.Task[None] | None = None
    prompt_task: asyncio.Task[PromptResponse] | None = None
    closing: bool = False


class ACPAgentServer:
    """Expose independent Agents created by an async context factory.

    A factory must yield a fresh Agent for every session. Its context owns
    application resources. Client-supplied MCP clients are automatically
    connected and added to the basic tool group for that session.

    Factory exit is cancelled after 30 seconds. Factories must propagate
    asyncio.CancelledError and must not block the event loop; otherwise the
    timeout cannot enforce its limit. Interrupted cleanup may leave resources
    unreleased. See :data:`AgentFactory` for the factory contract.

    Call :func:`serve_acp` for stdio transport, or bind this adapter to an
    SDK connection and call :meth:`aclose` when the connection ends.
    """

    def __init__(self, agent_factory: AgentFactory) -> None:
        self._factory = agent_factory
        self._sessions: dict[str, _Session] = {}
        self._conn: Client | None = None
        self._initialized = False
        self._closed = False

    def on_connect(self, conn: Client) -> None:
        """Bind the bidirectional ACP connection."""
        self._conn = conn

    async def initialize(  # pylint: disable=unused-argument
        self,
        protocol_version: int,
        **_: Any,
    ) -> InitializeResponse:
        """Offer the supported version rather than echoing an unknown one."""
        # A client may accept our version even if it proposed a newer one.
        self._initialized = True
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(
                name="agentscope",
                version=__version__,
            ),
            agent_capabilities=AgentCapabilities(
                mcp_capabilities=McpCapabilities(http=True, sse=False),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                ),
            ),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[MCPServer] | None = None,
        **_: Any,
    ) -> NewSessionResponse:
        """Create a session, rolling back all resources on setup failure."""
        if not self._initialized or self._closed:
            raise RequestError.invalid_request(
                {"details": "Initialize an open connection first"},
            )
        path = Path(cwd)
        if not path.is_absolute() or not path.is_dir():
            raise RequestError.invalid_params(
                {"details": "cwd must be an existing absolute directory"},
            )
        try:
            clients = build_mcp_clients(mcp_servers or [], str(path))
        except ValueError as exc:
            raise RequestError.invalid_params({"details": str(exc)}) from exc
        session_id = uuid4().hex
        session = _Session(
            context=ACPSessionContext(session_id, path),
            ready=asyncio.get_running_loop().create_future(),
        )
        self._sessions[session_id] = session

        async def own_resources() -> None:
            """Enter and exit MCP cancel scopes in the same task."""
            try:
                async with AsyncExitStack() as stack:
                    factory = self._factory(session.context)
                    # Register a timed exit after entering in this task.
                    # pylint: disable-next=unnecessary-dunder-call
                    agent = await factory.__aenter__()

                    async def close_factory(
                        exc_type: type[BaseException] | None,
                        exc: BaseException | None,
                        traceback: TracebackType | None,
                    ) -> bool | None:
                        """Bound exit in the task that entered the factory."""
                        deadline = asyncio.timeout(_FACTORY_CLOSE_TIMEOUT)
                        try:
                            async with deadline:
                                return await factory.__aexit__(
                                    exc_type,
                                    exc,
                                    traceback,
                                )
                        except TimeoutError as error:
                            if not deadline.expired():
                                raise
                            warning = (
                                f"ACP session '{session_id}' factory cleanup "
                                f"exceeded {_FACTORY_CLOSE_TIMEOUT} seconds; "
                                f"cleanup was interrupted and resources may "
                                f"not be fully released."
                            )
                            logger.warning(warning)
                            raise TimeoutError(
                                f"ACP session '{session_id}' factory cleanup "
                                f"exceeded {_FACTORY_CLOSE_TIMEOUT} seconds",
                            ) from error

                    stack.push_async_exit(close_factory)
                    for client in clients:
                        await client.connect()
                        if client.is_stateful:
                            stack.push_async_callback(client.close)
                    basic_mcps = agent.toolkit.tool_groups[0].mcps

                    def remove_session_mcps() -> None:
                        """Remove only session-owned client objects."""
                        basic_mcps[:] = [
                            entry
                            for entry in basic_mcps
                            if not any(entry is client for client in clients)
                        ]

                    if clients:
                        basic_mcps.extend(clients)
                        stack.callback(remove_session_mcps)
                    session.agent = agent
                    session.ready.set_result(None)
                    await session.stopped.wait()
            except BaseException as exc:
                if not session.ready.done():
                    session.ready.set_exception(exc)
                else:
                    raise

        session.owner = asyncio.create_task(own_resources())
        try:
            await asyncio.shield(session.ready)
        except BaseException:
            await self.close_session(session_id)
            # Retrieve setup exceptions even when the request was cancelled.
            if session.ready.done() and not session.ready.cancelled():
                session.ready.exception()
            raise
        return NewSessionResponse(session_id=session_id)

    def _get_session(self, session_id: str) -> _Session:
        """Reject stale, closing or still-initializing sessions."""
        session = self._sessions.get(session_id)
        if session is None or session.closing or session.agent is None:
            raise RequestError.invalid_params(
                {"details": "Unknown or unavailable session"},
            )
        return session

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ResourceLink
            | ImageContentBlock
            | AudioContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        **_: Any,
    ) -> PromptResponse:
        """Run a complete turn, including permission round trips."""
        session = self._get_session(session_id)
        if session.prompt_task is not None:
            raise RequestError.invalid_request(
                {"details": "This session already has an active prompt"},
            )
        if not prompt or any(
            not isinstance(block, (TextContentBlock, ResourceLink))
            for block in prompt
        ):
            raise RequestError.invalid_params(
                {"details": "Expected text or resource link content blocks"},
            )
        message = UserMsg(
            name="user",
            content="\n".join(
                block.text
                if isinstance(block, TextContentBlock)
                else f"[{block.name}]({block.uri})"
                for block in prompt
            ),
        )
        task = asyncio.create_task(self._run_prompt(session, message))
        session.prompt_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.cancel(session_id)
            caller = asyncio.current_task()
            if caller is not None and not caller.cancelling():
                return PromptResponse(stop_reason="cancelled")
            raise
        finally:
            session.prompt_task = None

    async def _run_prompt(
        self,
        session: _Session,
        inputs: Msg | UserConfirmResultEvent,
    ) -> PromptResponse:
        """Translate stream segments until the Agent finishes or aborts."""
        assert session.agent is not None and self._conn is not None
        session_id = session.context.session_id
        projector = EventProjector()
        pending: RequireUserConfirmEvent | None = None
        parked_reply: str | None = None
        reason = ReplyFinishedReason.COMPLETED

        async def interrupt_parked() -> None:
            """Clear pending tool state even if the client disconnected."""
            if parked_reply is not None:
                async for event in session.agent.reply_stream(
                    UserInterruptEvent(reply_id=parked_reply),
                ):
                    update = projector.project(event)
                    if update is not None:
                        with suppress(ConnectionError):
                            await self._conn.session_update(session_id, update)

        try:
            while True:
                pending = None
                async with aclosing(
                    session.agent.reply_stream(inputs),
                ) as stream:
                    async for event in stream:
                        if isinstance(event, RequireUserConfirmEvent):
                            pending = event
                            parked_reply = event.reply_id
                        elif isinstance(event, RequireExternalExecutionEvent):
                            parked_reply = event.reply_id
                            raise RequestError.internal_error(
                                {"details": "External execution unsupported"},
                            )
                        elif isinstance(event, ReplyEndEvent):
                            reason = event.finished_reason
                            if reason == ReplyFinishedReason.ERROR:
                                raise RequestError.internal_error(
                                    {
                                        "details": "Agent execution failed",
                                        "error": (
                                            event.error.model_dump(mode="json")
                                            if event.error is not None
                                            else None
                                        ),
                                    },
                                )
                        update = projector.project(event)
                        if update is not None:
                            await self._conn.session_update(session_id, update)
                if pending is None:
                    break
                inputs = await self._confirm(session_id, pending)
                parked_reply = None
            if reason == ReplyFinishedReason.INTERRUPTED:
                return PromptResponse(stop_reason="cancelled")
            if reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
                return PromptResponse(stop_reason="max_turn_requests")
            return PromptResponse(stop_reason="end_turn")
        except (asyncio.CancelledError, _PermissionCancelled):
            await interrupt_parked()
            return PromptResponse(stop_reason="cancelled")
        except Exception as exc:
            await interrupt_parked()
            if isinstance(exc, RequestError):
                raise
            # pylint: disable-next=logging-fstring-interpolation
            logger.exception(f"ACP prompt failed in session {session_id}")
            raise RequestError.internal_error(
                {"details": "Agent execution failed; see stderr"},
            ) from exc

    async def _confirm(
        self,
        session_id: str,
        pending: RequireUserConfirmEvent,
    ) -> UserConfirmResultEvent:
        """Ask for each tool approval and preserve optional rule grants."""
        assert self._conn is not None
        results = []
        for call in pending.tool_calls:
            options = [
                PermissionOption(
                    option_id="allow_once",
                    name="Allow once",
                    kind="allow_once",
                ),
                PermissionOption(
                    option_id="reject_once",
                    name="Reject",
                    kind="reject_once",
                ),
            ]
            if call.suggested_rules:
                options.append(
                    PermissionOption(
                        option_id="allow_always",
                        name="Allow matching calls in this session",
                        kind="allow_always",
                    ),
                )
            response = await self._conn.request_permission(
                session_id=session_id,
                options=options,
                tool_call=ToolCallUpdate(
                    tool_call_id=call.id,
                    title=call.name,
                    status="pending",
                    raw_input=parse_input(call.input),
                ),
            )
            if not isinstance(response.outcome, AllowedOutcome):
                raise _PermissionCancelled()
            selected = response.outcome.option_id
            allowed = selected == "allow_once" or (
                selected == "allow_always" and bool(call.suggested_rules)
            )
            results.append(
                ConfirmResult(
                    confirmed=allowed,
                    tool_call=call,
                    rules=(
                        call.suggested_rules
                        if allowed and selected == "allow_always"
                        else None
                    ),
                ),
            )
        return UserConfirmResultEvent(
            reply_id=pending.reply_id,
            confirm_results=results,
        )

    async def cancel(self, session_id: str, **_: Any) -> None:
        """Cancel once, then wait for the Agent's cleanup to finish."""
        session = self._sessions.get(session_id)
        task = session.prompt_task if session else None
        if task is not None and not task.done():
            if not task.cancelling():
                task.cancel()
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        caller = asyncio.current_task()
                        if caller is not None and caller.cancelling():
                            cancelled = True
            except RequestError:
                pass
            finally:
                if cancelled:
                    raise asyncio.CancelledError()

    async def close_session(
        self,
        session_id: str,
        **_: Any,
    ) -> CloseSessionResponse:
        """Stop work before closing the session's MCP and factory context."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.closing = True
            errors: list[BaseException] = []
            try:
                await self.cancel(session_id)
            except BaseException as exc:
                # An interrupt failure must not skip resource shutdown.
                errors.append(exc)
            session.stopped.set()
            try:
                if session.owner is not None:
                    if (
                        not session.ready.done()
                        and not session.owner.cancelling()
                    ):
                        session.owner.cancel()
                    while not session.owner.done():
                        try:
                            await asyncio.shield(session.owner)
                        except asyncio.CancelledError as exc:
                            caller = asyncio.current_task()
                            if caller is not None and caller.cancelling():
                                errors.append(exc)
                    if not session.owner.cancelled():
                        session.owner.result()
            except Exception as exc:
                errors.append(exc)
            finally:
                if not session.ready.done():
                    session.ready.cancel()
                self._sessions.pop(session_id, None)
            cancelled = next(
                (
                    exc
                    for exc in errors
                    if isinstance(exc, asyncio.CancelledError)
                ),
                None,
            )
            if cancelled is not None:
                failures = [
                    exc
                    for exc in errors
                    if not isinstance(exc, asyncio.CancelledError)
                ]
                if failures:
                    raise cancelled from BaseExceptionGroup(
                        "ACP session cleanup failed",
                        failures,
                    )
                raise cancelled
            if len(errors) == 1:
                raise errors[0]
            if errors:
                raise BaseExceptionGroup("ACP session cleanup failed", errors)
        return CloseSessionResponse()

    async def aclose(self) -> None:
        """Release every session on EOF or server shutdown."""
        self._closed = True
        results = await asyncio.gather(
            *(self.close_session(sid) for sid in list(self._sessions)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
