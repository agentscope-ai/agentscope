# -*- coding: utf-8 -*-
"""Schedule-specific Storage Fork tests."""
# pylint: disable=protected-access

from unittest import IsolatedAsyncioTestCase
from typing import Any

from redis.exceptions import ResponseError

from _storage_test_helpers import make_config, make_storage
from agentscope._utils import _common
from agentscope.app.storage import (
    SessionForkCorruptedGraphError,
    SessionSource,
)
from agentscope.message import TextBlock, UserMsg
from agentscope.state import AgentState


class TestScheduleSessionFork(IsolatedAsyncioTestCase):
    """Verify Schedule provenance and Schedule-specific side effects."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.schedule_id = "schedule-1"
        self.source_id = "schedule-session"
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(name="Scheduled"),
            state=AgentState(),
            session_id=self.source_id,
            source=SessionSource.SCHEDULE,
            source_schedule_id=self.schedule_id,
        )

    async def test_schedule_fork_resets_provenance_and_preserves_index(
        self,
    ) -> None:
        """A Schedule fork becomes a normal user-owned Session."""
        await self.storage.upsert_message(
            self.user_id,
            self.source_id,
            UserMsg(
                name="user",
                content=[TextBlock(text="scheduled content")],
            ),
        )
        schedule_index_key = self.storage._key(
            self.storage.key_config.schedule_session_index,
            user_id=self.user_id,
            schedule_id=self.schedule_id,
        )
        index_before = await self.storage._client.smembers(schedule_index_key)
        self.assertIn(self.source_id, index_before)

        fork = await self.storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )

        self.assertEqual(fork.source, SessionSource.USER)
        self.assertIsNone(fork.source_schedule_id)
        self.assertIsNone(fork.team_id)
        self.assertEqual(fork.config.name, "Scheduled (Fork)")
        self.assertEqual(
            await self.storage._client.smembers(schedule_index_key),
            index_before,
        )
        self.assertNotIn(
            fork.id,
            await self.storage._client.smembers(schedule_index_key),
        )
        self.assertEqual(
            await self.storage.list_messages(
                self.user_id,
                fork.id,
                limit=10,
            ),
            await self.storage.list_messages(
                self.user_id,
                self.source_id,
                limit=10,
            ),
        )

    async def test_schedule_provenance_requires_nonempty_schedule_id(
        self,
    ) -> None:
        """Schedule records without provenance are corrupted."""
        storage = make_storage()
        for suffix, schedule_id in (
            ("none", None),
            ("empty", ""),
            ("whitespace", "   "),
        ):
            session_id = f"missing-schedule-id-{suffix}"
            await storage.upsert_session(
                self.user_id,
                self.agent_id,
                make_config(),
                session_id=session_id,
                source=SessionSource.SCHEDULE,
                source_schedule_id=schedule_id,
            )
            with self.assertRaises(SessionForkCorruptedGraphError):
                await storage.fork_session(
                    self.user_id,
                    self.agent_id,
                    session_id,
                )

    async def test_user_session_cannot_carry_schedule_provenance(self) -> None:
        """A USER record with a schedule id is corrupted."""
        for suffix in ("empty", "whitespace"):
            storage = make_storage()
            schedule_id = "" if suffix == "empty" else "   "
            session_id = f"inconsistent-provenance-{suffix}"
            await storage.upsert_session(
                self.user_id,
                self.agent_id,
                make_config(),
                session_id=session_id,
                source=SessionSource.USER,
                source_schedule_id=schedule_id,
            )
            with self.assertRaises(SessionForkCorruptedGraphError):
                await storage.fork_session(
                    self.user_id,
                    self.agent_id,
                    session_id,
                )

    async def test_team_session_remains_conflict(self) -> None:
        """Team sessions remain outside the Schedule/ordinary phase."""
        await self.storage.set_session_team_id(
            self.user_id,
            self.source_id,
            "team-1",
        )
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_source_message_wrong_type_is_corrupted(self) -> None:
        """A non-list source Message Key is rejected before reading."""
        message_key = self.storage._message_key(self.user_id, self.source_id)
        await self.storage._client.set(message_key, "wrong-type")
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_source_session_wrong_type_is_corrupted(self) -> None:
        """A non-string source Session Key is rejected before reading."""
        session_key = self.storage._key(
            self.storage.key_config.session,
            user_id=self.user_id,
            session_id=self.source_id,
        )
        await self.storage._client.delete(session_key)
        await self.storage._client.rpush(session_key, "wrong-type")
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_target_index_wrong_type_is_corrupted(self) -> None:
        """A non-set target Agent Session Index is rejected."""
        index_key = self.storage._key(
            self.storage.key_config.session_index,
            user_id=self.user_id,
            agent_id=self.agent_id,
        )
        await self.storage._client.delete(index_key)
        await self.storage._client.set(index_key, "wrong-type")
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_wrongtype_get_rebuilds_pipeline(self) -> None:
        """A concurrent source type change rebuilds the full pipeline."""
        original_pipeline = self.storage._client.pipeline
        pipeline_count = 0
        wrongtype_raised = False

        class _WrongTypeOnGet:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __aenter__(self) -> "_WrongTypeOnGet":
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *args: Any) -> Any:
                """Close the wrapped pipeline context."""
                return await self.inner.__aexit__(*args)

            async def get(self, key: Any) -> Any:
                """Raise WRONGTYPE once, then delegate source reads."""
                nonlocal wrongtype_raised
                if not wrongtype_raised:
                    wrongtype_raised = True
                    raise ResponseError("WRONGTYPE concurrent change")
                return await self.inner.get(key)

            def __getattr__(self, name: str) -> Any:
                return getattr(self.inner, name)

        def pipeline(*args: Any, **kwargs: Any) -> _WrongTypeOnGet:
            nonlocal pipeline_count
            pipeline_count += 1
            return _WrongTypeOnGet(original_pipeline(*args, **kwargs))

        old_factory = _common._id_factory
        _common.set_id_factory(lambda: "after-wrongtype")
        self.storage._client.pipeline = pipeline
        try:
            fork = await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )
        finally:
            _common.set_id_factory(old_factory)

        self.assertEqual(pipeline_count, 2)
        self.assertEqual(fork.id, "after-wrongtype")

    async def test_non_wrongtype_response_error_is_propagated(self) -> None:
        """Non-WRONGTYPE source read errors are not swallowed."""
        original_pipeline = self.storage._client.pipeline

        class _OtherResponseError:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __aenter__(self) -> "_OtherResponseError":
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *args: Any) -> Any:
                """Close the wrapped pipeline context."""
                return await self.inner.__aexit__(*args)

            async def get(self, key: Any) -> Any:
                """Raise a non-WRONGTYPE Redis error for source reads."""
                raise ResponseError("READONLY replica")

            def __getattr__(self, name: str) -> Any:
                return getattr(self.inner, name)

        self.storage._client.pipeline = lambda *args, **kwargs: (
            _OtherResponseError(original_pipeline(*args, **kwargs))
        )
        with self.assertRaises(ResponseError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )
