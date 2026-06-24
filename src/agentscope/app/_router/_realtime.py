# -*- coding: utf-8 -*-
"""Realtime WebSocket router — bidirectional audio bridge between the
browser and :class:`RealtimeAgent`.

The single ``WS /realtime/{session_id}`` endpoint manages the full
lifecycle: resolve the realtime model from the session's chat config,
create a :class:`RealtimeAgent`, and run two concurrent tasks:

- **Upstream** — read JSON frames from the browser (``{"type": "audio",
  "data": "<base64>"}``), wrap them in a ``DataBlock``, and forward to
  ``agent.send()``.
- **Downstream** — iterate ``agent.event_stream()``, serialise each
  ``AgentEvent``, and send it back as a JSON text frame.  Events are
  also published to the message bus so the SSE stream stays in sync.
  User transcriptions and assistant replies are persisted to the
  session's message store so they survive page refreshes.

The :class:`RealtimeAgent` handles tool execution internally when a
:class:`~agentscope.tool.Toolkit` is provided.
"""
import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .._service._realtime_model import get_realtime_model
from ..message_bus import MessageBus
from ..storage import StorageBase
from ..workspace_manager._base import WorkspaceManagerBase
from ..._logging import logger
from ...event import (
    ReplyStartEvent,
    ReplyEndEvent,
    UserConfirmResultEvent,
    UserInputAudioEndEvent,
    UserInputAudioStartEvent,
    UserInputTranscriptionEvent,
)
from ...message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    UserMsg,
)
from ...realtime import RealtimeAgent
from ...tool import Toolkit

realtime_router = APIRouter(
    prefix="/realtime",
    tags=["realtime"],
)

# The frontend mic always captures 16-bit mono PCM at this fixed rate.
# Used to tag incoming audio so the model layer can resample if its
# expected input rate differs (e.g. OpenAI expects 24 kHz).
_FRONTEND_CAPTURE_RATE = 16000

_active_sessions: dict[str, WebSocket] = {}
_session_connect_lock: asyncio.Lock = asyncio.Lock()


async def _build_realtime_toolkit(
    workspace_manager: WorkspaceManagerBase,
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace_id: str,
) -> Toolkit | None:
    """Build a lightweight :class:`Toolkit` for realtime tool execution.

    Args:
        workspace_manager (`WorkspaceManagerBase`):
            The workspace manager instance.
        user_id (`str`):
            The authenticated user id.
        agent_id (`str`):
            The agent id for workspace scoping.
        session_id (`str`):
            The session id for workspace scoping.
        workspace_id (`str`):
            The workspace identifier to resolve.

    Returns:
        `Toolkit | None`: A toolkit with workspace tools, or ``None``
            if the workspace cannot be resolved.
    """
    try:
        workspace = await workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        tools = await workspace.list_tools()
        if not tools:
            return None
        return Toolkit(tools=tools)
    except Exception:
        logger.debug(
            "Could not build realtime toolkit for session %s",
            session_id,
            exc_info=True,
        )
        return None


