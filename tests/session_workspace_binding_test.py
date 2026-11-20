# -*- coding: utf-8 -*-
"""Regression test for workspace bindings on session creation.

``POST /sessions/`` accepts an explicit ``workspace_id`` so a caller can
share the workspace of one of their own sessions. The value names the
host directory bind-mounted into the workspace container, so it must be
scoped to the caller: binding a workspace id that belongs to somebody
else's session would hand this user that session's container and
files.
"""

from unittest import IsolatedAsyncioTestCase

from fastapi import HTTPException

from agentscope.app._router._schema import CreateSessionRequest
from agentscope.app._router._session import create_session
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    SessionConfig,
    SessionRecord,
)
from agentscope.agent import ContextConfig, ReActConfig


class _Storage:
    """Keep agents and sessions per user in memory."""

    def __init__(self) -> None:
        self.agents: dict[str, list[AgentRecord]] = {}
        self.sessions: dict[tuple[str, str], list[SessionRecord]] = {}

    def add_agent(self, agent: AgentRecord) -> None:
        """Register an agent owned by its user."""
        self.agents.setdefault(agent.user_id, []).append(agent)

    def add_session(self, session: SessionRecord) -> None:
        """Register a session under its (user, agent) pair."""
        self.sessions.setdefault(
            (session.user_id, session.agent_id),
            [],
        ).append(session)

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return the agents owned by ``user_id``."""
        return [
            agent.model_copy(deep=True)
            for agent in self.agents.get(user_id, [])
        ]

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return the sessions stored under ``(user_id, agent_id)``."""
        return [
            session.model_copy(deep=True)
            for session in self.sessions.get((user_id, agent_id), [])
        ]

    async def find_session_by_workspace_id(
        self,
        user_id: str,
        workspace_id: str,
    ) -> SessionRecord | None:
        """Return the caller's session bound to ``workspace_id``."""
        for agent in await self.list_agents(user_id):
            for session in await self.list_sessions(user_id, agent.id):
                if session.config.workspace_id == workspace_id:
                    return session
        return None

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: object = None,
        session_id: str | None = None,
        **_: object,
    ) -> SessionRecord:
        """Create the requested session."""
        del state
        session = SessionRecord(
            id=session_id or "created-session",
            user_id=user_id,
            agent_id=agent_id,
            config=config,
        )
        self.add_session(session)
        return session


class _Access:
    """Accept every agent, credential, and knowledge base."""

    async def resolve_agent(self, user_id: str, agent_id: str) -> object:
        """Return an opaque agent handle."""
        del user_id, agent_id
        return object()

    async def get_resource(
        self,
        user_id: str,
        kind: object,
        resource_id: str,
    ) -> object:
        """Return an opaque resource handle."""
        del user_id, kind, resource_id
        return object()

    async def resolve_knowledge_base(
        self,
        user_id: str,
        kb_id: str,
    ) -> object:
        """Return an opaque knowledge-base record."""
        del user_id, kb_id
        return object()


class _WorkspaceManager:
    """Mint workspace ids when the body does not name one."""

    def __init__(self) -> None:
        self.minted = 0

    async def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Return a caller-scoped workspace id."""
        del user_id, agent_id, session_id
        self.minted += 1
        return "minted-workspace"


def _agent(user_id: str, agent_id: str) -> AgentRecord:
    """Build a minimal agent record."""
    return AgentRecord(
        id=agent_id,
        user_id=user_id,
        data=AgentData(
            name=agent_id,
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


class CreateSessionWorkspaceBindingTest(IsolatedAsyncioTestCase):
    """Explicit workspace bindings are limited to the caller's own."""

    async def asyncSetUp(self) -> None:
        """Seed one session per user, each with its own workspace."""
        self.storage = _Storage()
        self.workspace_manager = _WorkspaceManager()
        self.storage.add_agent(_agent("alice", "alice-agent"))
        self.storage.add_agent(_agent("bob", "bob-agent"))
        self.storage.add_session(
            SessionRecord(
                id="alice-session",
                user_id="alice",
                agent_id="alice-agent",
                config=SessionConfig(workspace_id="alice-workspace"),
            ),
        )
        self.storage.add_session(
            SessionRecord(
                id="bob-session",
                user_id="bob",
                agent_id="bob-agent",
                config=SessionConfig(workspace_id="bob-workspace"),
            ),
        )

    async def _create(
        self,
        user_id: str,
        agent_id: str,
        workspace_id: str | None,
    ) -> object:
        """Call the handler directly."""
        return await create_session(
            body=CreateSessionRequest(
                agent_id=agent_id,
                workspace_id=workspace_id,
            ),
            user_id=user_id,
            storage=self.storage,
            workspace_manager=self.workspace_manager,
            access=_Access(),
        )

    async def test_rejects_another_users_workspace(self) -> None:
        """Bob cannot bind the workspace that belongs to Alice."""
        with self.assertRaises(HTTPException) as raised:
            await self._create("bob", "bob-agent", "alice-workspace")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            [
                session.config.workspace_id
                for session in self.storage.sessions[("bob", "bob-agent")]
            ],
            ["bob-workspace"],
        )

    async def test_accepts_the_callers_own_workspace(self) -> None:
        """Sharing one of the caller's own workspaces still works."""
        response = await self._create("bob", "bob-agent", "bob-workspace")

        self.assertEqual(
            [
                session.config.workspace_id
                for session in self.storage.sessions[("bob", "bob-agent")]
            ],
            ["bob-workspace", "bob-workspace"],
        )
        self.assertEqual(self.workspace_manager.minted, 0)
        self.assertIsNotNone(response.session_id)

    async def test_missing_workspace_falls_back_to_the_manager(self) -> None:
        """Omitting the binding mints one under the isolation policy."""
        await self._create("bob", "bob-agent", None)

        self.assertEqual(self.workspace_manager.minted, 1)
        self.assertEqual(
            self.storage.sessions[("bob", "bob-agent")][
                -1
            ].config.workspace_id,
            "minted-workspace",
        )
