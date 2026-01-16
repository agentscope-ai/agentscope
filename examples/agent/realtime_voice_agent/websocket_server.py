# -*- coding: utf-8 -*-
"""WebSocket server using Callback-based Voice Agent.

This example demonstrates the new callback-based architecture:
- RealtimeVoiceAgent: Agent with incoming_queue and callback pattern
- DashScopeRealtimeModel: Model that emits ModelEvents via callback
- EventMsgStream: Central queue with dispatch_loop
- ModelEvent/AgentEvent: Unified event system

Architecture:
    Browser → FastAPI WebSocket → EventMsgStream
                                      ↓
    DashScope ← Model ← Agent ← dispatch_loop
                  ↓         ↓
              callback   incoming_queue
                  ↓         ↓
        ModelEvents → AgentEvents → MsgStream.queue → dispatch → Browser

Usage:
    uvicorn websocket_server_v2:app --host 0.0.0.0 --port 8000

Requirements:
    pip install fastapi uvicorn websockets
"""

import asyncio
import base64
import os
import dataclasses
from typing import Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# Live Voice Agent imports
from agentscope.agent.realtime_voice_agent import (
    RealtimeVoiceAgent,
    DashScopeRealtimeModel,
    EventMsgStream,
    AgentEvent,
    AgentEventType,
    AgentSessionCreated,
    AgentResponseCreated,
    AgentResponseDelta,
    AgentResponseDone,
    AgentInputTranscriptionDelta,
    AgentInputTranscriptionDone,
    AgentInputStarted,
    AgentInputDone,
    AgentError,
    TextBlock,
    AudioBlock,
)
from agentscope import logger

app = FastAPI(title="VoiceAgent WebSocket Server V2")


def event_to_dict(event: AgentEvent) -> dict[str, Any]:
    """Convert AgentEvent to JSON-serializable dict for WebSocket."""
    result: dict[str, Any] = {
        "type": event.type.value,
        "agent_id": event.agent_id,
        "agent_name": event.agent_name,
    }

    # Add event-specific fields
    if isinstance(event, AgentSessionCreated):
        result["session_id"] = event.session_id

    elif isinstance(event, AgentResponseCreated):
        result["response_id"] = event.response_id

    elif isinstance(event, AgentResponseDelta):
        result["response_id"] = event.response_id
        delta = event.delta
        if isinstance(delta, TextBlock):
            result["delta"] = {
                "type": "text",
                "text": delta.text,
            }
        elif isinstance(delta, AudioBlock):
            result["delta"] = {
                "type": "audio",
                "data": delta.data,
                "media_type": delta.media_type,
            }
        else:
            result["delta"] = dataclasses.asdict(delta)

    elif isinstance(event, AgentResponseDone):
        result["response_id"] = event.response_id

    elif isinstance(event, AgentInputTranscriptionDelta):
        result["delta"] = event.delta
        result["item_id"] = event.item_id
        result["content_index"] = event.content_index

    elif isinstance(event, AgentInputTranscriptionDone):
        result["transcription"] = event.transcription
        result["item_id"] = event.item_id

    elif isinstance(event, AgentInputStarted):
        result["item_id"] = event.item_id
        result["audio_start_ms"] = event.audio_start_ms

    elif isinstance(event, AgentInputDone):
        result["item_id"] = event.item_id
        result["audio_end_ms"] = event.audio_end_ms

    elif isinstance(event, AgentError):
        result["error_type"] = event.error_type
        result["code"] = event.code
        result["message"] = event.message

    return result


