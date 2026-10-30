# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""ChatService resolves team/channel context once and fans it out."""

from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
    TeamData,
    TeamRecord,
    TTSModelConfig,
)
from agentscope.middleware import TTSMiddleware
from agentscope.state import AgentState


class _Storage:
    """Serve one worker session + its team/leader, counting team reads."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.get_team_calls = 0

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy; ``agent_id`` may be blank for leaders."""
        del user_id, agent_id
        record = self.sessions.get(session_id)
        return record.model_copy(deep=True) if record else None

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Return a detached agent record."""
        del user_id
        record = self.agents.get(agent_id)
        return record.model_copy(deep=True) if record else None

    async def get_team(self, user_id: str, team_id: str) -> TeamRecord | None:
        """Count every read — the run must need exactly one."""
        del user_id
        self.get_team_calls += 1
        return self.team if team_id == self.team.id else None

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept synthesized failure messages (none expected)."""


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-run-ctx-test")


class TestRunContextResolution(IsolatedAsyncioTestCase):
    """Team identity is read once and shared by toolkit + middleware."""

    async def test_worker_run_fetches_team_once(self) -> None:
        """One ``get_team`` read serves both consumers of the role."""
        await self._assert_worker_run(tts_available=True)

    async def test_missing_voice_profile_does_not_block_reply(self) -> None:
        """A missing profile skips TTS while the worker still replies."""
        await self._assert_worker_run(tts_available=False)

    async def _assert_worker_run(self, tts_available: bool) -> None:
        """Exercise assembly and reply with or without a usable TTS model."""
        user_id = "user-1"
        worker_agent = AgentRecord(
            id="agent-w",
            user_id=user_id,
            source="team",
            data=AgentData(
                name="worker",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        leader_agent = AgentRecord(
            id="agent-l",
            user_id=user_id,
            data=AgentData(
                name="Leader",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        config = SessionConfig(
            workspace_id="ws-1",
            chat_model_config=ChatModelConfig(
                type="test",
                credential_id="cred-1",
                model="m",
                parameters={},
            ),
            tts_model_config=TTSModelConfig(
                type="openai_credential",
                credential_id="cred-tts",
                model="gpt-4o-mini-tts",
                voice_profile_id="profile-1",
                parameters={"voice": "voice-custom"},
            ),
        )
        worker_session = SessionRecord(
            id="session-w",
            user_id=user_id,
            agent_id=worker_agent.id,
            team_id="team-1",
            config=config,
        )
        leader_session = SessionRecord(
            id="session-l",
            user_id=user_id,
            agent_id=leader_agent.id,
            team_id="team-1",
            config=config,
        )
        team = TeamRecord(
            id="team-1",
            user_id=user_id,
            session_id=leader_session.id,
            data=TeamData(name="team"),
        )
        storage = _Storage(
            sessions={s.id: s for s in (worker_session, leader_session)},
            agents={a.id: a for a in (worker_agent, leader_agent)},
            team=team,
        )
        equipped: list[list] = []
        replies: list[object] = []

        class _Agent:
            """Capture the middlewares; reply without doing anything."""

            def __init__(
                self,
                *,
                middlewares: list,
                state: AgentState,
                name: str,
                **_: object,
            ) -> None:
                equipped.append(middlewares)
                self.state = state
                self.name = name

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Yield nothing."""
                replies.append(inputs)
                if False:
                    yield object()

        toolkit_kwargs: dict = {}

        async def _get_toolkit(**kwargs: object) -> object:
            toolkit_kwargs.update(kwargs)
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            return object()

        class _Access:
            """Resolve the one worker agent."""

            async def resolve_agent(self, *_: object) -> AgentRecord:
                """Return a detached copy."""
                return worker_agent.model_copy(deep=True)

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=InMemoryMessageBus(),
            resource_access_service=_Access(),
            custom_agent_cls=_Agent,
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
            patch(
                "agentscope.app._service._chat.get_tts_model",
                autospec=True,
            ) as get_tts_model,
            patch(
                "agentscope.app._service._chat.publish_session_event",
                new_callable=AsyncMock,
            ) as publish,
        ):
            if not tts_available:
                get_tts_model.return_value = None
            await service._run_impl(
                user_id,
                worker_session.id,
                worker_agent.id,
                None,
            )

        failures = [
            call.args[2]
            for call in publish.await_args_list
            if call.args[2].get("name") == "tts_error"
        ]
        self.assertEqual(
            [
                {"name": item["name"], "value": item["value"]}
                for item in failures
            ],
            []
            if tts_available
            else [
                {
                    "name": "tts_error",
                    "value": {"stage": "configuration"},
                },
            ],
        )
        for middlewares in equipped:
            for middleware in middlewares:
                if isinstance(middleware, TTSMiddleware):
                    self.assertTrue(middleware.continue_on_error)

        get_tts_model.assert_awaited_once_with(
            user_id,
            config.tts_model_config,
            service._access,
            storage,
        )
        self.assertDictEqual(
            {
                "get_team_calls": storage.get_team_calls,
                "team_role": toolkit_kwargs["team_role"],
                "channel_tools": toolkit_kwargs["channel_tools"],
                "replies": replies,
                "tts_models": [
                    mw.tts
                    for mws in equipped
                    for mw in mws
                    if isinstance(mw, TTSMiddleware)
                ],
                "leader_names": [
                    mw._leader_name
                    for mws in equipped
                    for mw in mws
                    if hasattr(mw, "_leader_name")
                ],
            },
            {
                "get_team_calls": 1,
                "team_role": "worker",
                "channel_tools": [],
                "replies": [None],
                "tts_models": (
                    [get_tts_model.return_value] if tts_available else []
                ),
                "leader_names": ["Leader"],
            },
        )
