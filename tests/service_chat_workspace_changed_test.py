# -*- coding: utf-8 -*-
"""ChatService emits a workspace_changed event when a run finishes."""

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
from agentscope.message import UserMsg
from agentscope.state import AgentState


class _Storage:
    """Serve one worker session + its team/leader."""

    def __init__(
        self,
        sessions: dict[str, SessionRecord],
        agents: dict[str, AgentRecord],
        team: TeamRecord,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        del user_id, agent_id
        record = self.sessions.get(session_id)
        return record.model_copy(deep=True) if record else None

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        del user_id
        record = self.agents.get(agent_id)
        return record.model_copy(deep=True) if record else None

    async def get_team(self, user_id: str, team_id: str) -> TeamRecord | None:
        del user_id
        return self.team if team_id == self.team.id else None

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept message persistence."""


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        return SimpleNamespace(workdir="/tmp/agentscope-ws-changed-test")


class TestWorkspaceChangedEvent(IsolatedAsyncioTestCase):
    """A finished run tells session UIs to refetch the workspace listing."""

    async def test_run_emits_workspace_changed_once(self) -> None:
        """A successful run publishes exactly one workspace_changed event."""
        service, storage, worker_session, worker_agent = self._build_service()
        with self._patched(service) as publish:
            await service.run(
                worker_session.user_id,
                worker_session.id,
                worker_agent.id,
                UserMsg("user", "write the export"),
            )
        changed = [
            call.args[2]
            for call in publish.await_args_list
            if call.args[2].get("name") == "workspace_changed"
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(
            changed[0]["value"],
            {"session_id": worker_session.id},
        )

    async def test_run_failing_before_execution_emits_nothing(self) -> None:
        """A run that dies in assembly never touched the workspace."""
        service, storage, worker_session, worker_agent = self._build_service()
        with self._patched(service) as publish:
            with patch.object(
                service,
                "_run_impl",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                await service.run(
                    worker_session.user_id,
                    worker_session.id,
                    worker_agent.id,
                    UserMsg("user", "write the export"),
                )
        changed = [
            call.args[2]
            for call in publish.await_args_list
            if call.args[2].get("name") == "workspace_changed"
        ]
        self.assertEqual(changed, [])

    async def test_run_failing_mid_execution_still_emits(self) -> None:
        """Files may be written before a mid-run failure, so signal anyway."""
        service, storage, worker_session, worker_agent = self._build_service(
            failing_agent=True
        )
        with self._patched(service) as publish:
            await service.run(
                worker_session.user_id,
                worker_session.id,
                worker_agent.id,
                UserMsg("user", "write the export"),
            )
        changed = [
            call.args[2]
            for call in publish.await_args_list
            if call.args[2].get("name") == "workspace_changed"
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(
            changed[0]["value"],
            {"session_id": worker_session.id},
        )

    def _build_service(
        self,
        failing_agent: bool = False,
    ) -> tuple[ChatService, _Storage, SessionRecord, AgentRecord]:
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

        class _Agent:
            """Reply without doing anything."""

            def __init__(
                self,
                *,
                middlewares: list,
                state: AgentState,
                name: str,
                **_: object,
            ) -> None:
                del middlewares
                self.state = state
                self.name = name

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                del inputs
                if failing_agent:
                    raise RuntimeError("agent blew up mid-turn")
                if False:
                    yield object()

        class _Access:
            """Resolve the one worker agent."""

            async def resolve_agent(self, *_: object) -> AgentRecord:
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
        return service, storage, worker_session, worker_agent

    def _patched(self, service: ChatService) -> object:
        """Stub the model/toolkit/TTS lookups and capture bus publishes."""
        return _PatchContext(service)


class _PatchContext:
    """Context manager bundling the patches a run needs."""

    def __init__(self, service: ChatService) -> None:
        self._service = service
        self._stack: list[object] = []
        self.publish: AsyncMock | None = None

    async def _get_toolkit(self, **kwargs: object) -> object:
        del kwargs
        return object()

    async def _get_model(self, *_: object, **__: object) -> object:
        return object()

    def __enter__(self) -> AsyncMock:
        from contextlib import ExitStack

        stack = ExitStack()
        self._stack.append(stack)
        stack.enter_context(
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=self._get_toolkit,
            ),
        )
        stack.enter_context(
            patch(
                "agentscope.app._service._chat.get_model", new=self._get_model
            ),
        )
        get_tts_model = stack.enter_context(
            patch(
                "agentscope.app._service._chat.get_tts_model",
                autospec=True,
            ),
        )
        get_tts_model.return_value = None
        publish = stack.enter_context(
            patch(
                "agentscope.app._service._chat.publish_session_event",
                new_callable=AsyncMock,
            ),
        )
        self.publish = publish
        return publish

    def __exit__(self, *exc: object) -> None:
        stack = self._stack.pop()
        stack.close()
