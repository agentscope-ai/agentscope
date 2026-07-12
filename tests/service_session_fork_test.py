# -*- coding: utf-8 -*-
"""Tests for the SessionService Fork orchestration."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.app._service import SessionService, SessionStatus
from agentscope.app.storage import (
    SessionForkConflictError,
    SessionForkNotFoundError,
)


def _session(
    session_id: str = "source-session",
    user_id: str = "user-1",
    agent_id: str = "agent-1",
    team_id: str | None = None,
) -> SimpleNamespace:
    """Build the minimal SessionRecord shape used by the service tests."""
    return SimpleNamespace(
        id=session_id,
        user_id=user_id,
        agent_id=agent_id,
        team_id=team_id,
    )


class TestSessionServiceFork(IsolatedAsyncioTestCase):
    """Verify ownership-first status checks and Storage delegation."""

    def setUp(self) -> None:
        self.source = _session()
        self.storage = SimpleNamespace(
            get_session=AsyncMock(return_value=self.source),
            get_team=AsyncMock(return_value=None),
            fork_session=AsyncMock(return_value=_session("forked")),
        )
        self.bus = SimpleNamespace(
            is_locked=AsyncMock(return_value=False),
        )
        self.service = SessionService(self.storage, self.bus)

    async def test_fork_checks_ownership_before_status(self) -> None:
        """An inaccessible Session never reaches lock/status probing."""
        self.storage.get_session.return_value = _session(user_id="owner")

        with self.assertRaises(SessionForkNotFoundError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.bus.is_locked.assert_not_awaited()
        self.storage.fork_session.assert_not_awaited()

    async def test_record_id_and_agent_mismatch_are_not_found(self) -> None:
        """Record identity mismatches are hidden as NotFound."""
        for mismatched in (
            _session(session_id="other-session"),
            _session(agent_id="other-agent"),
        ):
            self.storage.get_session.return_value = mismatched
            with self.assertRaises(SessionForkNotFoundError):
                await self.service.fork_session(
                    "user-1",
                    "agent-1",
                    "source-session",
                )
        self.storage.fork_session.assert_not_awaited()

    async def test_every_non_idle_status_conflicts(self) -> None:
        """All non-IDLE statuses are rejected uniformly."""
        for session_status in (
            SessionStatus.RUNNING,
            SessionStatus.AWAITING_PERMISSION,
            SessionStatus.AWAITING_EXTERNAL_RESULT,
        ):
            self.service.get_session_status = AsyncMock(
                return_value=session_status,
            )
            with self.assertRaises(SessionForkConflictError):
                await self.service.fork_session(
                    "user-1",
                    "agent-1",
                    "source-session",
                )
        self.storage.fork_session.assert_not_awaited()

    async def test_root_status_none_is_not_found(self) -> None:
        """A missing root status is not treated as an idle Session."""
        self.service.get_session_status = AsyncMock(return_value=None)

        with self.assertRaises(SessionForkNotFoundError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.storage.fork_session.assert_not_awaited()

    async def test_idle_regular_session_delegates_to_storage(self) -> None:
        """An owned idle regular Session is delegated to Storage."""
        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.IDLE,
        )

        result = await self.service.fork_session(
            "user-1",
            "agent-1",
            "source-session",
        )

        self.assertEqual(result.id, "forked")
        self.storage.fork_session.assert_awaited_once_with(
            "user-1",
            "agent-1",
            "source-session",
        )

    async def test_team_leader_checks_members_in_member_namespace(
        self,
    ) -> None:
        """A Team Leader checks every normalized member by owner_id."""
        self.source.team_id = "team-1"
        member = SimpleNamespace(
            owner_id="member-owner",
            agent_id="member-agent",
            session_id="member-session",
        )
        self.storage.get_team.return_value = SimpleNamespace(
            session_id=self.source.id,
            data=SimpleNamespace(members=[member]),
        )
        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.IDLE,
        )

        await self.service.fork_session(
            "user-1",
            "agent-1",
            "source-session",
        )

        self.assertEqual(
            self.service.get_session_status.await_args_list[0].args,
            ("user-1", "agent-1", "source-session"),
        )
        self.assertEqual(
            self.service.get_session_status.await_args_list[1].args,
            ("member-owner", "member-agent", "member-session"),
        )

    async def test_team_worker_is_left_to_storage(self) -> None:
        """A non-Leader Team Session is not interpreted by the service."""
        self.source.team_id = "team-1"
        self.storage.get_team.return_value = SimpleNamespace(
            session_id="leader-session",
            data=SimpleNamespace(members=[]),
        )

        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.IDLE,
        )
        await self.service.fork_session(
            "user-1",
            "agent-1",
            "source-session",
        )
        self.service.get_session_status.assert_awaited_once_with(
            "user-1",
            "agent-1",
            "source-session",
        )
        self.storage.fork_session.assert_awaited_once()

    async def test_missing_team_is_left_to_storage(self) -> None:
        """A missing Team is not converted into a regular Session."""
        self.source.team_id = "missing-team"

        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.IDLE,
        )
        await self.service.fork_session(
            "user-1",
            "agent-1",
            "source-session",
        )
        self.service.get_session_status.assert_awaited_once_with(
            "user-1",
            "agent-1",
            "source-session",
        )
        self.storage.fork_session.assert_awaited_once()

    async def test_worker_non_idle_root_does_not_fork(self) -> None:
        """A worker root is checked before Storage handles worker rules."""
        self.source.team_id = "team-1"
        self.storage.get_team.return_value = SimpleNamespace(
            session_id="leader-session",
            data=SimpleNamespace(members=[]),
        )
        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.RUNNING,
        )

        with self.assertRaises(SessionForkConflictError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.storage.fork_session.assert_not_awaited()

    async def test_missing_team_non_idle_root_does_not_fork(self) -> None:
        """A missing Team does not bypass the root status check."""
        self.source.team_id = "missing-team"
        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.RUNNING,
        )

        with self.assertRaises(SessionForkConflictError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.storage.fork_session.assert_not_awaited()

    async def test_team_leader_non_idle_root_skips_members(self) -> None:
        """A non-idle Leader stops before checking member statuses."""
        self.source.team_id = "team-1"
        member = SimpleNamespace(
            owner_id="member-owner",
            agent_id="member-agent",
            session_id="member-session",
        )
        self.storage.get_team.return_value = SimpleNamespace(
            session_id=self.source.id,
            data=SimpleNamespace(members=[member]),
        )
        self.service.get_session_status = AsyncMock(
            return_value=SessionStatus.RUNNING,
        )

        with self.assertRaises(SessionForkConflictError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.service.get_session_status.assert_awaited_once_with(
            "user-1",
            "agent-1",
            "source-session",
        )
        self.storage.fork_session.assert_not_awaited()

    async def test_team_member_status_none_is_deferred_to_storage(
        self,
    ) -> None:
        """A missing member status does not make Service infer corruption."""
        self.source.team_id = "team-1"
        member = SimpleNamespace(
            owner_id="member-owner",
            agent_id="member-agent",
            session_id="member-session",
        )
        self.storage.get_team.return_value = SimpleNamespace(
            session_id=self.source.id,
            data=SimpleNamespace(members=[member]),
        )
        self.service.get_session_status = AsyncMock(
            side_effect=[SessionStatus.IDLE, None],
        )

        await self.service.fork_session(
            "user-1",
            "agent-1",
            "source-session",
        )

        self.storage.fork_session.assert_awaited_once()

    async def test_team_member_non_idle_does_not_fork(self) -> None:
        """A non-idle Team member blocks the whole Fork."""
        self.source.team_id = "team-1"
        member = SimpleNamespace(
            owner_id="member-owner",
            agent_id="member-agent",
            session_id="member-session",
        )
        self.storage.get_team.return_value = SimpleNamespace(
            session_id=self.source.id,
            data=SimpleNamespace(members=[member]),
        )
        self.service.get_session_status = AsyncMock(
            side_effect=[SessionStatus.IDLE, SessionStatus.RUNNING],
        )

        with self.assertRaises(SessionForkConflictError):
            await self.service.fork_session(
                "user-1",
                "agent-1",
                "source-session",
            )

        self.storage.fork_session.assert_not_awaited()
