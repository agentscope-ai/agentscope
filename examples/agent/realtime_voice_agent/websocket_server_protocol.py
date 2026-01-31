# -*- coding: utf-8 -*-
# pylint: disable=too-many-nested-blocks, too-many-branches
# pylint: disable=too-many-return-statements, too-many-statements
"""WebSocket server using the new protocol design.

This example demonstrates the protocol defined in PROTOCOL_DESIGN.md:
- Client Events: client.session.update, client.audio.append, etc.
- Server Events: server.session.created, server.audio.delta, etc.

Architecture:
    Browser → FastAPI WebSocket → Protocol Handler
                                      ↓
    DashScope ← Model ← Agent ← dispatch_loop
                  ↓         ↓
              callback   incoming_queue
                  ↓         ↓
        ModelEvents → ServerEvents → WebSocket → Browser

Usage:
    uvicorn websocket_server_protocol:app --host 0.0.0.0 --port 8000

Requirements:
    pip install fastapi uvicorn websockets
"""

import asyncio
import base64
import os
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# Live Voice Agent imports
from agentscope.agent.realtime_voice_agent import (
    RealtimeVoiceAgent,
    DashScopeRealtimeModel,
    EventMsgStream,
    AgentEvent,
    AgentSessionCreated,
    AgentResponseCreated,
    AgentResponseDelta,
    AgentResponseDone,
    AgentInputTranscriptionDone,
    AgentInputStarted,
    AgentInputDone,
    AgentError,
    TextBlock,
    AudioBlock,
    ToolUseBlock,
)

# Protocol imports
from agentscope.agent.realtime_voice_agent.protocol import (
    # Client Events
    deserialize_client_event,
    ClientSessionUpdate,
    ClientSessionEnd,
    ClientAudioAppend,
    ClientAudioCommit,
    ClientTextSend,
    ClientImageAppend,
    ClientResponseCreate,
    ClientResponseCancel,
    ClientToolResult,
    ClientPing,
    # Server Events
    ServerSessionCreated,
    ServerSessionUpdated,
    ServerTurnStarted,
    ServerResponseStarted,
    ServerResponseDone,
    ServerAudioDelta,
    ServerTextDelta,
    ServerTranscriptUser,
    ServerSpeechStarted,
    ServerSpeechStopped,
    ServerToolCall,
    ServerPong,
    ServerError,
    # Serialization
    serialize_event,
)

from agentscope import logger

app = FastAPI(title="VoiceAgent WebSocket Server (Protocol)")


def generate_event_id() -> str:
    """Generate a unique event ID."""
    import uuid

    return f"evt_{uuid.uuid4().hex[:12]}"


def get_timestamp_ms() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


