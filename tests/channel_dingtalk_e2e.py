# -*- coding: utf-8 -*-
"""Manual end-to-end checks for the DingTalk channel.

These checks use a real DingTalk internal application and intentionally do
not run as part of the pytest suite. Credentials are accepted only through
environment variables so they cannot be exposed in shell history::

    export DINGTALK_CLIENT_ID=ding...
    export DINGTALK_CLIENT_SECRET=...
    export DINGTALK_APPROVAL_CARD_TEMPLATE_ID=....schema
    export DINGTALK_STREAMING_CARD_TEMPLATE_ID=....schema  # optional
    uv run python tests/channel_dingtalk_e2e.py direct

Available scenarios are ``direct``, ``group``, ``approval``, ``streaming``,
``shutdown``, and ``all``. Follow the interactive instructions printed by
the selected scenario.
"""

import argparse
import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
import json
import os
from typing import Any, TypeAlias
from uuid import uuid4

from agentscope.app.channel import DingTalkChannel
from agentscope.app.channel._base import (
    ChannelConfirmationResultEvent,
    ChannelEvent,
)
from agentscope.app.channel._dingtalk._card import _approval_card_data
from agentscope.app.channel._dingtalk._channel import _AI_CARD_TEMPLATE_ID
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from agentscope.message import DataBlock

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Z3xkAAAAASUVORK5CYII=",
)
_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
_Event: TypeAlias = ChannelEvent | ChannelConfirmationResultEvent
_Emitter = Callable[[_Event], Awaitable[None]]


def _credentials() -> DingTalkChannel.Credentials:
    """Read real-application credentials from the environment."""
    client_id = os.environ.get("DINGTALK_CLIENT_ID", "")
    client_secret = os.environ.get("DINGTALK_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError(
            "Set DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET first.",
        )
    return DingTalkChannel.Credentials(
        client_id=client_id,
        client_secret=client_secret,
    )


def _template_id(variable: str) -> str:
    """Read a required Card Platform template id."""
    value = os.environ.get(variable, "")
    if not value:
        raise ValueError(f"Set {variable} to a published template id.")
    return value


def _channel(
    name: str,
    **config: Any,
) -> DingTalkChannel:
    """Construct a real DingTalk channel for one isolated scenario."""
    config.setdefault("only_at_reply", False)
    return DingTalkChannel(
        name,
        _credentials(),
        DingTalkChannel.Config(**config),
    )


async def _run_until(
    channel: DingTalkChannel,
    emit: _Emitter,
    completed: asyncio.Event,
    instruction: str,
    timeout: float,
) -> bool:
    """Run a listener until a scenario completes or times out."""
    listener = asyncio.create_task(channel.start_listening(emit))
    try:
        for _ in range(150):
            if channel.status.state == "connected":
                print(f"READY: {instruction}", flush=True)
                break
            if channel.status.state == "failed":
                print("FAIL: Stream connection failed", flush=True)
                return False
            await asyncio.sleep(0.2)
        else:
            print("FAIL: Stream connection timed out", flush=True)
            return False

        try:
            await asyncio.wait_for(completed.wait(), timeout=timeout)
        except TimeoutError:
            print("FAIL: interactive scenario timed out", flush=True)
            return False
        return True
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


