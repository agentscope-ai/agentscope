# -*- coding: utf-8 -*-
"""WhatsApp webhook routes for the Agent Service."""
from typing import Any

from ..channel import WhatsAppChannel
from ..message_bus import MessageBusKeys
from ..storage import ChannelRecord


def _channel_from_record(
    request: Any,
    record: ChannelRecord,
) -> WhatsAppChannel:
    """Build a short-lived adapter through the app's channel registry."""
    from fastapi import HTTPException

    registry = request.app.state.channel_type_registry
    channel = registry.create_channel_from_record(record)
    if not isinstance(channel, WhatsAppChannel):
        raise HTTPException(
            status_code=500,
            detail="The registered WhatsApp channel type is invalid.",
        )
    return channel


async def _channel_record_for_phone(
    request: Any,
    phone_number_id: str,
) -> ChannelRecord:
    """Resolve a WhatsApp phone number through shared storage."""
    from fastapi import HTTPException

    storage = request.app.state.storage
    channel_id = await storage.get_channel_id_by_platform_bot_id(
        phone_number_id,
    )
    record = await storage.get_channel(channel_id) if channel_id else None
    if (
        record is None
        or not record.enabled
        or record.channel_type != WhatsAppChannel.channel_type
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "No enabled WhatsApp channel matches the webhook's "
                "phone_number_id."
            ),
        )
    return record


async def _deliveries(
    request: Any,
    payload: dict[str, Any],
) -> list[tuple[ChannelRecord, str, dict[str, Any]]]:
    """Partition a webhook batch by channel and message id."""
    deliveries: list[tuple[ChannelRecord, str, dict[str, Any]]] = []
    for entry in payload.get("entry") or []:
        entry_base = {
            key: value for key, value in entry.items() if key != "changes"
        }
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = str(
                (value.get("metadata") or {}).get("phone_number_id", ""),
            )
            record = await _channel_record_for_phone(
                request,
                phone_number_id,
            )
            messages = value.get("messages") or []
            if not messages:
                deliveries.append(
                    (
                        record,
                        "",
                        {
                            "object": payload.get(
                                "object",
                                "whatsapp_business_account",
                            ),
                            "entry": [{**entry_base, "changes": [change]}],
                        },
                    ),
                )
                continue
            for message in messages:
                message_id = str(message.get("id", ""))
                if not message_id:
                    continue
                message_value = {**value, "messages": [message]}
                message_change = {**change, "value": message_value}
                deliveries.append(
                    (
                        record,
                        message_id,
                        {
                            "object": payload.get(
                                "object",
                                "whatsapp_business_account",
                            ),
                            "entry": [
                                {
                                    **entry_base,
                                    "changes": [message_change],
                                },
                            ],
                        },
                    ),
                )
    return deliveries


def create_whatsapp_webhook_router() -> Any:
    """Create the WhatsApp router, importing FastAPI only when enabled."""
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import PlainTextResponse

    router = APIRouter()

    @router.get("/webhooks/whatsapp")
    async def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
        """Handle Meta's webhook subscription challenge."""
        params = request.query_params
        for record in await request.app.state.storage.list_all_channels():
            if (
                not record.enabled
                or record.channel_type != WhatsAppChannel.channel_type
            ):
                continue
            channel = _channel_from_record(request, record)
            challenge = channel.verify_webhook(
                params.get("hub.mode", ""),
                params.get("hub.verify_token", ""),
                params.get("hub.challenge", ""),
            )
            if challenge is not None:
                return PlainTextResponse(challenge)
        raise HTTPException(status_code=403, detail="Invalid verify token")

    @router.post("/webhooks/whatsapp")
    async def receive_whatsapp_webhook(
        request: Request,
    ) -> dict[str, Any]:
        """Validate and persist a Meta WhatsApp webhook payload."""
        raw_body = await request.body()
        payload = await request.json()
        deliveries = await _deliveries(request, payload)
        if not deliveries:
            raise HTTPException(
                status_code=403,
                detail="Invalid WhatsApp webhook payload",
            )
        signature = request.headers.get("x-hub-signature-256")

        for record, _, _ in deliveries:
            channel = _channel_from_record(request, record)
            if not channel.verify_webhook_signature(raw_body, signature):
                raise HTTPException(
                    status_code=403,
                    detail="Invalid webhook signature",
                )

        accepted = 0
        channel_ids: set[str] = set()
        message_bus = request.app.state.message_bus
        for record, message_id, channel_payload in deliveries:
            if not message_id:
                continue
            await message_bus.log_append(
                MessageBusKeys.channel_webhook_queue(record.id),
                {
                    "channel_id": record.id,
                    "message_id": message_id,
                    "payload": channel_payload,
                },
            )
            channel_ids.add(record.id)
            accepted += 1
        if accepted:
            await message_bus.publish(
                MessageBusKeys.channel_webhook_signal(),
                {
                    "accepted": accepted,
                    "channel_ids": sorted(channel_ids),
                },
            )
        return {"ok": True, "accepted": accepted}

    return router


__all__ = ["create_whatsapp_webhook_router"]
