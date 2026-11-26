# -*- coding: utf-8 -*-
"""Permission PATCH regressions without Redis or background workers."""
import asyncio
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from schedule_router_test import _record
from utils import MockModel

from agentscope.agent import Agent
from agentscope.app._service import ChatService
from agentscope.app._router._schema._session import UpdateSessionRequest
from agentscope.app._router._session import update_session
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import ScheduleOrigin, SessionConfig, SessionRecord
from agentscope.event import ReplyEndEvent, UserInterruptEvent
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.permission import PermissionContext


class SessionPermissionPatchTest(IsolatedAsyncioTestCase):
    """Verify partial policy writes preserve the rest of the session."""

    async def test_lock_cannot_suppress_patch_failure(self) -> None:
        """A lock that suppresses a storage error cannot signal success."""
        bus = MagicMock(spec=InMemoryMessageBus)
        bus.acquire_lock_bounded.return_value.__aexit__.return_value = True
        storage = AsyncMock()
        storage.get_session.side_effect = OSError("Read failed")
        with self.assertRaisesRegex(RuntimeError, "session update"):
            await update_session(
                "session",
                UpdateSessionRequest(read_only=True),
                "agent",
                "user",
                storage,
                AsyncMock(),
                bus,
            )
        storage.upsert_session.assert_not_awaited()

    async def test_concurrent_patches_preserve_both_constraints(self) -> None:
        """Both successful patches merge with the last committed state."""
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
        )

        async def read(*_args: str) -> SessionRecord:
            snapshot = session.model_copy(deep=True)
            await asyncio.sleep(0)
            return snapshot

        async def save(**kwargs: Any) -> SessionRecord:
            session.state = kwargs["state"]
            return session

        bus = InMemoryMessageBus()
        storage = SimpleNamespace(get_session=read, upsert_session=save)
        await asyncio.gather(
            *(
                update_session(
                    session.id,
                    patch,
                    session.agent_id,
                    session.user_id,
                    storage,
                    SimpleNamespace(),
                    bus,
                )
                for patch in (
                    UpdateSessionRequest(read_only=True),
                    UpdateSessionRequest(interactive=False),
                )
            ),
        )
        self.assertEqual(
            session.state.permission_context,
            PermissionContext(read_only=True, interactive=False),
        )

    async def test_stop_during_patch_closes_parked_confirmation(self) -> None:
        """A config writer cannot consume an interrupt meant for the card."""
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
        )
        session.state.append_context(
            "agent",
            [
                ToolCallBlock(
                    id="call",
                    name="edit",
                    input="{}",
                    state="asking",
                ),
            ],
        )
        bus = InMemoryMessageBus()
        storage = AsyncMock()
        storage.get_session.return_value = session
        writing = asyncio.Event()
        release = asyncio.Event()

        async def save(**kwargs: Any) -> SessionRecord:
            writing.set()
            await release.wait()
            session.state = kwargs["state"]
            return session

        storage.upsert_session.side_effect = save
        service = ChatService(
            storage=storage,
            workspace_manager=AsyncMock(),
            scheduler_manager=AsyncMock(),
            background_task_manager=AsyncMock(),
            message_bus=bus,
            resource_access_service=AsyncMock(),
        )
        update = asyncio.create_task(
            update_session(
                session.id,
                UpdateSessionRequest(read_only=True),
                session.agent_id,
                session.user_id,
                storage,
                SimpleNamespace(),
                bus,
            ),
        )
        try:
            await asyncio.wait_for(writing.wait(), timeout=2)
            await service.interrupt(
                session.user_id,
                session.id,
                session.agent_id,
            )
            entries = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        finally:
            release.set()
            await update

        ((_, payload),) = entries
        event = UserInterruptEvent.model_validate(payload["input"])
        agent = Agent("agent", "Help", MockModel(), state=session.state)
        events = [item async for item in agent.reply_stream(event)]
        self.assertEqual(
            (
                payload["kind"],
                event.reply_id,
                session.state.permission_context.read_only,
                [
                    (e.reply_id, e.finished_reason)
                    for e in events
                    if isinstance(e, ReplyEndEvent)
                ],
                agent.state.has_awaiting_tool_calls(agent.name),
            ),
            (
                "resume",
                session.state.reply_id,
                True,
                [(event.reply_id, "interrupted")],
                False,
            ),
        )

    async def test_schedule_patch_survives_policy_refresh(self) -> None:
        """Session restrictions survive later schedule edits."""
        schedule = _record()
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
            origin=ScheduleOrigin(schedule_id=schedule.id),
        )
        schedule.apply_to_session(session.state, new_session=True)

        async def save(**kwargs: Any) -> SessionRecord:
            session.state = kwargs["state"]
            return session

        storage = SimpleNamespace(
            get_session=AsyncMock(return_value=session),
            get_schedule=AsyncMock(return_value=schedule),
            upsert_session=save,
        )
        for requested, schedule_limit, expected in (
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ):
            saved = await update_session(
                session_id=session.id,
                agent_id=session.agent_id,
                user_id=session.user_id,
                body=UpdateSessionRequest(
                    read_only=requested,
                    interactive=True,
                ),
                storage=storage,
                access=SimpleNamespace(),
                message_bus=InMemoryMessageBus(),
            )
            self.assertEqual(
                (
                    saved.state.permission_context.read_only,
                    saved.state.permission_context.interactive,
                ),
                (requested or schedule.data.read_only, False),
            )
            schedule.data.read_only = schedule_limit
            schedule.apply_to_session(session.state)
            self.assertEqual(
                (
                    session.state.permission_context.read_only,
                    session.state.schedule_session_limits.read_only,
                ),
                (expected, requested),
            )

    async def test_migrated_constraints_can_be_updated_independently(
        self,
    ) -> None:
        """False clears a legacy restriction without resetting other state."""
        updates: dict[str, Any]
        for legacy in ("ask", "explore", "accept_edits", "dont_ask"):
            for updates in (
                {"permission_mode": "auto"},
                {"permission_mode": "bypass", "read_only": False},
                {"read_only": False},
                {"read_only": True},
                {"allow_workspace_edits": False},
                {"allow_workspace_edits": True},
                {"interactive": False},
                {"interactive": True},
                {"name": "renamed"},
                {"read_only": None},
            ):
                with self.subTest(legacy=legacy, updates=updates):
                    session = SessionRecord(
                        id="session",
                        user_id="user",
                        agent_id="agent",
                        config=SessionConfig(workspace_id="workspace"),
                    )
                    session.state.permission_context = PermissionContext(
                        mode=legacy,
                    )
                    session.state.context = [
                        UserMsg(name="user", content="Keep this history"),
                    ]
                    session.state.summary = "Keep this summary"
                    original_state = session.state.model_dump(mode="json")
                    storage = SimpleNamespace(
                        get_session=AsyncMock(return_value=session),
                        upsert_session=AsyncMock(return_value=session),
                    )
                    await update_session(
                        session_id=session.id,
                        body=UpdateSessionRequest.model_validate(updates),
                        agent_id=session.agent_id,
                        user_id=session.user_id,
                        storage=storage,
                        access=SimpleNamespace(),
                        message_bus=InMemoryMessageBus(),
                    )
                    written = storage.upsert_session.call_args.kwargs
                    expected = session.state.model_dump(mode="json")
                    policy_updates = {
                        "mode" if key == "permission_mode" else key: value
                        for key, value in updates.items()
                        if key != "name" and value is not None
                    }
                    expected["permission_context"].update(policy_updates)
                    expected_config = session.config.model_dump()
                    if "name" in updates:
                        expected_config["name"] = updates["name"]
                        expected_config["naming"]["auto"] = False
                    self.assertEqual(
                        {
                            **written,
                            "state": written["state"].model_dump(mode="json")
                            if written["state"] is not None
                            else None,
                            "config": written["config"].model_dump(),
                        },
                        {
                            "user_id": "user",
                            "agent_id": "agent",
                            "session_id": "session",
                            "state": expected if policy_updates else None,
                            "config": expected_config,
                        },
                    )
                    self.assertEqual(
                        session.state.model_dump(mode="json"),
                        original_state,
                    )
