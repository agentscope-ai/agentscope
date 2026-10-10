# -*- coding: utf-8 -*-
"""A single-process FastAPI human-in-the-loop example."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.model import DashScopeChatModel
from agentscope.state import AgentState
from agentscope.tool import AskUser, AskUserAnswer, AskUserMetadata, Toolkit


class AgentLike(Protocol):  # pylint: disable=too-few-public-methods
    """The small part of ``Agent`` used by this example."""

    def reply_stream(self, inputs: Any) -> AsyncIterator[Any]:
        """Stream one reply."""


AgentFactory = Callable[[str], AgentLike]
PendingKind = Literal["external", "permission"]


class SessionBusyError(RuntimeError):
    """Raised when two turns target the same session concurrently."""


class NoPendingInteractionError(RuntimeError):
    """Raised when a confirmation or interrupt has nothing to resume."""


class UnknownToolCallError(RuntimeError):
    """Raised when a confirmation targets an unknown tool call."""


class UnknownSessionError(RuntimeError):
    """Raised when an operation targets a session that was never started."""


class ChatRequest(BaseModel):
    """Start a new turn in a persistent session."""

    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ConfirmRequest(BaseModel):
    """Answer a pending permission prompt or ``AskUser`` call."""

    tool_call_id: str = Field(min_length=1)
    confirmed: bool = True
    answers: list[AskUserAnswer] = Field(default_factory=list)


@dataclass
class Session:
    """One in-memory agent session and its concurrency state."""

    agent: AgentLike
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_task: asyncio.Task[Any] | None = None
    pending_reply_id: str | None = None
    pending_tool_calls: dict[
        str,
        tuple[PendingKind, ToolCallBlock],
    ] = field(default_factory=dict)

    async def chat(self, message: str) -> list[dict[str, Any]]:
        """Run a user turn, rejecting overlapping work or parked HITL."""
        task = self._current_task()
        async with self.state_lock:
            self._ensure_idle()
            if self.pending_reply_id is not None:
                raise SessionBusyError(
                    "The session is waiting for /confirm or /interrupt.",
                )
            self.active_task = task

        return await self._consume(
            task,
            UserMsg(name="user", content=message),
            pending_reply_id=None,
            pending_tool_calls={},
        )

    async def confirm(
        self,
        request: ConfirmRequest,
    ) -> list[dict[str, Any]]:
        """Resume a reply with a permission decision or an AskUser answer."""
        task = self._current_task()
        async with self.state_lock:
            self._ensure_idle()
            if self.pending_reply_id is None:
                raise NoPendingInteractionError(
                    "The session is not waiting for confirmation.",
                )
            pending_call = self.pending_tool_calls.get(request.tool_call_id)
            if pending_call is None:
                raise UnknownToolCallError(
                    f"Unknown pending tool call: {request.tool_call_id}",
                )

            pending_kind, tool_call = pending_call
            reply_id = self.pending_reply_id
            remaining = dict(self.pending_tool_calls)
            remaining.pop(request.tool_call_id)
            pending_reply_id = reply_id if remaining else None
            self.active_task = task

        if pending_kind == "permission":
            inputs: Any = UserConfirmResultEvent(
                reply_id=reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=request.confirmed,
                        tool_call=tool_call,
                    ),
                ],
            )
        else:
            if tool_call.name != AskUser.name or not request.answers:
                await self._release_task(task)
                raise NoPendingInteractionError(
                    "External AskUser calls require at least one answer.",
                )
            metadata = AskUserMetadata(answers=request.answers)
            inputs = ExternalExecutionResultEvent(
                reply_id=reply_id,
                execution_results=[
                    ToolResultBlock(
                        id=tool_call.id,
                        name=tool_call.name,
                        output=_format_answers(request.answers),
                        state=ToolResultState.SUCCESS,
                        metadata=metadata.model_dump(mode="json"),
                    ),
                ],
            )

        return await self._consume(
            task,
            inputs,
            pending_reply_id=pending_reply_id,
            pending_tool_calls=remaining,
        )

    async def interrupt(self) -> tuple[str, list[dict[str, Any]]]:
        """Cancel active work or close a reply parked at a HITL event."""
        task = self._current_task()
        async with self.state_lock:
            if self.active_task is not None:
                active_task = self.active_task
                active_task.cancel()
                return "interrupt_requested", []

            if self.pending_reply_id is None:
                raise NoPendingInteractionError(
                    "The session has no active or parked reply.",
                )

            reply_id = self.pending_reply_id
            self.active_task = task

        events = await self._consume(
            task,
            UserInterruptEvent(reply_id=reply_id),
            pending_reply_id=None,
            pending_tool_calls={},
        )
        return "interrupted", events

    async def _consume(
        self,
        task: asyncio.Task[Any],
        inputs: Any,
        *,
        pending_reply_id: str | None,
        pending_tool_calls: dict[
            str,
            tuple[PendingKind, ToolCallBlock],
        ],
    ) -> list[dict[str, Any]]:
        """Collect events and update the parked-HITL snapshot atomically."""
        events: list[dict[str, Any]] = []
        try:
            async for event in self.agent.reply_stream(inputs):
                events.append(event.model_dump(mode="json"))
                if isinstance(event, RequireExternalExecutionEvent):
                    pending_reply_id = event.reply_id
                    pending_tool_calls.update(
                        {
                            call.id: ("external", call)
                            for call in event.tool_calls
                        },
                    )
                elif isinstance(event, RequireUserConfirmEvent):
                    pending_reply_id = event.reply_id
                    pending_tool_calls.update(
                        {
                            call.id: ("permission", call)
                            for call in event.tool_calls
                        },
                    )
                elif isinstance(event, ReplyEndEvent):
                    pending_reply_id = None
                    pending_tool_calls = {}
        finally:
            async with self.state_lock:
                if self.active_task is task:
                    self.active_task = None
                    self.pending_reply_id = pending_reply_id
                    self.pending_tool_calls = pending_tool_calls
        return events

    async def _release_task(self, task: asyncio.Task[Any]) -> None:
        """Release a claimed turn after request validation fails."""
        async with self.state_lock:
            if self.active_task is task:
                self.active_task = None

    def _ensure_idle(self) -> None:
        if self.active_task is not None:
            raise SessionBusyError("Another request is using this session.")

    @staticmethod
    def _current_task() -> asyncio.Task[Any]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Session operations require an asyncio task.")
        return task


class SessionStore:
    """Create and retain one agent for each API session ID."""

    def __init__(self, agent_factory: AgentFactory) -> None:
        self.agent_factory = agent_factory
        self.sessions: dict[str, Session] = {}
        self.lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> Session:
        """Return the existing session or create it exactly once."""
        async with self.lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = Session(
                    agent=self.agent_factory(session_id),
                )
            return self.sessions[session_id]

    async def get(self, session_id: str) -> Session:
        """Return an existing session without creating an agent."""
        async with self.lock:
            try:
                return self.sessions[session_id]
            except KeyError as error:
                raise UnknownSessionError(
                    f"Unknown session: {session_id}",
                ) from error


def _format_answers(answers: list[AskUserAnswer]) -> str:
    """Create the human-readable half of an AskUser tool result."""
    formatted = []
    for answer in answers:
        value = ", ".join(answer.selected) or answer.other or "No answer"
        formatted.append(f"{answer.question}: {value}")
    return "\n".join(formatted)


def _default_agent_factory(session_id: str) -> Agent:
    """Build the demo agent lazily, when the first request arrives."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Set DASHSCOPE_API_KEY before creating a session.")

    return Agent(
        name="Planner",
        system_prompt=(
            "You are a planning assistant. Before carrying out a multi-step "
            "plan, present it in an AskUser question with Approve and Revise "
            "options. Continue only after the user answers."
        ),
        model=DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model="qwen3.8-max",
            stream=True,
        ),
        toolkit=Toolkit(tools=[AskUser()]),
        state=AgentState(session_id=session_id),
    )


def create_app(agent_factory: AgentFactory | None = None) -> FastAPI:
    """Create an app; dependency injection keeps tests model-free."""
    api = FastAPI(title="AgentScope FastAPI HITL example")
    store = SessionStore(agent_factory or _default_agent_factory)
    api.state.session_store = store

    @api.post("/chat")
    async def chat(request: ChatRequest) -> dict[str, Any]:
        session = await store.get_or_create(request.session_id)
        try:
            events = await session.chat(request.message)
        except SessionBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"session_id": request.session_id, "events": events}

    @api.post("/confirm/{session_id}")
    async def confirm(
        session_id: str,
        request: ConfirmRequest,
    ) -> dict[str, Any]:
        try:
            session = await store.get(session_id)
            events = await session.confirm(request)
        except UnknownSessionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SessionBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (NoPendingInteractionError, UnknownToolCallError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"session_id": session_id, "events": events}

    @api.post("/interrupt/{session_id}")
    async def interrupt(session_id: str) -> dict[str, Any]:
        try:
            session = await store.get(session_id)
            status, events = await session.interrupt()
        except UnknownSessionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except NoPendingInteractionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "session_id": session_id,
            "status": status,
            "events": events,
        }

    return api


app = create_app()
