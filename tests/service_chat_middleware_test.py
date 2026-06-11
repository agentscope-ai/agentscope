# -*- coding: utf-8 -*-
"""Tests for chat-service middleware assembly."""
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


class _StopAfterAgentConstruction(Exception):
    """Stop the service after middleware assembly has been captured."""


class _FakeStorage:
    """Minimal storage used up to Agent construction."""

    def __init__(
        self,
        agent_record: AgentRecord,
        session_record: SessionRecord,
    ) -> None:
        """Store the records returned by the fake."""
        self.agent_record = agent_record
        self.session_record = session_record

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


class _FakeWorkspace:
    """Workspace with no tools, skills, or MCPs."""

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
    """Tests for source-aware middleware assembly in ChatService."""

    async def _capture_middlewares(
        self,
        source: Literal["user", "team"],
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
            team_id="team-id" if source == "team" else None,
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

        def _capture_agent(*_args: Any, **kwargs: Any) -> None:
            captured.extend(kwargs["middlewares"])
            raise _StopAfterAgentConstruction

        service = ChatService(
            storage=_FakeStorage(agent, session),
            workspace_manager=_FakeWorkspaceManager(),
            scheduler_manager=_FakeSchedulerManager(),
            background_task_manager=_FakeBackgroundTaskManager(),
            message_bus=_FakeMessageBus(),
            custom_agent_cls=cast(Any, _capture_agent),
        )

        with (
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=MockModel()),
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
        middlewares = await self._capture_middlewares("team")

        self.assertTrue(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )

    async def test_user_agent_does_not_get_team_report_middleware(
        self,
    ) -> None:
        """User/leader sessions do not get the worker report gate."""
        middlewares = await self._capture_middlewares("user")

        self.assertFalse(
            any(isinstance(_, TeamReportMiddleware) for _ in middlewares),
        )
