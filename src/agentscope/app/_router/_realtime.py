# -*- coding: utf-8 -*-
"""Realtime voice model discovery and WebRTC negotiation endpoints."""

import asyncio
import uuid
from typing import Any
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ...agent import RealtimeAgent
from ...credential import CredentialFactory
from ..deps import (
    get_current_user_id,
    get_message_bus,
    get_resource_access_service,
    get_realtime_service,
    get_storage,
)
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from .._service import (
    RealtimeService,
    ResourceAccessService,
    get_realtime_model,
)
from ._schema import (
    ListRealtimeModelsRequest,
    ListRealtimeModelsResponse,
    RealtimeConfigResponse,
    RealtimeOfferRequest,
    RealtimeOfferResponse,
)


realtime_router = APIRouter(
    prefix="/realtime",
    tags=["realtime"],
    responses={404: {"description": "Not found"}},
)

_SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS = 1.0


@realtime_router.get(
    "/models",
    response_model=ListRealtimeModelsResponse,
    summary="List realtime models for a credential provider",
)
async def list_realtime_models(
    body: ListRealtimeModelsRequest = Depends(),
) -> ListRealtimeModelsResponse:
    """Return realtime model cards supported by a credential provider."""
    credential_cls = CredentialFactory.get_credential_class(body.provider)
    if credential_cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {body.provider!r} not found.",
        )

    models = credential_cls.list_realtime_models()
    return ListRealtimeModelsResponse(models=models, total=len(models))


@realtime_router.get(
    "/config",
    response_model=RealtimeConfigResponse,
    summary="Get browser WebRTC configuration",
)
async def get_realtime_config(
    request: Request,
    _: str = Depends(get_current_user_id),
) -> RealtimeConfigResponse:
    """Return deployment-provided ICE servers for browser negotiation."""
    return RealtimeConfigResponse(
        ice_servers=request.app.state.realtime_ice_servers,
    )


@realtime_router.post(
    "/sessions/{session_id}/offer",
    response_model=RealtimeOfferResponse,
    summary="Start a realtime session from a WebRTC offer",
)
async def create_realtime_offer(
    session_id: str,
    body: RealtimeOfferRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
    access: ResourceAccessService = Depends(get_resource_access_service),
    realtime_service: RealtimeService = Depends(get_realtime_service),
) -> RealtimeOfferResponse:
    """Negotiate one browser peer connection for a persisted session."""
    connection_key = (user_id, session_id)
    offer_locks: WeakValueDictionary[
        tuple[str, str],
        asyncio.Lock,
    ] = request.app.state.realtime_offer_locks
    offer_lock = offer_locks.setdefault(connection_key, asyncio.Lock())
    async with offer_lock:
        return await _create_realtime_offer(
            session_id=session_id,
            body=body,
            request=request,
            user_id=user_id,
            storage=storage,
            message_bus=message_bus,
            access=access,
            realtime_service=realtime_service,
        )


async def _create_realtime_offer(
    *,
    session_id: str,
    body: RealtimeOfferRequest,
    request: Request,
    user_id: str,
    storage: StorageBase,
    message_bus: MessageBus,
    access: ResourceAccessService,
    realtime_service: RealtimeService,
) -> RealtimeOfferResponse:
    """Create one offer while this process serializes session starts."""
    await access.resolve_agent(user_id, body.agent_id)
    session = await storage.get_session(
        user_id,
        body.agent_id,
        session_id,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    connection_key = (user_id, session_id)
    connections: dict[
        tuple[str, str],
        Any,
    ] = request.app.state.realtime_connections
    previous = connections.get(connection_key)
    if previous is not None:
        await previous.close()

    # The old runner persists during close, so reload after it is gone.
    session = await storage.get_session(
        user_id,
        body.agent_id,
        session_id,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    config = session.config.realtime_model_config
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Configure a realtime model before starting voice mode.",
        )

    try:
        model = await get_realtime_model(user_id, config, access)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    lock_key = MessageBusKeys.session_lock(session_id)
    if await message_bus.is_locked(lock_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session {session_id!r} is already running.",
        )

    try:
        from aiortc import (
            RTCConfiguration,
            RTCIceServer,
            RTCPeerConnection,
            RTCSessionDescription,
        )

        from .._service._webrtc_audio_transport import (
            WebRTCAudioTransport,
        )
        from .._service._webrtc_session import WebRTCSession
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "WebRTC support is not installed. Install "
                "agentscope[realtime]."
            ),
        ) from exc

    ice_servers = [
        RTCIceServer(**server)
        for server in request.app.state.realtime_ice_servers
    ]
    peer_connection = RTCPeerConnection(
        configuration=RTCConfiguration(iceServers=ice_servers),
    )
    transport = WebRTCAudioTransport(
        input_sample_rate=model.input_sample_rate,
        output_sample_rate=model.output_sample_rate,
    )
    runner: WebRTCSession | None = None

    @peer_connection.on("track")
    def _on_track(track: Any) -> None:
        if track.kind == "audio":
            transport.set_input_track(track)

    @peer_connection.on("datachannel")
    def _on_datachannel(channel: Any) -> None:
        if channel.label == "control":
            transport.set_data_channel(channel)

    @peer_connection.on("connectionstatechange")
    def _on_connection_state_change() -> None:
        if runner is not None and peer_connection.connectionState in {
            "failed",
            "closed",
        }:
            runner.request_close()

    try:
        await peer_connection.setRemoteDescription(
            RTCSessionDescription(sdp=body.sdp, type=body.type),
        )
        peer_connection.addTrack(transport.output_track)
        answer = await peer_connection.createAnswer()
        await peer_connection.setLocalDescription(answer)
        local_description = peer_connection.localDescription
        if local_description is None:
            raise RuntimeError("WebRTC negotiation produced no answer.")

        async def _create_agent() -> RealtimeAgent:
            return await realtime_service.create_agent(
                user_id=user_id,
                agent_id=body.agent_id,
                session_id=session_id,
                model=model,
            )

        connection_id = uuid.uuid4().hex

        def _remove_closed(closed: WebRTCSession) -> None:
            if connections.get(connection_key) is closed:
                connections.pop(connection_key, None)

        runner = WebRTCSession(
            connection_id=connection_id,
            peer_connection=peer_connection,
            transport=transport,
            agent_factory=_create_agent,
            storage=storage,
            message_bus=message_bus,
            user_id=user_id,
            agent_id=body.agent_id,
            session_id=session_id,
            on_closed=_remove_closed,
        )
        transport.on_disconnect = runner.request_close
        connections[connection_key] = runner
        runner.start()
        if not await runner.wait_until_lock_acquired(
            _SESSION_LOCK_ACQUIRE_TIMEOUT_SECONDS,
        ):
            await runner.close()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Session {session_id!r} is already running.",
            )
        return RealtimeOfferResponse(
            connection_id=connection_id,
            sdp=local_description.sdp,
            type="answer",
        )
    except HTTPException:
        await transport.close()
        await peer_connection.close()
        raise
    except Exception as exc:
        await transport.close()
        await peer_connection.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"WebRTC negotiation failed: {exc}",
        ) from exc
