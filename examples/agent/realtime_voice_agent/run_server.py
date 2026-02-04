# -*- coding: utf-8 -*-
"""A test server"""
import asyncio
import os
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from agentscope import logger
from agentscope.agent import RealtimeAgent
from agentscope.realtime import (
    DashScopeRealtimeModel,
    GeminiRealtimeModel,
    OpenAIRealtimeModel,
    ClientEvents,
    ServerEvents,
    ClientEventType,
)
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    execute_shell_command,
    view_text_file,
)

app = FastAPI()


@app.get("/")
async def get() -> FileResponse:
    """Serve the HTML test page."""
    html_path = Path(__file__).parent / "chatbot.html"
    return FileResponse(html_path)


@app.get("/api/check-models")
async def check_models() -> dict:
    """Check which model API keys are available in environment variables."""
    return {
        "dashscope": bool(os.getenv("DASHSCOPE_API_KEY")),
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
    }


async def frontend_receive(
    websocket: WebSocket,
    frontend_queue: asyncio.Queue,
) -> None:
    """Forward the message received from the agent to the frontend."""
    try:
        while True:
            msg: ServerEvents.EventBase = await frontend_queue.get()

            # Send the message as JSON
            await websocket.send_json(msg.model_dump())

    except Exception as e:
        print(f"[ERROR] frontend_receive error: {e}")
        traceback.print_exc()


@app.websocket("/ws/{user_id}/{session_id}")
async def single_agent_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for a single realtime agent."""
    try:
        await websocket.accept()

        logger.info(
            "Connected to WebSocket: user_id=%s, session_id=%s",
            user_id,
            session_id,
        )

        # Create the queue to forward messages to the frontend
        frontend_queue = asyncio.Queue()
        asyncio.create_task(
            frontend_receive(websocket, frontend_queue),
        )

        # Create the realtime agent
        agent = None

        while True:
            # Handle the incoming messages from the frontend
            # i.e. ClientEvents
            data = await websocket.receive_json()

            client_event = ClientEvents.from_json(data)

            if isinstance(
                client_event,
                ClientEvents.ClientSessionCreateEvent,
            ):
                # Create the agent by the given session arguments
                instructions = client_event.config.get(
                    "instructions",
                    "You're a helpful assistant.",
                )
                agent_name = client_event.config.get("agent_name", "Friday")
                model_provider = client_event.config.get(
                    "model_provider",
                    "dashscope",
                )

                sys_prompt = instructions

                # Create toolkit with tools for models that support them
                toolkit = None
                if model_provider in ["gemini", "openai"]:
                    toolkit = Toolkit()
                    toolkit.register_tool_function(execute_python_code)
                    toolkit.register_tool_function(execute_shell_command)
                    toolkit.register_tool_function(view_text_file)

                # Create the appropriate model based on provider
                if model_provider == "dashscope":
                    model = DashScopeRealtimeModel(
                        model_name="qwen3-omni-flash-realtime",
                        api_key=os.getenv("DASHSCOPE_API_KEY"),
                    )
                elif model_provider == "gemini":
                    model = GeminiRealtimeModel(
                        model_name=(
                            "gemini-2.5-flash-native-audio-preview-09-2025"
                        ),
                        api_key=os.getenv("GEMINI_API_KEY"),
                    )
                elif model_provider == "openai":
                    model = OpenAIRealtimeModel(
                        model_name="gpt-4o-realtime-preview",
                        api_key=os.getenv("OPENAI_API_KEY"),
                    )
                else:
                    raise ValueError(
                        f"Unsupported model provider: {model_provider}",
                    )

                # Create the agent
                agent = RealtimeAgent(
                    name=agent_name,
                    sys_prompt=sys_prompt,
                    model=model,
                    toolkit=toolkit,
                )

                await agent.start(frontend_queue)

                # Send session_created event to frontend
                await websocket.send_json(
                    ServerEvents.ServerSessionCreatedEvent(
                        session_id=session_id,
                    ).model_dump(),
                )
                print(
                    f"Session created successfully: {session_id}",
                )

            elif client_event.type == ClientEventType.CLIENT_SESSION_END:
                # End the session with the agent
                if agent:
                    await agent.stop()
                    agent = None

            else:
                await agent.handle_input(client_event)

    except Exception as e:
        print(f"[ERROR] WebSocket endpoint error: {e}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    uvicorn.run(
        "run_server:app",
        host="localhost",
        port=8000,
        reload=True,
        log_level="info",
    )
