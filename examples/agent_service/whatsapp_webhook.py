# -*- coding: utf-8 -*-
"""WhatsApp webhook routes for the Agent Service example."""
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agentscope.app.channel import WhatsAppChannel

router = APIRouter()


async def _running_channels(request: Request) -> list[WhatsAppChannel]:
    """Return WhatsApp channels running in this service process."""
    dispatcher = getattr(request.app.state, "channel_dispatcher", None)
    if dispatcher is None:
        return []
    channels: list[WhatsAppChannel] = []
    for record in await request.app.state.storage.list_all_channels():
        if record.channel_type != WhatsAppChannel.channel_type:
            continue
        channel = dispatcher.get_local_channel(record.id)
        if isinstance(channel, WhatsAppChannel):
            channels.append(channel)
    return channels


async def _channel_for_phone(
    request: Request,
    phone_number_id: str,
) -> WhatsAppChannel:
    """Resolve a destination phone number to its local channel."""
    dispatcher = getattr(request.app.state, "channel_dispatcher", None)
    channel_id = (
        await request.app.state.storage.get_channel_id_by_platform_bot_id(
            phone_number_id,
        )
    )
    channel = (
        dispatcher.get_local_channel(channel_id)
        if dispatcher is not None and channel_id
        else None
    )
    if not isinstance(channel, WhatsAppChannel):
        raise HTTPException(
            status_code=404,
            detail=(
                "No running WhatsApp channel matches the webhook's "
                "phone_number_id."
            ),
        )
    return channel


async def _deliveries(
    request: Request,
    payload: dict[str, Any],
) -> list[tuple[WhatsAppChannel, dict[str, Any]]]:
    """Partition a webhook batch by destination phone number id."""
    deliveries: list[tuple[WhatsAppChannel, dict[str, Any]]] = []
    for entry in payload.get("entry") or []:
        entry_base = {
            key: value for key, value in entry.items() if key != "changes"
        }
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = str(
                (value.get("metadata") or {}).get("phone_number_id", ""),
            )
            channel = await _channel_for_phone(request, phone_number_id)
            deliveries.append(
                (
                    channel,
                    {
                        "object": payload.get(
                            "object",
                            "whatsapp_business_account",
                        ),
                        "entry": [{**entry_base, "changes": [change]}],
                    },
                ),
            )
    return deliveries


@router.get("/webhooks/whatsapp")
async def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
    """Handle Meta's webhook subscription challenge."""
    params = request.query_params
    for channel in await _running_channels(request):
        challenge = channel.verify_webhook(
            params.get("hub.mode", ""),
            params.get("hub.verify_token", ""),
            params.get("hub.challenge", ""),
        )
        if challenge is not None:
            return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Invalid verify token")


@router.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(request: Request) -> dict[str, Any]:
    """Validate and enqueue a Meta WhatsApp webhook payload."""
    raw_body = await request.body()
    payload = await request.json()
    deliveries = await _deliveries(request, payload)
    signature = request.headers.get("x-hub-signature-256")
    if not deliveries or any(
        not channel.verify_webhook_signature(raw_body, signature)
        for channel, _ in deliveries
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook signature",
        )
    count = 0
    for channel, channel_payload in deliveries:
        count += await channel.handle_webhook(channel_payload)
    return {"ok": True, "accepted": count}
