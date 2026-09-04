# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Session auto-naming test cases.

A session created without a name is named after its creation timestamp,
which says nothing about what it holds; the first reply replaces that
with a title. The name a *person* chose must never be overwritten by
that, so ownership is tracked on ``SessionConfig.naming`` and asserted
from both ends here:

- the router sets and clears the flag (creation, rename), and a record
  written before the flag existed loads as user-owned, and
- :meth:`ChatService._auto_name_session` honours it, falls back to an
  excerpt of the opening message when the model call fails, and settles
  the name so it runs at most once per session.
"""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from utils import AnyString

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app import create_app
from agentscope.app._service import ChatService
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBusKeys,
    RedisMessageBus,
)
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    RedisStorage,
    SessionConfig,
    SessionNaming,
    SessionRecord,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}


class SessionNameOwnershipTest(IsolatedAsyncioTestCase):
    """Who owns ``config.name``, as decided by the session router."""

    async def asyncSetUp(self) -> None:
        """Start an app backed by fakeredis and seed one agent."""
        # enterContext binds the context manager to the test's lifetime;
        # pylint does not recognise the unittest-native helper.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class _Storage(RedisStorage):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        class _Bus(RedisMessageBus):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        app = create_app(
            storage=_Storage(),
            message_bus=_Bus(),
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
        )
        self.client = self.enterContext(TestClient(app))
        self.storage = app.state.storage
        self.agent_id = await self.storage.upsert_agent(
            "alice",
            AgentRecord(
                user_id="alice",
                data=AgentData(
                    name="ann",
                    system_prompt="You are ann.",
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                ),
            ),
        )

    async def _create(self, body: dict) -> str:
        """Create a session and return its id."""
        response = self.client.post("/sessions/", headers=HEADERS, json=body)
        self.assertEqual(response.status_code, 201)
        return response.json()["session_id"]

    async def _config(self, session_id: str) -> dict:
        """Return the stored config of *session_id* as plain JSON."""
        record = await self.storage.get_session(
            "alice",
            self.agent_id,
            session_id,
        )
        return record.config.model_dump(mode="json")

    async def test_unnamed_session_is_the_servers_to_name(self) -> None:
        """No name at creation leaves the placeholder up for grabs."""
        session_id = await self._create({"agent_id": self.agent_id})

        self.assertDictEqual(
            await self._config(session_id),
            {
                "workspace_id": AnyString(),
                # The creation timestamp — a placeholder, hence `auto`.
                "name": AnyString(),
                "naming": {"auto": True},
                "cwd": None,
                "chat_model_config": None,
                "fallback_chat_model_config": None,
                "tts_model_config": None,
                "knowledge_config": None,
            },
        )

    async def test_name_given_at_creation_is_kept(self) -> None:
        """A caller that names the session owns that name."""
        session_id = await self._create(
            {"agent_id": self.agent_id, "name": "release notes"},
        )

        self.assertDictEqual(
            await self._config(session_id),
            {
                "workspace_id": AnyString(),
                "name": "release notes",
                "naming": {"auto": False},
                "cwd": None,
                "chat_model_config": None,
                "fallback_chat_model_config": None,
                "tts_model_config": None,
                "knowledge_config": None,
            },
        )

    async def test_rename_takes_ownership(self) -> None:
        """Renaming settles the name against later auto-naming."""
        session_id = await self._create({"agent_id": self.agent_id})

        response = self.client.patch(
            f"/sessions/{session_id}",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
            json={"name": "my own name"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            await self._config(session_id),
            {
                "workspace_id": AnyString(),
                "name": "my own name",
                "naming": {"auto": False},
                "cwd": None,
                "chat_model_config": None,
                "fallback_chat_model_config": None,
                "tts_model_config": None,
                "knowledge_config": None,
            },
        )

    async def test_unrelated_patch_leaves_ownership_alone(self) -> None:
        """A PATCH that is not a rename must not settle the name."""
        session_id = await self._create({"agent_id": self.agent_id})

        response = self.client.patch(
            f"/sessions/{session_id}",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
            json={"cwd": "sub/dir"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            await self._config(session_id),
            {
                "workspace_id": AnyString(),
                "name": AnyString(),
                "naming": {"auto": True},
                "cwd": "sub/dir",
                "chat_model_config": None,
                "fallback_chat_model_config": None,
                "tts_model_config": None,
                "knowledge_config": None,
            },
        )

    def test_record_written_before_naming_existed_is_user_owned(self) -> None:
        """An upgraded deployment must not rename existing sessions.

        Sessions persisted by an earlier version carry no ``naming``
        block at all, so the field's default is what decides whether
        their names survive the upgrade.
        """
        config = SessionConfig.model_validate(
            {"workspace_id": "workspace-1", "name": "named last month"},
        )

        self.assertDictEqual(
            config.model_dump(mode="json"),
            {
                "workspace_id": "workspace-1",
                "name": "named last month",
                "naming": {"auto": False},
                "cwd": None,
                "chat_model_config": None,
                "fallback_chat_model_config": None,
                "tts_model_config": None,
                "knowledge_config": None,
            },
        )


class _Storage:
    """Record what auto-naming writes back."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session
        self.written_configs: list[SessionConfig] = []

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        session_id: str | None = None,
        **_: Any,
    ) -> SessionRecord:
        """Apply the config write and remember it."""
        assert user_id == self.session.user_id
        assert agent_id == self.session.agent_id
        assert session_id == self.session.id
        self.written_configs.append(config.model_copy(deep=True))
        self.session.config = config
        return self.session


