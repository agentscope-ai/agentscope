# -*- coding: utf-8 -*-
"""Tests for phase-one ordinary session storage forking."""
# pylint: disable=protected-access

from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from typing import Any

import fakeredis.aioredis
from redis.exceptions import WatchError

from agentscope._utils import _common
import agentscope.app.storage._redis_storage as redis_storage_module
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    SessionConfig,
    SessionForkConflictError,
    SessionForkCorruptedGraphError,
    SessionForkNotFoundError,
    SessionSource,
)
from agentscope.message import TextBlock, UserMsg
from agentscope.state import AgentState


def make_storage(
    *,
    key_ttl: int | None = None,
    decode_responses: bool = True,
) -> RedisStorage:
    """Create a RedisStorage backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = fakeredis.aioredis.FakeRedis(
        decode_responses=decode_responses,
    )
    storage.key_ttl = key_ttl
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_config(name: str = "Original") -> SessionConfig:
    """Build a session config with nested mutable data."""
    return SessionConfig(
        workspace_id="shared-workspace",
        name=name,
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="credential",
            model="model",
            parameters={"temperature": 0.2},
        ),
    )


class TestRegularSessionFork(IsolatedAsyncioTestCase):
    """Verify ordinary non-team, non-schedule session cloning."""

    async def asyncSetUp(self) -> None:
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.source_id = "source-session"
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(),
            state=AgentState(),
            session_id=self.source_id,
        )

    async def test_fork_copies_record_and_messages(self) -> None:
        """The fork has independent record data and exact message JSON."""
        source = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        assert source is not None
        old_timestamp = datetime(2000, 1, 1)
        source.created_at = old_timestamp
        source.updated_at = old_timestamp
        await self.storage._client.set(
            self.storage._key(
                self.storage.key_config.session,
                user_id=self.user_id,
                session_id=self.source_id,
            ),
            source.model_dump_json(),
        )
        source_messages = [
            UserMsg(
                name="user",
                content=[TextBlock(text="hello")],
            ),
            UserMsg(
                name="user",
                content=[TextBlock(text="world")],
            ),
        ]
        for message in source_messages:
            await self.storage.upsert_message(
                self.user_id,
                self.source_id,
                message,
            )
        source_raw = await self.storage._client.lrange(
            self.storage._message_key(self.user_id, self.source_id),
            0,
            -1,
        )

        fork = await self.storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )

        self.assertNotEqual(fork.id, source.id)
        self.assertNotEqual(fork.created_at, source.created_at)
        self.assertNotEqual(fork.updated_at, source.updated_at)
        self.assertEqual(fork.source, SessionSource.USER)
        self.assertIsNone(fork.source_schedule_id)
        self.assertIsNone(fork.team_id)
        self.assertEqual(fork.config.name, "Original (Fork)")
        self.assertEqual(fork.config.workspace_id, source.config.workspace_id)
        source_config = source.config.model_dump()
        fork_config = fork.config.model_dump()
        source_config.pop("name")
        fork_config.pop("name")
        self.assertEqual(fork_config, source_config)
        self.assertEqual(fork.state.model_dump(), source.state.model_dump())

        fork_raw = await self.storage._client.lrange(
            self.storage._message_key(self.user_id, fork.id),
            0,
            -1,
        )
        self.assertEqual(fork_raw, source_raw)
        self.assertIn(
            fork.id,
            await self.storage._client.smembers(
                self.storage._key(
                    self.storage.key_config.session_index,
                    user_id=self.user_id,
                    agent_id=self.agent_id,
                ),
            ),
        )

        fork.config.chat_model_config.parameters["temperature"] = 0.9
        fork.state.cur_iter = 7
        fork.state.tool_context.activated_groups.append("fork-only")
        source_after = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        assert source_after is not None
        self.assertEqual(
            source_after.config.chat_model_config.parameters["temperature"],
            0.2,
        )
        self.assertEqual(source_after.state.cur_iter, 0)
        self.assertEqual(source_after.state.tool_context.activated_groups, [])

    async def test_empty_source_does_not_create_message_key(self) -> None:
        """An empty source history produces no target Redis list."""
        fork = await self.storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        self.assertFalse(
            await self.storage._client.exists(
                self.storage._message_key(self.user_id, fork.id),
            ),
        )

    async def test_empty_name_uses_default_name(self) -> None:
        """An empty source name does not produce ``None (Fork)``."""
        storage = make_storage()
        await storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(name=""),
            session_id=self.source_id,
        )
        fork = await storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        self.assertTrue(fork.config.name)
        self.assertNotIn("None (Fork)", fork.config.name)

    async def test_ttl_is_applied_to_record_and_nonempty_messages(
        self,
    ) -> None:
        """New record and nonempty message list use the configured TTL."""
        storage = make_storage(key_ttl=60)
        await storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(),
            session_id=self.source_id,
        )
        await storage.upsert_message(
            self.user_id,
            self.source_id,
            UserMsg(
                name="user",
                content=[TextBlock(text="hello")],
            ),
        )
        fork = await storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        self.assertGreater(
            await storage._client.ttl(
                storage._key(
                    storage.key_config.session,
                    user_id=self.user_id,
                    session_id=fork.id,
                ),
            ),
            0,
        )
        self.assertGreater(
            await storage._client.ttl(
                storage._message_key(self.user_id, fork.id),
            ),
            0,
        )

    async def test_team_and_schedule_sources_are_conflicts(self) -> None:
        """Phase one rejects both unsupported source categories."""
        schedule = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(),
            source=SessionSource.SCHEDULE,
            session_id="schedule-session",
        )
        self.assertEqual(schedule.source, SessionSource.SCHEDULE)
        with self.assertRaises(SessionForkConflictError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                schedule.id,
            )

        await self.storage.set_session_team_id(
            self.user_id,
            self.source_id,
            "team-1",
        )
        with self.assertRaises(SessionForkConflictError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(),
            source_schedule_id="schedule-provenance",
            session_id="schedule-provenance-session",
        )
        with self.assertRaises(SessionForkConflictError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                "schedule-provenance-session",
            )

    async def test_bytes_message_payloads_are_copied_verbatim(self) -> None:
        """External pools without decode_responses keep bytes unchanged."""
        storage = make_storage(decode_responses=False)
        await storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_config(),
            session_id=self.source_id,
        )
        await storage.upsert_message(
            self.user_id,
            self.source_id,
            UserMsg(
                name="user",
                content=[TextBlock(text="bytes")],
            ),
        )
        source_key = storage._message_key(self.user_id, self.source_id)
        source_raw = await storage._client.lrange(source_key, 0, -1)
        fork = await storage.fork_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        fork_raw = await storage._client.lrange(
            storage._message_key(self.user_id, fork.id),
            0,
            -1,
        )
        self.assertEqual(source_raw, fork_raw)
        self.assertIsInstance(fork_raw[0], bytes)

    async def test_agent_mismatch_is_not_forkable(self) -> None:
        """The requested agent id is checked explicitly."""
        with self.assertRaises(SessionForkNotFoundError):
            await self.storage.fork_session(
                self.user_id,
                "different-agent",
                self.source_id,
            )

    async def test_missing_source_is_not_found(self) -> None:
        """A missing source Session maps to SessionForkNotFoundError."""
        with self.assertRaises(SessionForkNotFoundError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                "missing-session",
            )

    async def test_storage_key_id_mismatch_is_corrupted_graph(self) -> None:
        """A record ID must match the session key used to load it."""
        source_key = self.storage._key(
            self.storage.key_config.session,
            user_id=self.user_id,
            session_id=self.source_id,
        )
        source = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        assert source is not None
        await self.storage._client.set(
            source_key,
            source.model_copy(
                update={"id": "different-record-id"},
            ).model_dump_json(),
        )
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_invalid_source_json_is_corrupted_graph(self) -> None:
        """Malformed source JSON is normalized to the storage exception."""
        source_key = self.storage._key(
            self.storage.key_config.session,
            user_id=self.user_id,
            session_id=self.source_id,
        )
        await self.storage._client.set(source_key, "{invalid-json")
        with self.assertRaises(SessionForkCorruptedGraphError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )

    async def test_user_mismatch_is_not_forkable(self) -> None:
        """A corrupted record with another owner is treated as not found."""
        source_key = self.storage._key(
            self.storage.key_config.session,
            user_id=self.user_id,
            session_id=self.source_id,
        )
        source = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            self.source_id,
        )
        assert source is not None
        await self.storage._client.set(
            source_key,
            source.model_copy(
                update={"user_id": "other-user"},
            ).model_dump_json(),
        )
        with self.assertRaises(SessionForkNotFoundError):
            await self.storage.fork_session(
                self.user_id,
                self.agent_id,
                self.source_id,
            )


class TestForkPlanFailureTargets(IsolatedAsyncioTestCase):
    """Keep deterministic target ids available for failure assertions."""

    async def test_id_factory_can_pin_target_id(self) -> None:
        """The global ID factory supports precise target-key assertions."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(),
            session_id="source-session",
        )
        old_factory = _common._id_factory
        ids = iter(["target-id"])
        _common.set_id_factory(lambda: next(ids))
        try:
            fork = await storage.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )
            self.assertEqual(fork.id, "target-id")
            self.assertTrue(
                await storage._client.exists(
                    storage._key(
                        storage.key_config.session,
                        user_id="user-1",
                        session_id="target-id",
                    ),
                ),
            )
        finally:
            _common.set_id_factory(old_factory)

    async def test_target_id_collision_does_not_overwrite_existing_data(
        self,
    ) -> None:
        """A colliding target ID is skipped and never overwritten."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(name="source"),
            session_id="source-session",
        )
        await storage.upsert_message(
            "user-1",
            "source-session",
            UserMsg(
                name="user",
                content=[TextBlock(text="source")],
            ),
        )
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(name="existing"),
            session_id="existing-target",
        )
        await storage.upsert_message(
            "user-1",
            "existing-target",
            UserMsg(
                name="user",
                content=[TextBlock(text="existing")],
            ),
        )
        existing_before = await storage.get_session(
            "user-1",
            "agent-1",
            "existing-target",
        )
        assert existing_before is not None
        old_factory = _common._id_factory
        ids = iter(["existing-target", "fresh-target"])
        _common.set_id_factory(lambda: next(ids))
        try:
            fork = await storage.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )
        finally:
            _common.set_id_factory(old_factory)

        self.assertEqual(fork.id, "fresh-target")
        existing_after = await storage.get_session(
            "user-1",
            "agent-1",
            "existing-target",
        )
        assert existing_after is not None
        self.assertEqual(
            existing_after.model_dump(),
            existing_before.model_dump(),
        )
        existing_messages = await storage.list_messages(
            "user-1",
            "existing-target",
            limit=10,
        )
        self.assertEqual(existing_messages[0].content[0].text, "existing")

    async def test_empty_source_checks_orphan_target_message_key(self) -> None:
        """An orphan target list also prevents target ID reuse."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(name="source"),
            session_id="source-session",
        )
        orphan_key = storage._message_key("user-1", "orphan-target")
        await storage._client.rpush(orphan_key, "orphan-message")
        old_factory = _common._id_factory
        ids = iter(["orphan-target", "clean-target"])
        _common.set_id_factory(lambda: next(ids))
        try:
            fork = await storage.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )
        finally:
            _common.set_id_factory(old_factory)

        self.assertEqual(fork.id, "clean-target")
        self.assertEqual(
            await storage._client.lrange(orphan_key, 0, -1),
            ["orphan-message"],
        )
        self.assertFalse(
            await storage._client.exists(
                storage._message_key("user-1", "clean-target"),
            ),
        )

    async def test_watch_error_rebuilds_plan_without_first_target(
        self,
    ) -> None:
        """A retried transaction gets a new plan and leaves no first target."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(),
            session_id="source-session",
        )
        old_factory = _common._id_factory
        ids = iter(["target-first", "target-second"])
        _common.set_id_factory(lambda: next(ids))
        original_pipeline = storage._client.pipeline
        calls = 0
        pipeline_count = 0

        class _ExecuteOnceAsWatchError:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __aenter__(self) -> "_ExecuteOnceAsWatchError":
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *args: Any) -> Any:
                return await self.inner.__aexit__(*args)

            async def execute(self) -> Any:
                """Raise once, then delegate transaction execution."""
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise WatchError()
                return await self.inner.execute()

            def __getattr__(self, name: str) -> Any:
                return getattr(self.inner, name)

        def pipeline(*args: Any, **kwargs: Any) -> _ExecuteOnceAsWatchError:
            """Create a pipeline wrapper that fails once on execute."""
            nonlocal pipeline_count
            pipeline_count += 1
            return _ExecuteOnceAsWatchError(original_pipeline(*args, **kwargs))

        storage._client.pipeline = pipeline
        try:
            fork = await storage.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )
        finally:
            _common.set_id_factory(old_factory)

        self.assertEqual(fork.id, "target-second")
        self.assertEqual(calls, 2)
        self.assertEqual(pipeline_count, 2)
        self.assertFalse(
            await storage._client.exists(
                storage._key(
                    storage.key_config.session,
                    user_id="user-1",
                    session_id="target-first",
                ),
            ),
        )
        self.assertTrue(
            await storage._client.exists(
                storage._key(
                    storage.key_config.session,
                    user_id="user-1",
                    session_id="target-second",
                ),
            ),
        )

    async def test_clone_plan_failure_leaves_no_target(self) -> None:
        """A pre-MULTI plan error cannot create target keys."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(),
            session_id="source-session",
        )
        old_factory = _common._id_factory
        _common.set_id_factory(lambda: "target-plan-error")
        try:
            with patch.object(
                redis_storage_module,
                "build_fork_session_name",
                side_effect=ValueError("invalid clone plan"),
            ):
                with self.assertRaises(ValueError):
                    await storage.fork_session(
                        "user-1",
                        "agent-1",
                        "source-session",
                    )
        finally:
            _common.set_id_factory(old_factory)

        target_key = storage._key(
            storage.key_config.session,
            user_id="user-1",
            session_id="target-plan-error",
        )
        self.assertFalse(await storage._client.exists(target_key))

    async def test_watch_retry_exhaustion_leaves_no_targets(self) -> None:
        """Exhausted WatchError retries do not leave records or indexes."""
        storage = make_storage()
        await storage.upsert_session(
            "user-1",
            "agent-1",
            make_config(),
            session_id="source-session",
        )
        old_factory = _common._id_factory
        ids = iter(["target-one", "target-two", "target-three"])
        _common.set_id_factory(lambda: next(ids))
        original_pipeline = storage._client.pipeline
        pipeline_count = 0

        class _AlwaysWatchError:
            def __init__(self, inner: Any) -> None:
                self.inner = inner

            async def __aenter__(self) -> "_AlwaysWatchError":
                await self.inner.__aenter__()
                return self

            async def __aexit__(self, *args: Any) -> Any:
                return await self.inner.__aexit__(*args)

            async def execute(self) -> Any:
                """Always raise WatchError to exhaust retries."""
                raise WatchError()

            def __getattr__(self, name: str) -> Any:
                return getattr(self.inner, name)

        def pipeline(*args: Any, **kwargs: Any) -> _AlwaysWatchError:
            """Create a pipeline wrapper that always raises WatchError."""
            nonlocal pipeline_count
            pipeline_count += 1
            return _AlwaysWatchError(original_pipeline(*args, **kwargs))

        storage._client.pipeline = pipeline
        try:
            with self.assertRaises(SessionForkConflictError):
                await storage.fork_session(
                    "user-1",
                    "agent-1",
                    "source-session",
                )
        finally:
            _common.set_id_factory(old_factory)

        self.assertEqual(pipeline_count, 3)
        for target_id in (
            "target-one",
            "target-two",
            "target-three",
        ):
            self.assertFalse(
                await storage._client.exists(
                    storage._key(
                        storage.key_config.session,
                        user_id="user-1",
                        session_id=target_id,
                    ),
                ),
            )
        index_key = storage._key(
            storage.key_config.session_index,
            user_id="user-1",
            agent_id="agent-1",
        )
        self.assertTrue(
            {
                "target-one",
                "target-two",
                "target-three",
            }.isdisjoint(await storage._client.smembers(index_key)),
        )