class WebSocketVoiceSessionV2:
    """Voice session using Callback-based Voice Agent.

    Architecture:
    - RealtimeVoiceAgent: Receives ModelEvents via callback, outputs
    AgentEvents
    - DashScopeRealtimeModel: WebSocket communication with DashScope
    - EventMsgStream: Central queue with dispatch_loop
    """

    def __init__(self, session_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.websocket = websocket
        self.agent: Optional[RealtimeVoiceAgent] = None
        self.msg_stream: Optional[EventMsgStream] = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # Track accumulated text for streaming display
        self._accumulated_text: dict[str, str] = {}

    async def initialize(self) -> None:
        """Initialize the voice agent and model."""
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY environment variable not set")

        # 1. Create WebSocket Model (callback-based)
        model = DashScopeRealtimeModel(
            api_key=api_key,
            model_name="qwen3-omni-flash-realtime",
            voice="Cherry",
            instructions="You are a helpful voice assistant. "
            "Keep responses concise.",
            vad_enabled=True,
        )

        # 2. Create Agent (with incoming_queue)
        self.agent = RealtimeVoiceAgent(
            name="assistant",
            model=model,
            sys_prompt="You are a helpful voice assistant.",
        )

        # 3. Create MsgStream (with dispatch_loop)
        self.msg_stream = EventMsgStream(
            agents=[self.agent],
            queue_max_size=1000,
        )

        # 4. Register external callback for WebSocket forwarding
        self.msg_stream.on_event = self._on_agent_event

        # 5. Start MsgStream (which starts agents)
        await self.msg_stream.start()

        self._running = True
        logger.info("Session %s: Agent initialized", self.session_id)

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Callback for AgentEvents - forward to WebSocket.

        This is called by MsgStream's dispatch_loop for every AgentEvent.
        """
        if not self._running:
            return

        # Schedule async send in event loop
        asyncio.create_task(self._send_event_to_websocket(event))

    async def _send_event_to_websocket(self, event: AgentEvent) -> None:
        """Send AgentEvent to WebSocket client."""
        try:
            # Convert event to dict
            event_dict = event_to_dict(event)

            # Handle specific events for frontend compatibility
            if event.type == AgentEventType.RESPONSE_DELTA:
                assert isinstance(event, AgentResponseDelta)
                delta = event.delta

                if isinstance(delta, TextBlock):
                    # Accumulate text for streaming display
                    key = f"{event.agent_id}_{event.response_id}"
                    self._accumulated_text.setdefault(key, "")
                    self._accumulated_text[key] += delta.text

                    # Send accumulated text (matching old behavior)
                    await self.websocket.send_json(
                        {
                            "type": "text",
                            "name": event.agent_name,
                            "data": self._accumulated_text[key],
                            "is_partial": True,
                        },
                    )

                elif isinstance(delta, AudioBlock):
                    # Send audio directly
                    await self.websocket.send_json(
                        {
                            "type": "audio",
                            "name": event.agent_name,
                            "data": delta.data,
                            "sample_rate": 24000,  # Default PCM rate
                        },
                    )

            elif event.type == AgentEventType.RESPONSE_DONE:
                assert isinstance(event, AgentResponseDone)
                # Send final text
                key = f"{event.agent_id}_{event.response_id}"
                if key in self._accumulated_text:
                    await self.websocket.send_json(
                        {
                            "type": "text",
                            "name": event.agent_name,
                            "data": self._accumulated_text[key],
                            "is_partial": False,
                        },
                    )
                    del self._accumulated_text[key]

                # Send response_end event
                await self.websocket.send_json(
                    {
                        "type": "event",
                        "event": "response_end",
                        "name": event.agent_name,
                    },
                )
                logger.info("Session %s: Response complete", self.session_id)

            elif event.type == AgentEventType.SESSION_CREATED:
                await self.websocket.send_json(
                    {
                        "type": "event",
                        "event": "session_created",
                        **event_dict,
                    },
                )

            elif event.type == AgentEventType.ERROR:
                assert isinstance(event, AgentError)
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "message": event.message,
                        "code": event.code,
                    },
                )

            # Log other events
            elif event.type in (
                AgentEventType.INPUT_STARTED,
                AgentEventType.INPUT_DONE,
            ):
                logger.debug(
                    "Session %s: %s event",
                    self.session_id,
                    event.type.value,
                )

        except Exception as e:
            logger.error(
                "Session %s: Error sending event: %s",
                self.session_id,
                e,
            )

    async def start(self) -> None:
        """Start the session tasks."""
        if not self.agent or not self.msg_stream:
            raise RuntimeError("Session not initialized")

        # Task: Receive from WebSocket and inject to MsgStream
        self._tasks.append(
            asyncio.create_task(self._receive_from_websocket()),
        )

    async def _receive_from_websocket(self) -> None:
        """Receive audio from WebSocket and send to Agent's model."""
        if not self.agent:
            return

        audio_chunk_count = 0

        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(
                        self.websocket.receive_json(),
                        timeout=0.1,
                    )

                    if data.get("type") == "audio":
                        # Decode audio and send directly to model
                        audio_bytes = base64.b64decode(data["data"])
                        sample_rate = data.get("sample_rate", 16000)

                        audio_chunk_count += 1
                        if audio_chunk_count == 1:
                            logger.info(
                                "Session %s: First audio chunk received "
                                "(%d bytes)",
                                self.session_id,
                                len(audio_bytes),
                            )

                        # Send audio directly to model
                        self.agent.model.send_audio(audio_bytes, sample_rate)

                    elif data.get("type") == "control":
                        action = data.get("action")
                        logger.info(
                            "Session %s: Control: %s",
                            self.session_id,
                            action,
                        )
                        if action == "stop":
                            self._stop()
                            break
                        if action == "interrupt":
                            await self.agent.model.cancel_response()

                except asyncio.TimeoutError:
                    continue

        except WebSocketDisconnect:
            logger.info("Session %s: WebSocket disconnected", self.session_id)
            self._stop()
        except Exception as e:
            logger.error("Session %s: Error receiving: %s", self.session_id, e)

    def _stop(self) -> None:
        """Signal the session to stop."""
        self._running = False
        self._stop_event.set()

    async def wait_until_stopped(self) -> None:
        """Wait until the session is stopped."""
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Stop the session and cleanup."""
        self._stop()

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()

        # Stop MsgStream (which stops agents)
        if self.msg_stream:
            await self.msg_stream.stop()


# Store active sessions
sessions: dict[str, WebSocketVoiceSessionV2] = {}


@app.websocket("/ws/voice/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for voice conversations."""
    await websocket.accept()
    logger.info("Session %s: WebSocket connected", session_id)

    session = WebSocketVoiceSessionV2(session_id, websocket)

    try:
        await websocket.send_json(
            {
                "type": "event",
                "event": "connected",
                "session_id": session_id,
            },
        )

        logger.info("Session %s: Initializing agent...", session_id)
        await session.initialize()
        sessions[session_id] = session
        logger.info("Session %s: Agent initialized successfully", session_id)

        await websocket.send_json(
            {
                "type": "event",
                "event": "ready",
                "message": "Voice agent initialized",
            },
        )

        await session.start()
        logger.info("Session %s: Session started", session_id)

        # Wait for session to end
        await session.wait_until_stopped()

    except WebSocketDisconnect:
        logger.info("Session %s: Client disconnected", session_id)
    except Exception as e:
        logger.error("Session %s: Error: %s", session_id, e, exc_info=True)
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(e),
                },
            )
        except Exception:
            pass
    finally:
        logger.info("Session %s: Cleaning up...", session_id)
        await session.stop()
        if session_id in sessions:
            del sessions[session_id]
        logger.info("Session %s: Session ended", session_id)


@app.get("/", response_class=HTMLResponse)
async def get_frontend() -> str:
    """Serve the frontend HTML page."""
    html_path = os.path.join(
        os.path.dirname(__file__),
        "websocket_client.html",
    )
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html>
        <body>
            <h1>VoiceAgent WebSocket Server</h1>
            <p>WebSocket endpoint: ws://localhost:8000/ws/voice/{
            session_id}</p>
            <p>Please use websocket_client.html for the full frontend.</p>
            <p><b>Architecture:</b> Callback-based ModelEvent/AgentEvent</p>
        </body>
    </html>
    """


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "realtime_voice_agent",
        "active_sessions": len(sessions),
        "session_ids": list(sessions.keys()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
