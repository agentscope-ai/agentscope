# -*- coding: utf-8 -*-
"""The human-in-the-loop example of the ReAct agent."""
import os
from typing import AsyncGenerator

from fastapi import FastAPI

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession

app = FastAPI()


@app.get("/")
async def chat_endpoint(session_id: str) -> AsyncGenerator[Msg, None]:
    """The chat endpoint for the human-in-the-loop example."""

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeChatFormatter(),
    )

    session = JSONSession()
    await session.load_session_state(session_id=session_id, agent=agent)

    try:
        async for msg, _ in stream_printing_messages(
            agents=[agent],
            coroutine_task=agent(
                Msg("user", "Hello, who are you?", "user"),
            ),
        ):
            yield msg

    finally:
        await session.save_session_state(session_id=session_id, agent=agent)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="localhost",
        port=8000,
    )
