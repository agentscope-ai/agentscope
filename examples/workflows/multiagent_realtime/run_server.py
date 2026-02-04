# -*- coding: utf-8 -*-
"""A multi-agent realtime voice interaction server using ChatRoom."""
import asyncio
import os
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

from agentscope import logger
from agentscope.agent import RealtimeAgent
from agentscope.message import TextBlock
from agentscope.pipeline import ChatRoom
from agentscope.realtime import (
    ClientEvents,
    ServerEvents,
    ClientEventType,
    DashScopeRealtimeModel,
    GeminiRealtimeModel,
    OpenAIRealtimeModel,
)

app = FastAPI()


@app.get("/")
async def get() -> FileResponse:
    """Serve the HTML test page."""
    html_path = Path(__file__).parent / "multi_agent.html"
    return FileResponse(html_path)


@app.get("/model_availability")
async def model_availability() -> dict:
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
    """Forward the message received from the agents to the frontend."""
    try:
        while True:
            msg: ServerEvents.EventBase = await frontend_queue.get()

            # Send the message as JSON
            await websocket.send_json(msg.model_dump())

    except Exception as e:
        print(f"[ERROR] frontend_receive error: {e}")
        traceback.print_exc()


@app.websocket("/ws/{user_id}/{session_id}")
async def multi_agent_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for multi-agent realtime voice interaction."""
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

        # Chat room and agents
        chat_room = None

        while True:
            # Handle the incoming messages from the frontend
            # i.e. ClientEvents
            data = await websocket.receive_json()

            client_event = ClientEvents.from_json(data)

            if isinstance(
                client_event,
                ClientEvents.ClientSessionCreateEvent,
            ):
                # Create agents by the given session arguments
                agent1_name = client_event.config.get("agent1_name", "Agent1")
                agent1_instructions = client_event.config.get(
                    "agent1_instructions",
                    "You are a helpful assistant.",
                )

                agent2_name = client_event.config.get("agent2_name", "Agent2")
                agent2_instructions = client_event.config.get(
                    "agent2_instructions",
                    "You are a helpful assistant.",
                )

                model_provider = client_event.config.get(
                    "model_provider",
                    "dashscope",
                )

                # Create the appropriate model based on provider
                if model_provider == "dashscope":
                    model1 = DashScopeRealtimeModel(
                        model_name="qwen3-omni-flash-realtime",
                        api_key=os.getenv("DASHSCOPE_API_KEY"),
                        voice="Dylan",
                        enable_input_audio_transcription=False,
                    )
                    model2 = DashScopeRealtimeModel(
                        model_name="qwen3-omni-flash-realtime",
                        api_key=os.getenv("DASHSCOPE_API_KEY"),
                        voice="Peter",
                        enable_input_audio_transcription=False,
                    )

                elif model_provider == "gemini":
                    model1 = GeminiRealtimeModel(
                        model_name=(
                            "gemini-2.5-flash-native-audio-preview-09-2025"
                        ),
                        api_key=os.getenv("GEMINI_API_KEY"),
                        voice="Puck",
                    )
                    model2 = GeminiRealtimeModel(
                        model_name=(
                            "gemini-2.5-flash-native-audio-preview-09-2025"
                        ),
                        api_key=os.getenv("GEMINI_API_KEY"),
                        voice="Charon",
                    )

                elif model_provider == "openai":
                    model1 = OpenAIRealtimeModel(
                        model_name="gpt-4o-realtime-preview",
                        api_key=os.getenv("OPENAI_API_KEY"),
                        voice="alloy",
                    )
                    model2 = OpenAIRealtimeModel(
                        model_name="gpt-4o-realtime-preview",
                        api_key=os.getenv("OPENAI_API_KEY"),
                        voice="echo",
                    )
                else:
                    raise ValueError(
                        f"Unsupported model provider: {model_provider}",
                    )

                # Create the first agent
                agent1 = RealtimeAgent(
                    name=agent1_name,
                    sys_prompt=agent1_instructions,
                    model=model1,
                )

                # Create the second agent
                agent2 = RealtimeAgent(
                    name=agent2_name,
                    sys_prompt=agent2_instructions,
                    model=model2,
                )

                # Create chat room with both agents
                chat_room = ChatRoom(agents=[agent1, agent2])

                await chat_room.start(frontend_queue)

                # Send session_created event to frontend
                await websocket.send_json(
                    ServerEvents.ServerSessionCreatedEvent(
                        session_id=session_id,
                    ).model_dump(),
                )

                await agent1.model.send(
                    TextBlock(
                        type="text",
                        text="<system>Now you can talk.</system>",
                    ),
                )

            elif client_event.type == ClientEventType.CLIENT_SESSION_END:
                # End the session with the chat room
                if chat_room:
                    await chat_room.stop()
                    chat_room = None

            else:
                # Forward other events to the chat room
                if chat_room:
                    await chat_room.handle_input(client_event)

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
