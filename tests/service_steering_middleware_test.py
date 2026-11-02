# -*- coding: utf-8 -*-
"""Tests for queued-message steering state and reasoning boundaries."""
import json
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app import _bus_ops
from agentscope.app._bus_ops import (
    ChatSteeringUnavailableError,
    acknowledge_steering_chat_inputs,
    enqueue_chat_input,
    fail_steering_chat_input,
    finish_active_chat_reply,
    list_chat_inputs,
    register_active_chat_reply,
    steer_chat_input,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.middleware import SteeringMiddleware
from agentscope.event import HintBlockEvent
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    Msg,
    ToolCallBlock,
    UserMsg,
)

_STEERING_HINT_SOURCE = "chat_input_steering"


def _make_agent() -> SimpleNamespace:
    """Build the state shape used by SteeringMiddleware."""
    return SimpleNamespace(
        name="agent",
        state=SimpleNamespace(
            session_id="session",
            reply_id="reply",
            cur_iter=0,
            context=[UserMsg(name="user", content="initial")],
        ),
        react_config=SimpleNamespace(max_iters=10),
    )


async def _collect(generator: AsyncGenerator) -> list:
    """Collect one asynchronous event generator."""
    return [event async for event in generator]


def _without_timestamps(value: Any) -> Any:
    """Remove volatile timestamps while retaining the complete structure."""
    if isinstance(value, dict):
        return {
            key: _without_timestamps(item)
            for key, item in value.items()
            if key not in {"created_at", "finished_at"}
        }
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _message_structures(messages: list[Msg]) -> list[dict]:
    """Serialize complete messages without volatile timestamps."""
    return [
        _without_timestamps(message.model_dump(mode="json"))
        for message in messages
    ]


def _hint_event_structure(event: HintBlockEvent) -> dict:
    """Serialize a complete hint event without generated identity fields."""
    payload = _without_timestamps(event.model_dump(mode="json"))
    payload.pop("id")
    return payload


def _expected_hint_message(item_id: str, message: Msg) -> Msg:
    """Build the current-reply context message for one steered input."""
    return AssistantMsg(
        id="reply",
        name="agent",
        content=[
            HintBlock(
                id=f"{item_id}:0",
                source=_STEERING_HINT_SOURCE,
                hint=list(message.content),
            ),
        ],
    )


def _expected_hint_event(item_id: str, message: Msg) -> dict:
    """Build the stable complete event structure for one steering hint."""
    return _hint_event_structure(
        HintBlockEvent(
            reply_id="reply",
            block_id=f"{item_id}:0",
            source=_STEERING_HINT_SOURCE,
            hint=list(message.content),
        ),
    )


async def _custom_events(bus: InMemoryMessageBus) -> list[dict]:
    """Read complete custom events without generated identity fields."""
    entries = await bus.log_read(MessageBusKeys.session_events("session"))
    events: list[dict] = []
    for _entry_id, payload in entries:
        if payload.get("type") != "CUSTOM":
            continue
        stable = _without_timestamps(payload)
        stable.pop("id")
        events.append(stable)
    return events