class ProtocolWebSocketSession:
    """Voice session using the new protocol design.

    This session:
    1. Receives client events (client.*)
    2. Converts to Agent operations
    3. Converts AgentEvents to server events (server.*)
    4. Sends server events to WebSocket
    """

    def __init__(self, session_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.websocket = websocket
        self.agent: RealtimeVoiceAgent | None = None
        self.msg_stream: EventMsgStream | None = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # State
        self._current_turn_id: str | None = None
        self._current_response_id: str | None = None
        self._audio_chunk_count = 0

    async def initialize(self) -> None:
        """Initialize the voice agent and model."""
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY environment variable not set")

        # 1. Create WebSocket Model
        model = DashScopeRealtimeModel(
            api_key=api_key,
            model_name="qwen3-omni-flash-realtime",
            voice="Cherry",
            instructions="You are a helpful voice assistant. "
            "Keep responses concise.",
            vad_enabled=True,
        )

        # 2. Create Agent
        self.agent = RealtimeVoiceAgent(
            name="assistant",
            model=model,
            sys_prompt="You are a helpful voice assistant.",
        )

        # 3. Create MsgStream
        self.msg_stream = EventMsgStream(
            agents=[self.agent],
            queue_max_size=1000,
        )

        # 4. Register callback
        self.msg_stream.on_event = self._on_agent_event

        # 5. Start MsgStream
        await self.msg_stream.start()

        self._running = True
        logger.info("Session %s: Agent initialized", self.session_id)

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Convert AgentEvent to ServerEvent and send."""
        if not self._running:
            return
        asyncio.create_task(self._handle_agent_event(event))

    async def _handle_agent_event(self, event: AgentEvent) -> None:
        """Convert AgentEvent to protocol ServerEvent and send."""
        try:
            server_event = self._convert_to_server_event(event)
            if server_event:
                await self._send_server_event(server_event)
        except Exception as e:
            logger.error(
                "Session %s: Error handling event: %s",
                self.session_id,
                e,
            )

    def _convert_to_server_event(self, event: AgentEvent) -> Any | None:
        """Convert AgentEvent to protocol ServerEvent."""

        # Session created - skip since we send it manually in start()
        if isinstance(event, AgentSessionCreated):
            # Already sent in start(), skip duplicate
            return None

        # Response created (response started)
        if isinstance(event, AgentResponseCreated):
            self._current_response_id = event.response_id
            return ServerResponseStarted(
                session_id=self.session_id,
                turn_id=self._current_turn_id,
                response_id=event.response_id,
            )

        # Response delta (audio/text/tool)
        if isinstance(event, AgentResponseDelta):
            delta = event.delta

            if isinstance(delta, AudioBlock):
                return ServerAudioDelta(
                    session_id=self.session_id,
                    response_id=event.response_id,
                    data=delta.data,
                    sample_rate=24000,  # Output is always 24kHz PCM
                )

            if isinstance(delta, TextBlock):
                return ServerTextDelta(
                    session_id=self.session_id,
                    response_id=event.response_id,
                    text=delta.text,
                )

            if isinstance(delta, ToolUseBlock):
                return ServerToolCall(
                    session_id=self.session_id,
                    response_id=event.response_id,
                    tool_call_id=delta.id,
                    tool_name=delta.name,
                    arguments=delta.input,
                )

        # Response done
        if isinstance(event, AgentResponseDone):
            return ServerResponseDone(
                session_id=self.session_id,
                response_id=event.response_id,
                usage={
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                }
                if hasattr(event, "input_tokens")
                else None,
            )

        # Input transcription done (user said)
        if isinstance(event, AgentInputTranscriptionDone):
            return ServerTranscriptUser(
                session_id=self.session_id,
                turn_id=self._current_turn_id,
                text=event.transcription,
            )

        # Input started (speech started)
        if isinstance(event, AgentInputStarted):
            # Generate new turn ID
            self._current_turn_id = f"turn_{generate_event_id()}"
            # Send both turn started and speech started
            asyncio.create_task(
                self._send_server_event(
                    ServerTurnStarted(
                        session_id=self.session_id,
                        turn_id=self._current_turn_id,
                    ),
                ),
            )
            return ServerSpeechStarted(
                session_id=self.session_id,
                turn_id=self._current_turn_id,
            )

        # Input done (speech stopped)
        if isinstance(event, AgentInputDone):
            return ServerSpeechStopped(
                session_id=self.session_id,
                turn_id=self._current_turn_id,
            )

        # Error
        if isinstance(event, AgentError):
            return ServerError(
                session_id=self.session_id,
                code=event.code,
                message=event.message,
                retryable=False,
            )

        return None

    async def _send_server_event(self, event: Any) -> None:
        """Serialize and send server event."""
        try:
            json_str = serialize_event(event)
            await self.websocket.send_text(json_str)
        except Exception as e:
            logger.error(
                "Session %s: Error sending event: %s",
                self.session_id,
                e,
            )

    async def start(self) -> None:
        """Start the session."""
        if not self.agent or not self.msg_stream:
            raise RuntimeError("Session not initialized")

        # Send session created
        await self._send_server_event(
            ServerSessionCreated(
                session_id=self.session_id,
                agent_name=self.agent.name,
            ),
        )

        # Start receive task
        self._tasks.append(
            asyncio.create_task(self._receive_from_websocket()),
        )

    async def _receive_from_websocket(self) -> None:
        """Receive client events and process."""
        if not self.agent:
            return

        try:
            while self._running:
                try:
                    # Receive text message
                    text = await asyncio.wait_for(
                        self.websocket.receive_text(),
                        timeout=0.1,
                    )

                    # Deserialize client event
                    try:
                        event = deserialize_client_event(text)
                        await self._handle_client_event(event)
                    except ValueError as e:
                        logger.warning(
                            "Session %s: Invalid client event: %s",
                            self.session_id,
                            e,
                        )
                        await self._send_server_event(
                            ServerError(
                                session_id=self.session_id,
                                code="INVALID_EVENT",
                                message=str(e),
                                retryable=False,
                            ),
                        )

                except asyncio.TimeoutError:
                    continue

        except WebSocketDisconnect:
            logger.info("Session %s: WebSocket disconnected", self.session_id)
            self._stop()
        except Exception as e:
            logger.error("Session %s: Error receiving: %s", self.session_id, e)

    async def _handle_client_event(self, event: Any) -> None:
        """Handle client events."""

        # Session update
        if isinstance(event, ClientSessionUpdate):
            logger.info("Session %s: Session update received", self.session_id)
            config = event.config if isinstance(event.config, dict) else {}

            # Apply configuration to the model
            if config and hasattr(self.agent.model, "update_session"):
                try:
                    await self.agent.model.update_session(config)
                    logger.info(
                        "Session %s: Config applied: %s",
                        self.session_id,
                        list(config.keys()),
                    )
                except Exception as e:
                    logger.warning(
                        "Session %s: Failed to update session: %s",
                        self.session_id,
                        e,
                    )

            # Send confirmation
            await self._send_server_event(
                ServerSessionUpdated(
                    session_id=self.session_id,
                    config=config,
                ),
            )

        # Session end
        elif isinstance(event, ClientSessionEnd):
            logger.info("Session %s: Session end requested", self.session_id)
            self._stop()

        # Audio append
        elif isinstance(event, ClientAudioAppend):
            audio_bytes = base64.b64decode(event.data)
            sample_rate = getattr(event, "sample_rate", 16000)
            self._audio_chunk_count += 1

            if self._audio_chunk_count == 1:
                logger.info(
                    "Session %s: First audio chunk (%d bytes, %d Hz)",
                    self.session_id,
                    len(audio_bytes),
                    sample_rate,
                )

            # Send to model (will resample if needed)
            self.agent.model.send_audio(audio_bytes, sample_rate)

        # Audio commit (manual mode)
        elif isinstance(event, ClientAudioCommit):
            logger.info("Session %s: Audio commit", self.session_id)
            # Trigger response in manual mode
            await self.agent.model.create_response()

        # Image append
        elif isinstance(event, ClientImageAppend):
            if self._audio_chunk_count == 0:
                logger.warning(
                    "Session %s: Image before audio, skipping",
                    self.session_id,
                )
                return

            image_bytes = base64.b64decode(event.image)
            logger.debug(
                "Session %s: Image received (%d bytes)",
                self.session_id,
                len(image_bytes),
            )

            if hasattr(self.agent.model, "send_image"):
                mime_type = getattr(event, "mime_type", "image/jpeg")
                self.agent.model.send_image(image_bytes, mime_type)

        # Text send
        elif isinstance(event, ClientTextSend):
            text = event.text
            if text:
                logger.info(
                    "Session %s: Text received: %s",
                    self.session_id,
                    text[:50] + "..." if len(text) > 50 else text,
                )
                if hasattr(self.agent.model, "send_text"):
                    self.agent.model.send_text(text)
                else:
                    logger.warning(
                        "Session %s: Model does not support text input",
                        self.session_id,
                    )

        # Response create (manual mode)
        elif isinstance(event, ClientResponseCreate):
            logger.info("Session %s: Response create", self.session_id)
            await self.agent.model.create_response()

        # Response cancel
        elif isinstance(event, ClientResponseCancel):
            logger.info("Session %s: Response cancel", self.session_id)
            await self.agent.model.cancel_response()

        # Tool result
        elif isinstance(event, ClientToolResult):
            logger.info(
                "Session %s: Tool result for %s",
                self.session_id,
                event.tool_call_id,
            )
            result = event.result
            if isinstance(result, dict):
                import json

                result = json.dumps(result)
            await self.agent.model.send_tool_result(
                event.tool_call_id,
                "",  # tool_name not needed
                str(result),
            )

        # Ping
        elif isinstance(event, ClientPing):
            await self._send_server_event(
                ServerPong(
                    session_id=self.session_id,
                    timestamp=get_timestamp_ms(),
                ),
            )

    def _stop(self) -> None:
        """Signal session to stop."""
        self._running = False
        self._stop_event.set()

    async def wait_until_stopped(self) -> None:
        """Wait until stopped."""
        await self._stop_event.wait()

    async def stop(self) -> None:
        """Stop and cleanup."""
        self._stop()

        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()

        if self.msg_stream:
            await self.msg_stream.stop()


# Store active sessions
sessions: dict[str, ProtocolWebSocketSession] = {}


@app.websocket("/ws/voice/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint using the new protocol."""
    await websocket.accept()
    logger.info("Session %s: WebSocket connected", session_id)

    session = ProtocolWebSocketSession(session_id, websocket)

    try:
        logger.info("Session %s: Initializing agent...", session_id)
        await session.initialize()
        sessions[session_id] = session
        logger.info("Session %s: Agent initialized", session_id)

        await session.start()
        logger.info("Session %s: Session started", session_id)

        await session.wait_until_stopped()

    except WebSocketDisconnect:
        logger.info("Session %s: Client disconnected", session_id)
    except Exception as e:
        logger.error("Session %s: Error: %s", session_id, e, exc_info=True)
        try:
            # pylint: disable=protected-access
            await session._send_server_event(
                ServerError(
                    session_id=session_id,
                    code="INTERNAL_ERROR",
                    message=str(e),
                    retryable=False,
                ),
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
    """Serve frontend."""
    html_path = os.path.join(
        os.path.dirname(__file__),
        "websocket_client_protocol.html",
    )
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return """
    <html>
        <body>
            <h1>VoiceAgent WebSocket Server (Protocol)</h1>
            <p>WebSocket endpoint:
             ws://localhost:8000/ws/voice/{session_id}</p>
            <p>Protocol: PROTOCOL_DESIGN.md</p>
        </body>
    </html>
    """


@app.get("/health")
async def health_check() -> dict:
    """Health check."""
    return {
        "status": "healthy",
        "version": "protocol_v1",
        "active_sessions": len(sessions),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
