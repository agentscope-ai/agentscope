# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for cross-resource session deletion."""
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.app._service import SessionService
from agentscope.app.message_bus import MessageBus
from agentscope.app.storage import (
    SessionConfig,
    SessionRecord,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.app.workspace_manager import WorkspaceManagerBase


class _Storage:
    """Small stateful storage double for the service deletion cascade."""

    def __init__(
        self,
        sessions: list[SessionRecord],
        team: TeamRecord | None = None,
    ) -> None:
        self.sessions = {session.id: session for session in sessions}
        self.team = team
        self.deleted_sessions: list[tuple[str, str, str]] = []
        self.deleted_agents: list[tuple[str, str]] = []
        self.deleted_teams: list[tuple[str, str]] = []

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a session only for its exact owner and agent."""
        session = self.sessions.get(session_id)
        if (
            session is None
            or session.user_id != user_id
            or session.agent_id != agent_id
        ):
            return None
        return session

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return the agent's remaining sessions."""
        return [
            session
            for session in self.sessions.values()
            if session.user_id == user_id and session.agent_id == agent_id
        ]

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Return the configured team when its identity matches."""
        if (
            self.team is None
            or self.team.user_id != user_id
            or self.team.id != team_id
        ):
            return None
        return self.team

    async def upsert_team(
        self,
        user_id: str,
        team: TeamRecord,
    ) -> TeamRecord:
        """Persist roster changes made while deleting a created agent."""
        assert team.user_id == user_id
        self.team = team
        return team

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete an exact session and record the call."""
        self.deleted_sessions.append((user_id, agent_id, session_id))
        session = await self.get_session(user_id, agent_id, session_id)
        if session is None:
            return False
        del self.sessions[session_id]
        return True

    async def list_schedules(self, user_id: str) -> list[Any]:
        """This focused double has no schedules."""
        del user_id
        return []

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Record the final agent-record deletion."""
        self.deleted_agents.append((user_id, agent_id))
        return True

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete the team and detach its surviving leader."""
        self.deleted_teams.append((user_id, team_id))
        team = await self.get_team(user_id, team_id)
        if team is None:
            return False
        leader = self.sessions.get(team.session_id)
        if leader is not None:
            leader.team_id = None
        self.team = None
        return True


class _Workspace:
    """Record session and agent scopes purged from one workspace."""

    def __init__(self, fail_session_id: str | None = None) -> None:
        self.fail_session_id = fail_session_id
        self.purged_sessions: list[tuple[str, str]] = []
        self.purged_agents: list[str] = []

    async def purge_session(self, *, agent_id: str, session_id: str) -> None:
        """Record a purge and optionally simulate a backend failure."""
        self.purged_sessions.append((agent_id, session_id))
        if session_id == self.fail_session_id:
            raise RuntimeError("workspace unavailable")

    async def purge_agent(self, *, agent_id: str) -> None:
        """Record deletion of an agent-scoped skill partition."""
        self.purged_agents.append(agent_id)


class _WorkspaceManager(WorkspaceManagerBase):
    """Return stable workspace doubles by persisted workspace id."""

    def __init__(self, failing_session_id: str | None = None) -> None:
        super().__init__()
        self.failing_session_id = failing_session_id
        self.workspaces: dict[str, _Workspace] = {}
        self.lookups: list[tuple[str, str, str, str | None]] = []

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> Any:
        self.lookups.append((user_id, agent_id, session_id, workspace_id))
        assert workspace_id is not None
        return self.workspaces.setdefault(
            workspace_id,
            _Workspace(self.failing_session_id),
        )

    async def close(self, workspace_id: str) -> None:
        del workspace_id

    async def close_all(self) -> None:
        pass


class _SessionService(SessionService):
    """Exercise real deletion orchestration without a message-bus backend."""

    def __init__(
        self,
        storage: Any,
        workspace_manager: WorkspaceManagerBase,
    ) -> None:
        super().__init__(
            storage,
            AsyncMock(spec=MessageBus),
            workspace_manager,
        )
        self.cancelled: list[str] = []
        self.bus_purged: list[str] = []
        self.events: list[tuple[str, str]] = []

    async def cancel_session_run(
        self,
        session_id: str,
        *,
        timeout: float = 10.0,
    ) -> bool:
        del timeout
        self.cancelled.append(session_id)
        return True

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        self.events.append(("delete_team", team_id))
        return await super().delete_team(user_id, team_id)

    async def _purge_session_bus(self, session_id: str) -> None:
        self.bus_purged.append(session_id)

    async def _purge_subagent_hitl(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        del user_id, agent_id
        self.events.append(("purge_hitl", session_id))


def _session(
    agent_id: str,
    session_id: str,
    workspace_id: str,
    *,
    team_id: str | None = None,
) -> SessionRecord:
    """Build a minimal session record."""
    return SessionRecord(
        id=session_id,
        user_id="user",
        agent_id=agent_id,
        team_id=team_id,
        config=SessionConfig(workspace_id=workspace_id),
    )


def _team(
    leader: SessionRecord,
    members: list[tuple[SessionRecord, str]],
) -> TeamRecord:
    """Build a role-aware team roster."""
    return TeamRecord(
        id="team",
        user_id="user",
        session_id=leader.id,
        data=TeamData(
            name="team",
            members=[
                TeamMember(
                    owner_id=member.user_id,
                    agent_id=member.agent_id,
                    session_id=member.id,
                    role=role,
                )
                for member, role in members
            ],
        ),
    )


class TestSessionServiceWorkspacePurge(IsolatedAsyncioTestCase):
    """Workspace cleanup must mirror the role-aware team cascade."""

    async def test_deleting_regular_session_purges_its_scope(self) -> None:
        """A non-team session keeps the existing single-purge behavior."""
        session = _session("agent", "session", "workspace")
        storage = _Storage([session])
        manager = _WorkspaceManager()

        deleted = await _SessionService(storage, manager).delete_session(
            "user",
            session.agent_id,
            session.id,
        )

        self.assertTrue(deleted)
        self.assertEqual(
            manager.workspaces["workspace"].purged_sessions,
            [(session.agent_id, session.id)],
        )

    async def test_created_worker_purges_both_scopes(self) -> None:
        """A created worker drops both session and skill partitions."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "shared-ws",
            team_id="team",
        )
        worker = _session(
            "worker-agent",
            "worker-session",
            "shared-ws",
            team_id="team",
        )
        storage = _Storage(
            [leader, worker],
            _team(leader, [(worker, "created")]),
        )
        manager = _WorkspaceManager()
        service = _SessionService(storage, manager)

        await service.delete_session("user", leader.agent_id, leader.id)

        workspace = manager.workspaces["shared-ws"]
        self.assertEqual(
            workspace.purged_sessions,
            [
                (worker.agent_id, worker.id),
                (leader.agent_id, leader.id),
            ],
        )
        self.assertEqual(workspace.purged_agents, [worker.agent_id])
        self.assertEqual(storage.deleted_agents, [("user", worker.agent_id)])
        self.assertCountEqual(service.bus_purged, [leader.id, worker.id])

    async def test_invited_agent_keeps_other_scopes(self) -> None:
        """Only an invited agent's borrowed team session is purged."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "leader-ws",
            team_id="team",
        )
        ordinary = _session("invited-agent", "ordinary-session", "invited-ws")
        borrowed = _session(
            "invited-agent",
            "borrowed-session",
            "invited-ws",
            team_id="team",
        )
        storage = _Storage(
            [leader, ordinary, borrowed],
            _team(leader, [(borrowed, "invited")]),
        )
        manager = _WorkspaceManager()

        await _SessionService(storage, manager).delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        workspace = manager.workspaces["invited-ws"]
        self.assertEqual(
            workspace.purged_sessions,
            [(borrowed.agent_id, borrowed.id)],
        )
        self.assertEqual(workspace.purged_agents, [])
        self.assertIn(ordinary.id, storage.sessions)
        self.assertNotIn(borrowed.id, storage.sessions)
        self.assertEqual(storage.deleted_agents, [])

    async def test_hitl_is_purged_before_leader_team_deletion(self) -> None:
        """Role lookup remains available to HITL cleanup."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "workspace",
            team_id="team",
        )
        storage = _Storage([leader], _team(leader, []))
        service = _SessionService(storage, _WorkspaceManager())

        await service.delete_session("user", leader.agent_id, leader.id)

        self.assertEqual(
            service.events[:2],
            [("purge_hitl", leader.id), ("delete_team", "team")],
        )

    async def test_delete_team_skips_leader_in_corrupt_roster(self) -> None:
        """A leader listed as its own member cannot recurse."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "workspace",
            team_id="team",
        )
        storage = _Storage(
            [leader],
            _team(leader, [(leader, "created")]),
        )
        manager = _WorkspaceManager()
        service = _SessionService(storage, manager)

        deleted = await service.delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        self.assertTrue(deleted)
        self.assertEqual(storage.deleted_teams, [("user", "team")])
        self.assertEqual(storage.deleted_agents, [])
        self.assertEqual(
            manager.workspaces["workspace"].purged_sessions,
            [(leader.agent_id, leader.id)],
        )

    async def test_session_purge_failure_still_purges_agent(self) -> None:
        """Best-effort session cleanup does not skip the agent partition."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "shared-ws",
            team_id="team",
        )
        worker = _session(
            "worker-agent",
            "worker-session",
            "shared-ws",
            team_id="team",
        )
        storage = _Storage(
            [leader, worker],
            _team(leader, [(worker, "created")]),
        )
        manager = _WorkspaceManager(failing_session_id=worker.id)

        await _SessionService(storage, manager).delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        workspace = manager.workspaces["shared-ws"]
        self.assertEqual(workspace.purged_agents, [worker.agent_id])
        self.assertIn((leader.agent_id, leader.id), workspace.purged_sessions)