class TestSteeringQueueState(IsolatedAsyncioTestCase):
    """Queue reservations remain durable until injection or fallback."""

    async def test_steering_preserves_reference_storage(self) -> None:
        """Steer, failure and fallback keep payloads out of the FIFO."""
        bus = InMemoryMessageBus()
        message = UserMsg(name="user", content="guide")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            message,
        )
        expected = {
            "id": item["id"],
            "user_id": "user",
            "session_id": "session",
            "agent_id": "agent",
            "created_at": item["created_at"],
            "input_ref": item["id"],
            "state": "queued",
        }
        async with bus.acquire_lock(MessageBusKeys.session_lock("session")):
            await register_active_chat_reply(bus, "session", "reply")
            for transition in ("steer", "fail", "steer", "finish"):
                with self.subTest(transition=transition):
                    if transition == "steer":
                        await steer_chat_input(
                            bus,
                            "user",
                            "session",
                            "agent",
                            item["id"],
                            "reply",
                        )
                        expected["state"] = "steering"
                        expected["target_reply_id"] = "reply"
                        expected.pop("error", None)
                    elif transition == "fail":
                        await fail_steering_chat_input(
                            bus,
                            "session",
                            "reply",
                            item["id"],
                            "invalid",
                        )
                        expected["state"] = "failed"
                        expected["error"] = "invalid"
                        expected.pop("target_reply_id")
                    else:
                        await finish_active_chat_reply(bus, "session", "reply")
                        expected["state"] = "queued"
                        expected.pop("target_reply_id")
                    entries = await bus.queue_read(
                        MessageBusKeys.chat_inputs("session"),
                    )
                    self.assertEqual(
                        [entry for _, entry in entries],
                        [expected],
                    )
                    self.assertEqual(
                        await bus.registry_getall(
                            MessageBusKeys.chat_input_payloads("session"),
                        ),
                        {
                            item["id"]: json.dumps(
                                message.model_dump(mode="json"),
                            ),
                        },
                    )

    async def test_notification_failure_keeps_steering_committed(self) -> None:
        """A failed broadcast cannot reject reservation or acknowledgement."""
        bus = InMemoryMessageBus()
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg("user", "guide"),
        )
        async with bus.acquire_lock(MessageBusKeys.session_lock("session")):
            await register_active_chat_reply(bus, "session", "reply")
            with patch.object(
                _bus_ops,
                "_publish_chat_queue_changed",
                side_effect=ConnectionError("offline"),
            ):
                reserved = await steer_chat_input(
                    bus,
                    "user",
                    "session",
                    "agent",
                    item["id"],
                    "reply",
                )
                self.assertEqual(reserved, [{**item, "state": "steering"}])
                removed = await acknowledge_steering_chat_inputs(
                    bus,
                    "session",
                    "reply",
                    [item["id"]],
                )
                self.assertEqual(removed, reserved)
            self.assertEqual(await list_chat_inputs(bus, "session"), [])
            self.assertEqual(
                await bus.registry_getall(
                    MessageBusKeys.chat_input_payloads("session"),
                ),
                {},
            )

    async def test_reserve_is_idempotent_and_finish_restores(self) -> None:
        """Repeated Steer targets once and ReplyEnd restores deferred send."""
        bus = InMemoryMessageBus()
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg(name="user", content="guide"),
        )

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            first = await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            second = await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            steering_item = {
                **item,
                "state": "steering",
                "error": None,
            }
            self.assertEqual(first, [steering_item])
            self.assertEqual(second, [steering_item])

            restored = await finish_active_chat_reply(
                bus,
                "session",
                "reply",
            )
            self.assertEqual(restored, [item["id"]])

        self.assertEqual(await list_chat_inputs(bus, "session"), [item])

    async def test_acknowledge_non_head_preserves_other_items(self) -> None:
        """Injecting one selected item never changes other FIFO entries."""
        bus = InMemoryMessageBus()
        first = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg(name="user", content="first"),
        )
        selected = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg(name="user", content="selected"),
        )
        third = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg(name="user", content="third"),
        )

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                selected["id"],
                "reply",
            )
            removed = await acknowledge_steering_chat_inputs(
                bus,
                "session",
                "reply",
                [selected["id"]],
            )
            self.assertEqual(
                removed,
                [
                    {
                        **selected,
                        "state": "steering",
                        "error": None,
                    },
                ],
            )

        self.assertEqual(
            await list_chat_inputs(bus, "session"),
            [first, third],
        )
        self.assertEqual(
            await bus.registry_getall(
                MessageBusKeys.chat_input_payloads("session"),
            ),
            {
                first["id"]: json.dumps(first["input"]),
                third["id"]: json.dumps(third["input"]),
            },
        )

    async def test_stale_reply_is_rejected_without_changing_item(self) -> None:
        """A client reply id must match the server-side active registry."""
        bus = InMemoryMessageBus()
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            UserMsg(name="user", content="guide"),
        )

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "current")
            with self.assertRaises(ChatSteeringUnavailableError):
                await steer_chat_input(
                    bus,
                    "user",
                    "session",
                    "agent",
                    item["id"],
                    "stale",
                )

        self.assertEqual(await list_chat_inputs(bus, "session"), [item])