@realtime_router.websocket("/{session_id}")
async def realtime_ws(
    websocket: WebSocket,
    session_id: str,
    agent_id: str = Query(...),
    user_id: str = Query(...),
) -> None:
    """Bidirectional audio bridge for a realtime session.

    Manages the full WebSocket lifecycle: validates ownership, resolves
    the realtime model, creates a :class:`RealtimeAgent`, and runs
    concurrent upstream/downstream tasks until one side disconnects.

    Browsers cannot set custom headers on WebSocket connections, so
    ``user_id`` is passed as a query parameter (same security posture
    as the temporary ``X-User-ID`` header used elsewhere).

    Args:
        websocket (`WebSocket`):
            The incoming WebSocket connection from the browser.
        session_id (`str`):
            Path parameter — the session to connect.
        agent_id (`str`):
            Query parameter — the agent that owns the session.
        user_id (`str`):
            Query parameter — the authenticated user.
    """
    storage: StorageBase = websocket.app.state.storage
    message_bus: MessageBus = websocket.app.state.message_bus
    workspace_manager: WorkspaceManagerBase = (
        websocket.app.state.workspace_manager
    )

    # ---- Validate ownership ----
    session_record = await storage.get_session(
        user_id,
        agent_id,
        session_id,
    )
    if session_record is None:
        await websocket.close(code=4004, reason="Session not found.")
        return

    agent_record = await storage.get_agent(user_id, agent_id)
    if agent_record is None:
        await websocket.close(code=4004, reason="Agent not found.")
        return

    # ---- Prevent duplicate connections ----
    async with _session_connect_lock:
        if session_id in _active_sessions:
            await websocket.close(
                code=4009,
                reason="Realtime session already active.",
            )
            return
        # Reserve the slot early to prevent races during model setup.
        _active_sessions[session_id] = websocket  # type: ignore[assignment]

    # ---- Resolve realtime model ----
    model_cfg = session_record.config.realtime_model_config
    if not model_cfg:
        _active_sessions.pop(session_id, None)
        await websocket.close(
            code=4000,
            reason="No realtime model configured on this session.",
        )
        return

    try:
        model = await get_realtime_model(user_id, model_cfg, storage)
    except Exception as e:
        _active_sessions.pop(session_id, None)
        await websocket.close(code=4000, reason=str(e))
        return

    # ---- Build toolkit for tool execution ----
    toolkit: Toolkit | None = None
    if model.support_tools:
        workspace_id = session_record.config.workspace_id or agent_id
        toolkit = await _build_realtime_toolkit(
            workspace_manager,
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        if toolkit is None:
            logger.info(
                "Realtime session %s: model supports tools but no "
                "workspace tools found (workspace_id=%s).",
                session_id,
                workspace_id,
            )
    else:
        logger.debug(
            "Realtime session %s: model %s does not support tools.",
            session_id,
            model.model_name,
        )

    # ---- Accept connection ----
    await websocket.accept()

    # Trim stale replay-log events from any previous connection.
    # Without this, SSE subscribers would replay old audio/text events
    # causing unwanted audio autoplay on session switch.
    try:
        await message_bus.session_trim_events(session_id)
    except Exception:
        logger.warning(
            "Failed to trim stale replay log on connect for session %s",
            session_id,
            exc_info=True,
        )

    # ---- Create agent (reuse persisted state for context continuity) ----
    agent_state = session_record.state
    agent_state.session_id = session_id

    agent = RealtimeAgent(
        name=agent_record.data.name,
        model=model,
        instructions=agent_record.data.system_prompt,
        session_id=session_id,
        toolkit=toolkit,
        state=agent_state,
    )

    try:
        await agent.connect()

        upstream_task = asyncio.create_task(
            _upstream(
                websocket,
                agent,
                _FRONTEND_CAPTURE_RATE,
                storage,
                user_id,
                session_id,
            ),
            name=f"rt-upstream:{session_id}",
        )
        downstream_task = asyncio.create_task(
            _downstream(
                websocket,
                agent,
                message_bus,
                storage,
                user_id,
                session_id,
                agent_name=agent_record.data.name,
            ),
            name=f"rt-downstream:{session_id}",
        )

        done, pending = await asyncio.wait(
            [upstream_task, downstream_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        # Await cancelled tasks to suppress "exception never retrieved"
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task.exception() and not isinstance(
                task.exception(),
                (WebSocketDisconnect, asyncio.CancelledError),
            ):
                logger.error(
                    "Realtime task error for session %s: %s",
                    session_id,
                    task.exception(),
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "Realtime session error for %s",
            session_id,
        )
    finally:
        await agent.disconnect()
        # Persist the updated state so context survives reconnection
        # and is available when switching to text chat mode.
        try:
            await storage.update_session_state(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                state=agent.state,
            )
        except Exception:
            logger.warning(
                "Failed to persist realtime agent state for session %s",
                session_id,
                exc_info=True,
            )
        # Trim the replay log so that SSE subscribers reconnecting
        # after the realtime session ends see a clean slate instead
        # of replaying stale audio/text events (which would cause
        # duplicate message rendering and unwanted audio autoplay).
        try:
            await message_bus.session_trim_events(session_id)
        except Exception:
            logger.warning(
                "Failed to trim replay log for realtime session %s",
                session_id,
                exc_info=True,
            )
        _active_sessions.pop(session_id, None)


async def _upstream(
    websocket: WebSocket,
    agent: RealtimeAgent,
    capture_sample_rate: int,
    storage: StorageBase,
    user_id: str,
    session_id: str,
) -> None:
    """Read frames from the browser and forward to the agent.

    Supported frame types:

    - ``audio`` — base64 PCM audio forwarded via ``agent.send()``.
    - ``content`` — text and/or data blocks (images, files) sent as
      a user message. Persisted to storage and forwarded to the agent.
    - ``user_confirm`` — tool-call confirmation forwarded to
      ``agent.handle_user_confirm()`` so the pending permission
      future is resolved.

    Args:
        websocket (`WebSocket`):
            The active WebSocket connection from the browser.
        agent (`RealtimeAgent`):
            The agent to forward content to.
        capture_sample_rate (`int`):
            The actual PCM sample rate of the frontend microphone
            capture (used for media_type tagging).  The model layer
            will resample if its expected input rate differs.
        storage (`StorageBase`):
            Storage for persisting user messages.
        user_id (`str`):
            The authenticated user id.
        session_id (`str`):
            The session id for message persistence.
    """
    while True:
        raw = await websocket.receive_text()
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue

        frame_type = frame.get("type")

        if frame_type == "audio":
            data = frame.get("data", "")
            if data:
                await agent.send(
                    DataBlock(
                        source=Base64Source(
                            data=data,
                            media_type=f"audio/pcm;rate={capture_sample_rate}",
                        ),
                    ),
                )

        elif frame_type == "content":
            blocks = frame.get("blocks", [])
            if not blocks:
                continue

            content_blocks: list = []
            for blk in blocks:
                blk_type = blk.get("type")
                if blk_type == "text":
                    tb = TextBlock(text=blk.get("text", ""))
                    content_blocks.append(tb)
                    await agent.send(tb)
                elif blk_type == "data":
                    src = blk.get("source", {})
                    db = DataBlock(
                        source=Base64Source(
                            data=src.get("data", ""),
                            media_type=src.get("media_type", ""),
                        ),
                        name=blk.get("name"),
                    )
                    content_blocks.append(db)
                    await agent.send(db)

            if content_blocks:
                msg = UserMsg(
                    name="user",
                    content=content_blocks,
                )
                try:
                    await storage.upsert_message(
                        user_id,
                        session_id,
                        msg,
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist user content for session %s",
                        session_id,
                        exc_info=True,
                    )

        elif frame_type == "user_confirm":
            try:
                event = UserConfirmResultEvent(**frame.get("data", {}))
                agent.handle_user_confirm(event)
            except Exception:
                logger.warning(
                    "Failed to process user_confirm frame",
                    exc_info=True,
                )


async def _downstream(
    websocket: WebSocket,
    agent: RealtimeAgent,
    message_bus: MessageBus,
    storage: StorageBase,
    user_id: str,
    session_id: str,
    *,
    agent_name: str,  # pylint: disable=unused-argument
) -> None:
    """Forward agent events to the browser, publish to the bus, and
    persist user/assistant messages to the session's message store.

    Uses :meth:`Msg.append_event` — the same method the regular chat
    path uses — so that all content blocks (text, audio DataBlocks,
    tool calls, etc.) are accumulated and persisted together.  This
    ensures audio replay buttons survive session switches and page
    refreshes.

    Tool execution is handled internally by the :class:`RealtimeAgent`
    when a toolkit is configured.

    Args:
        websocket (`WebSocket`):
            The active WebSocket connection to the browser.
        agent (`RealtimeAgent`):
            The agent whose event stream to consume.
        message_bus (`MessageBus`):
            Bus for publishing events to SSE subscribers.
        storage (`StorageBase`):
            Storage for persisting messages.
        user_id (`str`):
            The authenticated user id.
        session_id (`str`):
            The session id for message persistence.
        agent_name (`str`):
            The agent display name (reserved for future use).
    """
    reply_msg: Msg | None = None
    # Track completed replies so post-ReplyEnd events (tool results,
    # confirmations) can still be appended and persisted.
    completed_replies: dict[str, Msg] = {}

    try:
        async for event in agent.event_stream():
            payload = event.model_dump(mode="json")
            try:
                await websocket.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                return
            await message_bus.session_publish_event(session_id, payload)

            # ---- Persist messages to storage ----
            if isinstance(event, UserInputTranscriptionEvent):
                if event.transcript:
                    msg = UserMsg(name="user", content=event.transcript)
                    try:
                        await storage.upsert_message(
                            user_id,
                            session_id,
                            msg,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist user transcript for "
                            "session %s",
                            session_id,
                            exc_info=True,
                        )

            elif isinstance(
                event,
                (UserInputAudioStartEvent, UserInputAudioEndEvent),
            ):
                pass

            elif isinstance(event, ReplyStartEvent):
                reply_msg = AssistantMsg(
                    name=event.name,
                    content=[],
                    id=event.reply_id,
                    created_at=event.created_at,
                )

            elif isinstance(event, ReplyEndEvent):
                if reply_msg is not None:
                    reply_msg.append_event(event)
                    try:
                        await storage.upsert_message(
                            user_id,
                            session_id,
                            reply_msg,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist assistant reply for "
                            "session %s",
                            session_id,
                            exc_info=True,
                        )
                    completed_replies[reply_msg.id] = reply_msg
                    reply_msg = None

            elif reply_msg is not None:
                reply_msg.append_event(event)

            else:
                # Post-ReplyEnd events (tool results, confirmations)
                # target a completed reply by reply_id.
                reply_id = getattr(event, "reply_id", None)
                if reply_id and reply_id in completed_replies:
                    target = completed_replies[reply_id]
                    target.append_event(event)
                    try:
                        await storage.upsert_message(
                            user_id,
                            session_id,
                            target,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist post-reply event for "
                            "session %s",
                            session_id,
                            exc_info=True,
                        )
    finally:
        # Persist any in-progress reply that didn't receive a
        # ReplyEndEvent before the session ended (e.g. WebSocket
        # disconnect during the response, or cancellation).  Without
        # this, audio accumulated during the reply is lost and
        # historical playback shows no audio for that message.
        if reply_msg is not None and reply_msg.content:
            try:
                await asyncio.shield(
                    storage.upsert_message(
                        user_id,
                        session_id,
                        reply_msg,
                    ),
                )
            except Exception:
                logger.warning(
                    "Failed to persist in-progress reply on disconnect "
                    "for session %s",
                    session_id,
                    exc_info=True,
                )
