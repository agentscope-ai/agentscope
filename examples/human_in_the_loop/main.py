# -*- coding: utf-8 -*-
"""Confirm tool calls over HTTP with a minimal FastAPI server.

The agent has two tools: ``get_weather`` runs right away, while
``send_email`` stops the reply and waits for the user to confirm or
cancel it. The server exposes two endpoints:

- ``POST /chat`` sends a user message to the agent. When the agent
  calls a tool that needs confirmation, the reply is parked and the
  pending tool calls are returned instead of a final answer.
- ``POST /confirm`` sends the user's decision for each pending tool
  call. A confirmed call is executed and the agent continues; a
  cancelled call is answered with a "denied" tool result and the agent
  continues reasoning without it.

Run with::

    export DASHSCOPE_API_KEY=sk-...
    python main.py [--model qwen3.7-max] [--port 8000]
"""
import argparse
import asyncio
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import Msg, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, DashScopeChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit


def get_weather(city: str) -> str:
    """Get the current weather of a city.

    Args:
        city (`str`):
            The name of the city.
    """
    return f"It's sunny and 25°C in {city}."


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email.

    Args:
        to (`str`):
            The email address of the recipient.
        subject (`str`):
            The subject of the email.
        body (`str`):
            The body of the email.
    """
    # A real implementation would deliver the email here
    return f"Email '{subject}' ({len(body)} characters) sent to {to}."


def create_agent(model: ChatModelBase) -> Agent:
    """Create an agent whose ``send_email`` tool requires confirmation."""
    return Agent(
        name="Friday",
        system_prompt="You are a helpful assistant named Friday.",
        model=model,
        toolkit=Toolkit(
            tools=[
                # Safe to run without asking the user
                FunctionTool(
                    get_weather,
                    is_read_only=True,
                    permission=PermissionDecision(
                        behavior=PermissionBehavior.ALLOW,
                        message="Reading the weather is always allowed.",
                    ),
                ),
                # Without an explicit permission, a function tool asks
                # the user to confirm every call
                FunctionTool(send_email),
            ],
        ),
    )


class ChatRequest(BaseModel):
    """The request body of ``POST /chat``."""

    session_id: str
    message: str


class Decision(BaseModel):
    """The user's decision for one pending tool call."""

    tool_call_id: str
    confirmed: bool


class ConfirmRequest(BaseModel):
    """The request body of ``POST /confirm``."""

    session_id: str
    decisions: list[Decision]


class PendingToolCall(BaseModel):
    """A tool call waiting for the user's decision."""

    id: str
    name: str
    input: str


class ChatResponse(BaseModel):
    """The response body of both endpoints."""

    status: str
    """``"completed"`` or ``"awaiting_confirmation"``."""
    reply: str | None = None
    """The agent's reply text."""
    pending_tool_calls: list[PendingToolCall] = []
    """The tool calls to confirm or cancel with ``POST /confirm``."""


class Session:
    """An agent and the tool calls it is waiting on."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.lock = asyncio.Lock()
        self.reply_id: str | None = None
        self.pending: dict[str, ToolCallBlock] = {}


def create_app(model: ChatModelBase) -> FastAPI:
    """Create the FastAPI app. Sessions are kept in memory."""
    app = FastAPI(title="AgentScope human-in-the-loop example")
    sessions: dict[str, Session] = {}

    async def run(
        session: Session,
        inputs: Msg | UserConfirmResultEvent,
    ) -> ChatResponse:
        """Run the agent until it finishes or parks on a confirmation."""
        reply: Msg | None = None
        async for item in session.agent.reply_stream(
            inputs,
            yield_final_msg=True,
        ):
            if isinstance(item, RequireUserConfirmEvent):
                session.reply_id = item.reply_id
                for tool_call in item.tool_calls:
                    session.pending[tool_call.id] = tool_call
            elif isinstance(item, Msg):
                reply = item

        text = reply.get_text_content() if reply else None
        if session.pending:
            return ChatResponse(
                status="awaiting_confirmation",
                reply=text,
                pending_tool_calls=[
                    PendingToolCall(id=_.id, name=_.name, input=_.input)
                    for _ in session.pending.values()
                ],
            )
        return ChatResponse(status="completed", reply=text)

    @app.post("/chat")
    async def chat(request: ChatRequest) -> ChatResponse:
        """Send a user message to the agent."""
        if request.session_id not in sessions:
            sessions[request.session_id] = Session(create_agent(model))
        session = sessions[request.session_id]

        async with session.lock:
            if session.pending:
                raise HTTPException(
                    status_code=409,
                    detail="Confirm or cancel the pending tool calls "
                    "with POST /confirm first.",
                )
            return await run(
                session,
                UserMsg(name="user", content=request.message),
            )

    @app.post("/confirm")
    async def confirm(request: ConfirmRequest) -> ChatResponse:
        """Confirm or cancel the pending tool calls and resume the agent."""
        session = sessions.get(request.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown session.")

        async with session.lock:
            decided = {_.tool_call_id for _ in request.decisions}
            if not session.pending or decided != set(session.pending):
                raise HTTPException(
                    status_code=400,
                    detail="Send exactly one decision for each pending "
                    f"tool call: {sorted(session.pending)}.",
                )

            event = UserConfirmResultEvent(
                reply_id=session.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=_.confirmed,
                        tool_call=session.pending[_.tool_call_id],
                    )
                    for _ in request.decisions
                ],
            )
            session.pending.clear()
            return await run(session, event)

    return app


def main() -> None:
    """The main entry point of the demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model=args.model,
        stream=True,
    )
    uvicorn.run(create_app(model), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
