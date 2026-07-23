# -*- coding: utf-8 -*-
"""Tests for chat-service middleware assembly."""
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from utils import MockModel

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.middleware import TeamReportMiddleware
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.middleware import MiddlewareBase
from agentscope.tool import Toolkit


class _StopAfterAgentConstruction(Exception):
    """Stop the service after middleware assembly has been captured."""


class _FakeStorage:
    """Minimal storage used up to Agent construction."""

    def __init__(
        self,
        agent_record: AgentRecord,
        session_record: SessionRecord,
        team_leader_session_id: str | None,
    ) -> None:
        """Store the records returned by the fake."""
        self.agent_record = agent_record
        self.session_record = session_record
        self.team_leader_session_id = team_leader_session_id

    async def get_agent(
        self,
        _user_id: str,
        _agent_id: str,
    ) -> AgentRecord | None:
        """Return the configured agent record."""
        return self.agent_record

    async def get_session(
        self,
        _user_id: str,
        _agent_id: str,
        _session_id: str,
    ) -> SessionRecord | None:
        """Return the configured session record."""
        return self.session_record

    async def get_team(
        self,
        _user_id: str,
        _team_id: str,
    ) -> Any:
        """Return a minimal team record when the session has a team."""
        if self.team_leader_session_id is None:
            return None
        return SimpleNamespace(session_id=self.team_leader_session_id)


class _FakeResourceAccess:
    """Resource access service returning the configured agent."""

    def __init__(self, agent_record: AgentRecord) -> None:
        """Store the agent returned by ``resolve_agent``."""
        self.agent_record = agent_record

    async def resolve_agent(
        self,
        _user_id: str,
        _agent_id: str,
    ) -> AgentRecord:
        """Return the configured agent record."""
        return self.agent_record


class _FakeWorkspace:
    """Workspace with no tools, skills, or MCPs."""

    workdir = "/tmp/agentscope-test-workspace"

    async def list_tools(self) -> list[Any]:
        """Return no workspace tools."""
        return []

    async def list_skills(self) -> list[Any]:
        """Return no skills."""
        return []

    async def list_mcps(self) -> list[Any]:
        """Return no MCPs."""
        return []


class _FakeWorkspaceManager:
    """Workspace manager returning a single fake workspace."""

    async def get_workspace(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> _FakeWorkspace:
        """Return a fake workspace."""
        return _FakeWorkspace()


class _FakeSchedulerManager:
    """Scheduler manager with no tools."""

    async def list_tools(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> list[Any]:
        """Return no scheduler tools."""
        return []


class _FakeBackgroundTaskManager:
    """Background task manager with no tools."""

    async def list_tools(self) -> list[Any]:
        """Return no background task tools."""
        return []


class _FakeMessageBus:
    """Message bus placeholder used for middleware construction."""


class TestChatServiceMiddlewareAssembly(IsolatedAsyncioTestCase):
    """Tests for team-role-aware middleware assembly in ChatService."""

    async def _capture_middlewares(
        self,
        source: Literal["user", "team"],
        team_role: Literal["leader", "worker"] | None,
    ) -> list[MiddlewareBase]:
        """Run ChatService until Agent construction and capture middlewares."""
        user_id = "u"
        agent = AgentRecord(
            user_id=user_id,
            source=source,
            data=AgentData(
                name="agent",
                system_prompt="system",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        session = SessionRecord(
            user_id=user_id,
            agent_id=agent.id,
            team_id="team-id" if team_role is not None else None,
            config=SessionConfig(
                workspace_id="ws",
                chat_model_config=ChatModelConfig(
                    type="mock",
                    credential_id="cred",
                    model="mock",
                    parameters={},
                ),
            ),
        )
        captured: list[MiddlewareBase] = []
        team_leader_session_id = None
        if team_role == "leader":
            team_leader_session_id = session.id
        elif team_role == "worker":
            team_leader_session_id = "leader-session-id"
        storage = _FakeStorage(
            agent,
            session,
            team_leader_session_id,
        )

        def _capture_agent(*_args: Any, **kwargs: Any) -> None:
            captured.extend(kwargs["middlewares"])
            raise _StopAfterAgentConstruction

        service = ChatService(
            storage=storage,
            workspace_manager=_FakeWorkspaceManager(),
            scheduler_manager=_FakeSchedulerManager(),
            background_task_manager=_FakeBackgroundTaskManager(),
            message_bus=_FakeMessageBus(),
            resource_access_service=cast(
                Any,
                _FakeResourceAccess(agent),
            ),
            custom_agent_cls=cast(Any, _capture_agent),
        )

        with (
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=MockModel()),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=Toolkit()),
            ),
        ):
            with self.assertRaises(_StopAfterAgentConstruction):
                await service._run_impl(  # pylint: disable=protected-access
                    user_id=user_id,
                    session_id=session.id,
                    agent_id=agent.id,
                    input_msg=None,
                )

        return captured

    async def test_team_worker_gets_team_report_middleware(self) -> None:
        """Team worker sessions get the report gate middleware."""
        middlewares = await self._capture_middlewares("team", "worker")

        self.assertTrue(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )

    async def test_invited_worker_gets_team_report_middleware(self) -> None:
        """Invited user-source workers get the report gate middleware."""
        middlewares = await self._capture_middlewares("user", "worker")

        self.assertTrue(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )

    async def test_team_leader_does_not_get_team_report_middleware(
        self,
    ) -> None:
        """Team leader sessions do not get the worker report gate."""
        middlewares = await self._capture_middlewares("user", "leader")

        self.assertFalse(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )

    async def test_standalone_user_does_not_get_team_report_middleware(
        self,
    ) -> None:
        """Standalone user sessions do not get the report gate."""
        middlewares = await self._capture_middlewares("user", None)

        self.assertFalse(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )
