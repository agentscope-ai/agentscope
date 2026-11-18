# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""ChatService resolves team/channel context once and fans it out."""

from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app._bus_ops import enqueue_chat_input
from agentscope.app._chat_input_store import INPUT_EXECUTION
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app.middleware._chat_tts_middleware import ChatTTSMiddleware
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
from agentscope.message import UserMsg
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

    async def get_message(self, *_: object) -> None:
        """No prior reply exists in these assembly-only scenarios."""


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

    async def test_queue_and_direct_input_each_saved_once(self) -> None:
        """Queue persistence replaces the initial save in ChatService."""
        for queued in (False, True):
            with self.subTest(queued=queued):
                await self._assert_worker_run(True, queued=queued)

    async def _assert_worker_run(
        self,
        tts_available: bool,
        queued: bool | None = None,
    ) -> None:
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
                self.speech = next(
                    mw
                    for mw in middlewares
                    if isinstance(mw, ChatTTSMiddleware)
                )

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Execute speech setup around an empty reply."""
                replies.append(inputs)

                async def reply(**_: object) -> AsyncGenerator:
                    if False:
                        yield object()

                async for event in self.speech.on_reply(
                    self,
                    {"inputs": inputs},
                    reply,
                ):
                    yield event

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
        message = UserMsg("user", "run once") if queued is not None else None
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
            patch.object(
                storage,
                "upsert_message",
                wraps=storage.upsert_message,
            ) as save,
        ):
            if not tts_available:
                get_tts_model.return_value = None
            else:
                get_tts_model.return_value.realtime = False
            if queued:
                queue = ChatInputQueue(service._message_bus, storage, service)
                self.addAsyncCleanup(queue.close)
                await enqueue_chat_input(
                    service._message_bus,
                    user_id,
                    worker_session.id,
                    worker_agent.id,
                    message,
                    client_builtin_tools=["AskUser"],
                )
                self.assertTrue(
                    await queue.drain_chat_inputs(
                        user_id,
                        worker_session.id,
                        worker_agent.id,
                    ),
                )
            else:
                await service._run_impl(
                    user_id,
                    worker_session.id,
                    worker_agent.id,
                    message,
                    client_builtin_tools=["AskUser"],
                )
            if message is not None:
                expected_message = message
                if queued:
                    expected_message = message.model_copy(
                        update={
                            "metadata": {
                                INPUT_EXECUTION: {
                                    "reply_id": None,
                                    "finished": False,
                                },
                            },
                        },
                    )
                save.assert_awaited_once_with(
                    user_id,
                    worker_session.id,
                    expected_message,
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
                    "value": {
                        "stage": "configuration",
                        "reply_id": None,
                        "message_ids": [],
                    },
                },
            ],
        )
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
                "client_builtin_tools": toolkit_kwargs["client_builtin_tools"],
                "replies": replies,
                "tts_configs": [
                    mw._default_config
                    for mws in equipped
                    for mw in mws
                    if isinstance(mw, ChatTTSMiddleware)
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
                "client_builtin_tools": ["AskUser"],
                "replies": [message],
                "tts_configs": [config.tts_model_config],
                "leader_names": ["Leader"],
            },
        )