class TestSteeringMiddleware(IsolatedAsyncioTestCase):
    """Steering is consumed before or after a model call as needed."""

    async def test_injection_notification_failure_keeps_committed_hint(
        self,
    ) -> None:
        """A failed terminal event cannot roll back an acknowledged hint."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        message = UserMsg("user", "guide")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            message,
        )

        async def model_call(**_kwargs: object) -> AsyncGenerator:
            yield AssistantMsg(id="reply", name="agent", content="done")

        async with bus.acquire_lock(MessageBusKeys.session_lock("session")):
            await register_active_chat_reply(bus, "session", "reply")
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            with patch(
                "agentscope.app.middleware._steering_middleware."
                "publish_session_event",
                side_effect=ConnectionError("offline"),
            ):
                events = await _collect(
                    SteeringMiddleware(bus).on_reasoning(
                        agent,
                        {},
                        model_call,
                    ),
                )
        self.assertEqual(await list_chat_inputs(bus, "session"), [])
        self.assertEqual(
            _message_structures(agent.state.context[1:]),
            _message_structures([_expected_hint_message(item["id"], message)]),
        )
        self.assertEqual(
            [
                _hint_event_structure(event)
                for event in events
                if isinstance(event, HintBlockEvent)
            ],
            [_expected_hint_event(item["id"], message)],
        )

    async def test_pre_call_steering_reaches_one_model_call(self) -> None:
        """An already reserved item is visible to the immediate model call."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        initial = agent.state.context[0]
        guide = UserMsg(name="user", content="guide")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            guide,
        )
        calls = 0
        response = AssistantMsg(
            id="reply",
            name="agent",
            content="done",
        )

        async def model_call(**_kwargs: object) -> AsyncGenerator:
            nonlocal calls
            calls += 1
            self.assertEqual(
                _message_structures(agent.state.context),
                _message_structures(
                    [initial, _expected_hint_message(item["id"], guide)],
                ),
            )
            agent.state.context.append(response)
            yield response

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            events = await _collect(
                SteeringMiddleware(bus).on_reasoning(
                    agent,
                    {},
                    model_call,
                ),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(
            [
                _hint_event_structure(event)
                if isinstance(event, HintBlockEvent)
                else event
                for event in events
            ],
            [_expected_hint_event(item["id"], guide), response],
        )
        self.assertEqual(await list_chat_inputs(bus, "session"), [])

    async def test_hint_does_not_attach_to_previous_reply(self) -> None:
        """A same-agent tail from an older reply remains unchanged."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        previous = AssistantMsg(
            id="previous-reply",
            name="agent",
            content="previous response",
        )
        agent.state.context = [previous]
        guide = UserMsg(name="user", content="guide current reply")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            guide,
        )

        async def model_call(**_kwargs: object) -> AsyncGenerator:
            self.assertEqual(
                _message_structures(agent.state.context),
                _message_structures(
                    [
                        previous,
                        _expected_hint_message(item["id"], guide),
                    ],
                ),
            )
            yield AssistantMsg(id="reply", name="agent", content="done")

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            await _collect(
                SteeringMiddleware(bus).on_reasoning(
                    agent,
                    {},
                    model_call,
                ),
            )

        self.assertEqual(
            _message_structures(agent.state.context),
            _message_structures(
                [previous, _expected_hint_message(item["id"], guide)],
            ),
        )

    async def test_post_call_steering_continues_model_only_reply(self) -> None:
        """A mid-stream steer yields continuation to the Agent's next round."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        guide = UserMsg(name="user", content="new direction")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            guide,
        )
        calls = 0
        responses: list[Msg] = []

        async def model_call(**_kwargs: object) -> AsyncGenerator:
            nonlocal calls
            calls += 1
            response = AssistantMsg(
                id="reply",
                name="agent",
                content=f"response {calls}",
            )
            responses.append(response)
            agent.state.context.append(response)
            if calls == 1:
                await steer_chat_input(
                    bus,
                    "user",
                    "session",
                    "agent",
                    item["id"],
                    "reply",
                )
            yield response

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            events = await _collect(
                SteeringMiddleware(bus).on_reasoning(
                    agent,
                    {},
                    model_call,
                ),
            )
            self.assertEqual(calls, 1)
            self.assertEqual(
                [event for event in events if isinstance(event, Msg)],
                [],
            )
            events.extend(
                await _collect(
                    SteeringMiddleware(bus).on_reasoning(
                        agent,
                        {},
                        model_call,
                    ),
                ),
            )

        self.assertEqual(calls, 2)
        self.assertEqual(
            [
                _hint_event_structure(event)
                if isinstance(event, HintBlockEvent)
                else event
                for event in events
            ],
            [_expected_hint_event(item["id"], guide), responses[1]],
        )
        self.assertEqual(
            await _custom_events(bus),
            [
                {
                    "metadata": {},
                    "type": "CUSTOM",
                    "name": "chat_input_injected",
                    "value": {
                        "queue_item_id": item["id"],
                        "message_ids": [guide.id],
                    },
                },
            ],
        )

    async def test_post_call_tool_path_does_not_repeat_reasoning(self) -> None:
        """A tool-bound response leaves steering for the next reasoning."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        initial = agent.state.context[0]
        guide = UserMsg(name="user", content="guide after tool")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            guide,
        )
        calls = 0

        async def tool_model_call(**_kwargs: object) -> AsyncGenerator:
            nonlocal calls
            calls += 1
            agent.state.context.append(
                AssistantMsg(
                    id="reply",
                    name="agent",
                    content=[
                        ToolCallBlock(
                            id="tool",
                            name="work",
                            input="{}",
                        ),
                    ],
                ),
            )
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            return
            yield  # pragma: no cover

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            events = await _collect(
                SteeringMiddleware(bus).on_reasoning(
                    agent,
                    {},
                    tool_model_call,
                ),
            )
            self.assertEqual(events, [])

            async def after_tool(**_kwargs: object) -> AsyncGenerator:
                yield AssistantMsg(id="reply", name="agent", content="done")

            events = await _collect(
                SteeringMiddleware(bus).on_reasoning(agent, {}, after_tool),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(
            [
                _hint_event_structure(event)
                for event in events
                if isinstance(event, HintBlockEvent)
            ],
            [_expected_hint_event(item["id"], guide)],
        )
        expected_tool_message = AssistantMsg(
            id="reply",
            name="agent",
            content=[
                ToolCallBlock(id="tool", name="work", input="{}"),
                _expected_hint_message(item["id"], guide).content[0],
            ],
        )
        self.assertEqual(
            _message_structures(agent.state.context),
            _message_structures([initial, expected_tool_message]),
        )

    async def test_unsupported_content_stays_failed_in_queue(self) -> None:
        """Preparation failures remain visible and never discard the item."""
        bus = InMemoryMessageBus()
        agent = _make_agent()
        original = UserMsg(name="user", content="will be corrupted")
        item = await enqueue_chat_input(
            bus,
            "user",
            "session",
            "agent",
            original,
        )
        await bus.registry_set(
            MessageBusKeys.chat_input_payloads("session"),
            item["id"],
            json.dumps({"invalid": "message"}),
        )

        async def model_call(**_kwargs: object) -> AsyncGenerator:
            response = AssistantMsg(
                id="reply",
                name="agent",
                content="done",
            )
            agent.state.context.append(response)
            yield response

        async with bus.acquire_lock(
            MessageBusKeys.session_lock("session"),
            ttl_secs=60,
        ):
            await register_active_chat_reply(bus, "session", "reply")
            await steer_chat_input(
                bus,
                "user",
                "session",
                "agent",
                item["id"],
                "reply",
            )
            events = await _collect(
                SteeringMiddleware(bus).on_reasoning(
                    agent,
                    {},
                    model_call,
                ),
            )

        queued = await list_chat_inputs(bus, "session")
        error = queued[0]["error"]
        self.assertIsInstance(error, str)
        self.assertEqual(
            queued,
            [
                {
                    **item,
                    "input": {"invalid": "message"},
                    "state": "failed",
                    "error": error,
                },
            ],
        )
        self.assertEqual(
            [event for event in events if isinstance(event, Msg)],
            [agent.state.context[-1]],
        )
        self.assertEqual(
            await _custom_events(bus),
            [
                {
                    "metadata": {},
                    "type": "CUSTOM",
                    "name": "chat_input_steer_failed",
                    "value": {
                        "queue_item_id": item["id"],
                        "message_ids": [],
                        "message": error,
                    },
                },
            ],
        )