class _Model:
    """A chat model that answers the naming call, or refuses to."""

    def __init__(self, title: str | None = None) -> None:
        self.title = title
        self.calls: list[list[Any]] = []

    async def generate_structured_output(
        self,
        messages: list[Any],
        structured_model: Any,
    ) -> Any:
        """Return the configured title, or fail like a bad credential."""
        del structured_model
        self.calls.append(messages)
        if self.title is None:
            raise RuntimeError("401 Unauthorized: invalid api key")

        class _Response:
            content = {"title": self.title}

        return _Response()


class AutoNameSessionTest(IsolatedAsyncioTestCase):
    """The naming step itself, driven directly against stubs."""

    def setUp(self) -> None:
        """Build a service over a single unnamed session."""
        self.session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id="agent-1",
            config=SessionConfig(
                workspace_id="workspace-1",
                name="2026-08-31 10:00:00",
                naming=SessionNaming(auto=True),
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="credential-1",
                    model="test-model",
                    parameters={},
                ),
            ),
        )
        self.storage = _Storage(self.session)
        self.bus = InMemoryMessageBus()
        self.service = ChatService(
            storage=self.storage,
            workspace_manager=object(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=object(),
        )

    async def _name(self, model: _Model, trigger_text: str) -> None:
        """Run the naming step for the seeded session."""
        await self.service._auto_name_session(
            "user-1",
            "agent-1",
            self.session,
            model,
            trigger_text,
        )

    async def _published(self) -> list[dict]:
        """Return the events auto-naming put on the session stream."""
        entries = await self.bus.log_read(
            MessageBusKeys.session_events("session-1"),
        )
        return [payload for _, payload in entries]

    async def test_generated_title_replaces_the_placeholder(self) -> None:
        """A working model names the session and settles the name."""
        await self._name(_Model("Release notes for v2"), "draft the v2 notes")

        self.assertListEqual(
            [c.model_dump(mode="json") for c in self.storage.written_configs],
            [
                {
                    "workspace_id": "workspace-1",
                    "name": "Release notes for v2",
                    "naming": {"auto": False},
                    "cwd": None,
                    "chat_model_config": {
                        "type": "test",
                        "credential_id": "credential-1",
                        "model": "test-model",
                        "parameters": {},
                    },
                    "fallback_chat_model_config": None,
                    "tts_model_config": None,
                    "knowledge_config": None,
                },
            ],
        )
        self.assertListEqual(
            await self._published(),
            [
                {
                    "type": "CUSTOM",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "name": "session_updated",
                    "value": {},
                    "metadata": {},
                },
            ],
        )

    async def test_model_failure_falls_back_to_an_excerpt(self) -> None:
        """A dead credential still beats leaving the timestamp up."""
        await self._name(_Model(), "draft the v2 release notes")

        self.assertListEqual(
            [(c.name, c.naming.auto) for c in self.storage.written_configs],
            [("draft the v2 release notes", False)],
        )

    async def test_long_title_is_truncated(self) -> None:
        """A model that ignores the brief cannot widen the sidebar."""
        await self._name(_Model("word " * 40), "anything")

        self.assertListEqual(
            [len(c.name) for c in self.storage.written_configs],
            [60],
        )

    async def test_settled_name_is_left_alone(self) -> None:
        """A session the user named is never re-named, nor announced."""
        self.session.config.naming = SessionNaming(auto=False)

        await self._name(_Model("a title"), "some opening message")

        self.assertListEqual(self.storage.written_configs, [])
        self.assertListEqual(await self._published(), [])

    async def test_textless_turn_is_left_for_later(self) -> None:
        """An image-only opening turn gives nothing to name from."""
        await self._name(_Model("a title"), "")

        self.assertListEqual(self.storage.written_configs, [])
        self.assertListEqual(await self._published(), [])