async def _direct(timeout: float) -> bool:
    """Check direct callbacks, discovery, users, and bidirectional media."""
    channel = _channel("dingtalk-direct-e2e")
    completed = asyncio.Event()
    results: dict[str, bool] = {}

    def maybe_complete() -> None:
        required = (
            "inbound_text",
            "conversation",
            "user_search",
            "target_text",
            "target_image",
            "target_file",
            "inbound_image",
            "inbound_file",
        )
        if all(results.get(key) for key in required):
            completed.set()

    async def emit(event: _Event) -> None:
        if not isinstance(event, ChannelEvent):
            return
        data_blocks = [
            block for block in event.content if isinstance(block, DataBlock)
        ]
        for block in data_blocks:
            media_type = block.source.media_type
            if media_type.startswith("image/"):
                results["inbound_image"] = True
                print("PASS: inbound image converted to DataBlock", flush=True)
            else:
                results["inbound_file"] = True
                print("PASS: inbound file converted to DataBlock", flush=True)
        if data_blocks:
            maybe_complete()
            return
        if results.get("inbound_text"):
            return

        results["inbound_text"] = event.metadata.get("chat_type") == "private"
        print(
            (
                "PASS: direct Stream callback normalized"
                if results["inbound_text"]
                else "FAIL: callback is not a direct message"
            ),
            flush=True,
        )
        api = channel._openapi  # pylint: disable=protected-access
        if api is None:
            print("FAIL: OpenAPI client unavailable", flush=True)
            completed.set()
            return

        conversations = await channel.list_bot_chats()
        results["conversation"] = any(
            item.get("chat_id") == event.chat_id for item in conversations
        )
        print(
            (
                "PASS: observed direct conversation listed"
                if results["conversation"]
                else "FAIL: direct conversation is missing"
            ),
            flush=True,
        )

        users = await api.search_users(event.channel_user_name, 20)
        results["user_search"] = any(
            user.get("user_id") == event.channel_user_id for user in users
        )
        print(
            (
                "PASS: user search returned the inbound user"
                if results["user_search"]
                else "FAIL: inbound user is missing from search"
            ),
            flush=True,
        )

        target = f"user:{event.channel_user_id}"
        results["target_text"] = await api.send_text(
            target,
            "**AgentScope DingTalk E2E**\n\nTargeted Markdown passed.",
        )
        results["target_image"] = await api.send_media(
            target,
            _PNG,
            "agentscope-e2e.png",
            "image/png",
        )
        results["target_file"] = await api.send_media(
            target,
            _PDF,
            "agentscope-e2e.pdf",
            "application/pdf",
        )
        for key, label in (
            ("target_text", "targeted Markdown"),
            ("target_image", "targeted image"),
            ("target_file", "targeted file"),
        ):
            print(
                (
                    f"PASS: {label} accepted"
                    if results[key]
                    else f"FAIL: {label} rejected"
                ),
                flush=True,
            )
        maybe_complete()

    finished = await _run_until(
        channel,
        emit,
        completed,
        "send a direct text message, then one image and one PDF",
        timeout,
    )
    return finished and all(results.values())


async def _group(timeout: float) -> bool:
    """Check group callback normalization, discovery, and targeted send."""
    channel = _channel("dingtalk-group-e2e", only_at_reply=True)
    completed = asyncio.Event()
    succeeded = False

    async def emit(event: _Event) -> None:
        nonlocal succeeded
        if not isinstance(event, ChannelEvent) or completed.is_set():
            return
        is_group = event.metadata.get("chat_type") == "group"
        conversations = await channel.list_bot_chats()
        discovered = any(
            item.get("chat_id") == event.chat_id
            and item.get("chat_type") == "group"
            for item in conversations
        )
        api = channel._openapi  # pylint: disable=protected-access
        sent = bool(
            api
            and await api.send_text(
                event.chat_id,
                "**AgentScope group E2E**\n\nTargeted group Markdown passed.",
            ),
        )
        for ok, label in (
            (is_group, "group callback normalized"),
            (discovered, "observed group conversation listed"),
            (sent, "targeted group Markdown accepted"),
        ):
            print(f"{'PASS' if ok else 'FAIL'}: {label}", flush=True)
        succeeded = is_group and discovered and sent
        completed.set()

    finished = await _run_until(
        channel,
        emit,
        completed,
        "mention the robot in a test group",
        timeout,
    )
    return finished and succeeded


