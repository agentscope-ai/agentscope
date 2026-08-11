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
    """Small storage double exposing the team graph to the service."""

    def __init__(
        self,
        sessions: list[SessionRecord],
        team: TeamRecord | None = None,
    ) -> None:
        self.sessions = {session.id: session for session in sessions}
        self.team = team
        self.deleted: list[tuple[str, str, str]] = []

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

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Record the storage-level leader deletion."""
        self.deleted.append((user_id, agent_id, session_id))
        return session_id in self.sessions


class _Workspace:
    """Record session scopes purged from one logical workspace."""

    def __init__(self, fail_session_id: str | None = None) -> None:
        self.fail_session_id = fail_session_id
        self.purged: list[tuple[str, str]] = []

    async def purge_session(self, *, agent_id: str, session_id: str) -> None:
        """Record a purge and optionally simulate a backend failure."""
        self.purged.append((agent_id, session_id))
        if session_id == self.fail_session_id:
            raise RuntimeError("workspace unavailable")


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

    async def cancel_session_run(
        self,
        session_id: str,
        *,
        timeout: float = 10.0,
    ) -> bool:
        del timeout
        self.cancelled.append(session_id)
        return True

    async def _purge_session_bus(self, session_id: str) -> None:
        self.bus_purged.append(session_id)

    async def _purge_subagent_hitl(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        del user_id, agent_id, session_id


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
    """Workspace cleanup must mirror storage's team cascade."""

    async def test_deleting_regular_session_purges_its_scope(self) -> None:
        """A non-team session keeps the existing single-purge behavior."""
        leader = _session("leader-agent", "leader-session", "leader-ws")
        storage = _Storage([leader])
        manager = _WorkspaceManager()

        deleted = await _SessionService(storage, manager).delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        self.assertTrue(deleted)
        self.assertEqual(
            manager.workspaces["leader-ws"].purged,
            [(leader.agent_id, leader.id)],
        )

    async def test_deleting_leader_purges_created_worker(self) -> None:
        """A created worker's session scope is purged with its leader."""
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

        self.assertCountEqual(service.bus_purged, [leader.id, worker.id])
        self.assertEqual(
            manager.workspaces["shared-ws"].purged,
            [
                (leader.agent_id, leader.id),
                (worker.agent_id, worker.id),
            ],
        )

    async def test_invited_agent_keeps_ordinary_session_scope(self) -> None:
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

        self.assertEqual(
            manager.workspaces["invited-ws"].purged,
            [(borrowed.agent_id, borrowed.id)],
        )
        self.assertNotIn(
            (ordinary.agent_id, ordinary.id),
            manager.workspaces["invited-ws"].purged,
        )

    async def test_shared_workspace_purges_every_session_scope(self) -> None:
        """A shared workspace still receives one purge per session."""
        leader = _session(
            "leader-agent",
            "leader-session",
            "shared-ws",
            team_id="team",
        )
        worker_a = _session(
            "worker-a",
            "worker-a-session",
            "shared-ws",
            team_id="team",
        )
        worker_b = _session(
            "worker-b",
            "worker-b-session",
            "shared-ws",
            team_id="team",
        )
        storage = _Storage(
            [leader, worker_a, worker_b],
            _team(
                leader,
                [(worker_a, "created"), (worker_b, "created")],
            ),
        )
        manager = _WorkspaceManager()

        await _SessionService(storage, manager).delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        self.assertEqual(
            manager.workspaces["shared-ws"].purged,
            [
                (leader.agent_id, leader.id),
                (worker_a.agent_id, worker_a.id),
                (worker_b.agent_id, worker_b.id),
            ],
        )

    async def test_workspace_failure_does_not_skip_later_targets(self) -> None:
        """One failed purge does not prevent later session cleanup."""
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
        manager = _WorkspaceManager(failing_session_id=leader.id)

        await _SessionService(storage, manager).delete_session(
            "user",
            leader.agent_id,
            leader.id,
        )

        self.assertEqual(
            manager.workspaces["shared-ws"].purged,
            [
                (leader.agent_id, leader.id),
                (worker.agent_id, worker.id),
            ],
        )