async def _approval(timeout: float) -> bool:
    """Check approval-card delivery and both callback decisions."""
    template_id = _template_id("DINGTALK_APPROVAL_CARD_TEMPLATE_ID")
    channel = _channel(
        "dingtalk-approval-e2e",
        approval_card_template_id=template_id,
    )
    completed = asyncio.Event()
    run_id = uuid4().hex
    inbound: ChannelEvent | None = None
    expected = True
    current_tool_call_id = ""
    results: list[bool] = []

    async def deliver(decision: bool) -> bool:
        nonlocal current_tool_call_id
        if inbound is None:
            return False
        api = channel._openapi  # pylint: disable=protected-access
        if api is None:
            return False
        label = "approve" if decision else "deny"
        current_tool_call_id = f"e2e-{run_id}-{label}"
        out_track_id = await api.create_approval_card(
            inbound.chat_id,
            inbound.channel_user_id,
            template_id,
            _approval_card_data(
                current_tool_call_id,
                inbound.chat_id,
                "SendMessage",
                '{"target":"user:e2e","text":"approval E2E"}',
                inbound.channel_user_id,
                f"e2e-agent-{run_id}",
                f"e2e-session-{run_id}",
            ),
        )
        if out_track_id:
            print(
                f"READY: click {'approve' if decision else 'reject'}",
                flush=True,
            )
        else:
            print("FAIL: approval card was not delivered", flush=True)
        return bool(out_track_id)

    async def emit(event: _Event) -> None:
        nonlocal inbound, expected
        if isinstance(event, ChannelEvent):
            if inbound is not None:
                return
            inbound = event
            if not await deliver(True):
                completed.set()
            return
        if event.tool_call_id != current_tool_call_id:
            print("IGNORED: callback from another card", flush=True)
            return
        matched = event.approved is expected
        results.append(matched)
        print(
            f"{'PASS' if matched else 'FAIL'}: "
            f"{'approval' if expected else 'denial'} callback routed",
            flush=True,
        )
        if expected:
            expected = False
            if not await deliver(False):
                completed.set()
        else:
            completed.set()

    finished = await _run_until(
        channel,
        emit,
        completed,
        "send a direct message to receive the first approval card",
        timeout,
    )
    return finished and results == [True, True]


async def _probe_card(timeout: float) -> bool:
    """Dump what the public AI card sends back when a button is clicked.

    Approval currently needs a template the operator builds, because a
    template is the only place routing data can ride. The SDK's public AI
    card takes buttons at send time instead, which would remove that
    requirement — if a click reports which button it was. Nothing states
    whether it does, so ask a real application: this prints the whole
    callback rather than asserting anything about it.
    """
    channel = _channel("dingtalk-probe-card-e2e")
    completed = asyncio.Event()
    seen: list[dict[str, Any]] = []

    async def on_card(payload: dict[str, Any]) -> None:
        seen.append(payload)
        print(
            f"\nCALLBACK {len(seen)}:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2),
            flush=True,
        )
        if len(seen) >= 2:
            completed.set()

    # Replace the parser: it drops anything without routing fields, which
    # is exactly what this probe needs to see.
    channel._on_card_callback = on_card  # pylint: disable=protected-access

    buttons = {
        "order": ["msgTitle", "msgContent", "msgButtons"],
        "msgButtons": [
            {
                "text": "Approve",
                "color": "blue",
                "id": "agree",
                "request": True,
            },
            {
                "text": "Deny",
                "color": "red",
                "id": "reject",
                "request": True,
            },
        ],
    }

    async def emit(event: _Event) -> None:
        if not isinstance(event, ChannelEvent) or seen:
            return
        api = channel._openapi  # pylint: disable=protected-access
        if api is None:
            completed.set()
            return
        # pylint: disable-next=protected-access
        create = api._create_and_deliver_card
        out_track_id = await create(
            event.chat_id,
            _AI_CARD_TEMPLATE_ID,
            {
                "msgTitle": "AgentScope approval probe",
                "msgContent": "**Tool:** `SendMessage`",
                "sys_full_json_obj": json.dumps(buttons),
            },
        )
        print(f"CARD outTrackId={out_track_id!r}", flush=True)
        if not out_track_id:
            print("FAIL: the public AI card was not delivered", flush=True)
            completed.set()
            return
        print("READY: click Approve, then click Deny", flush=True)

    finished = await _run_until(
        channel,
        emit,
        completed,
        "send a direct message to receive the probe card",
        timeout,
    )
    return finished and len(seen) >= 2


async def _streaming_events() -> AsyncIterator[dict[str, Any]]:
    """Yield a small Agent event stream with visible update intervals."""
    reply_id = f"dingtalk-streaming-e2e-{uuid4().hex}"
    for start_event in (
        ReplyStartEvent(
            session_id="dingtalk-streaming-e2e",
            reply_id=reply_id,
            name="assistant",
        ),
        TextBlockStartEvent(reply_id=reply_id, block_id="text-1"),
    ):
        yield start_event.model_dump(mode="json")
    for delta in (
        "# AgentScope DingTalk streaming E2E",
        "\n\n- AI card created",
        "\n- Incremental Markdown updated",
        "\n- Stream finalized successfully",
    ):
        yield TextBlockDeltaEvent(
            reply_id=reply_id,
            block_id="text-1",
            delta=delta,
        ).model_dump(mode="json")
        await asyncio.sleep(0.4)
    for end_event in (
        TextBlockEndEvent(reply_id=reply_id, block_id="text-1"),
        ReplyEndEvent(
            session_id="dingtalk-streaming-e2e",
            reply_id=reply_id,
        ),
    ):
        yield end_event.model_dump(mode="json")


async def _streaming(timeout: float) -> bool:
    """Check AI-card creation, incremental updates, and finalization."""
    # Exercises the shipped default unless an override is configured.
    override = os.environ.get("DINGTALK_STREAMING_CARD_TEMPLATE_ID", "")
    config: dict[str, Any] = {}
    if override:
        config = {
            "streaming_card_template_id": override,
            "streaming_card_key": "content",
        }
    channel = _channel("dingtalk-streaming-e2e", **config)
    completed = asyncio.Event()
    succeeded = False

    async def emit(event: _Event) -> None:
        nonlocal succeeded
        if not isinstance(event, ChannelEvent) or completed.is_set():
            return
        api = channel._openapi  # pylint: disable=protected-access
        if api is None:
            print("FAIL: OpenAPI client unavailable", flush=True)
            completed.set()
            return
        create = api.create_streaming_card
        update = api.stream_card
        created = False
        updates: list[tuple[bool, bool]] = []

        async def tracked_create(*args: Any, **kwargs: Any) -> str | None:
            nonlocal created
            result = await create(*args, **kwargs)
            created = bool(result)
            return result

        async def tracked_update(*args: Any, **kwargs: Any) -> bool:
            result = await update(*args, **kwargs)
            updates.append((bool(kwargs.get("finalize")), result))
            return result

        setattr(api, "create_streaming_card", tracked_create)
        setattr(api, "stream_card", tracked_update)
        try:
            await channel.send_response(event, _streaming_events())
        finally:
            setattr(api, "create_streaming_card", create)
            setattr(api, "stream_card", update)
        succeeded = (
            created
            and len(updates) >= 2
            and all(ok for _, ok in updates)
            and any(finalize for finalize, _ in updates)
        )
        print(
            (
                "PASS: AI card created, incrementally updated, and finalized"
                if succeeded
                else "FAIL: AI streaming-card lifecycle was incomplete"
            ),
            flush=True,
        )
        completed.set()

    finished = await _run_until(
        channel,
        emit,
        completed,
        "send a direct message to receive the streaming AI card",
        timeout,
    )
    return finished and succeeded


async def _shutdown(_timeout: float) -> bool:
    """Check that the official Stream connection stops promptly."""
    channel = _channel("dingtalk-shutdown-e2e")

    async def emit(event: _Event) -> None:
        del event

    listener = asyncio.create_task(channel.start_listening(emit))
    for _ in range(150):
        if channel.status.state == "connected":
            break
        if channel.status.state == "failed":
            print("FAIL: Stream connection failed", flush=True)
            return False
        await asyncio.sleep(0.2)
    else:
        print("FAIL: Stream connection timed out", flush=True)
        return False

    listener.cancel()
    try:
        await asyncio.wait_for(listener, timeout=5.0)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        print("FAIL: Stream listener did not stop within 5 seconds")
        return False
    succeeded = channel.status.state == "stopped"
    print(
        (
            "PASS: real DingTalk Stream stopped cleanly"
            if succeeded
            else "FAIL: Channel did not enter stopped state"
        ),
        flush=True,
    )
    return succeeded


_SCENARIOS: dict[str, Callable[[float], Awaitable[bool]]] = {
    "direct": _direct,
    "group": _group,
    "approval": _approval,
    "streaming": _streaming,
    "probe-card": _probe_card,
    "shutdown": _shutdown,
}


async def _main(scenario: str, timeout: float) -> int:
    """Run one scenario or the complete interactive sequence."""
    names = list(_SCENARIOS) if scenario == "all" else [scenario]
    for name in names:
        print(f"\n=== DingTalk E2E: {name} ===", flush=True)
        if not await _SCENARIOS[name](timeout):
            print(f"FAIL: {name}", flush=True)
            return 1
        print(f"PASS: {name}", flush=True)
    return 0


def _parse_args() -> argparse.Namespace:
    """Parse the manual runner command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(*_SCENARIOS, "all"),
        help="real DingTalk scenario to run",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds allowed for each interactive scenario",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _ARGS = _parse_args()
    raise SystemExit(asyncio.run(_main(_ARGS.scenario, _ARGS.timeout)))
