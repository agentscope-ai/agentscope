# -*- coding: utf-8 -*-
"""Team config reconcile tests.

``PUT /agent/{agent_id}/team/config`` replaces the leader's config
wholesale. Its reconciliation must only touch self-built backlinks:
invited-by-reference members (``parent_agent_id`` is None or points at
another leader) must never be re-stamped, otherwise a wholesale replace
would silently "promote" them into self-built members (flipping
``is_self_built`` to true and arming the cascade delete on removal).
"""

from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope.app._router._agent import SetTeamConfigRequest, set_team_config
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    TeamConfig,
)
from agentscope.agent import ContextConfig, ReActConfig

OWNER = "team-owner"
LEADER_ID = "team-leader"


def _make_record(
    rid: str,
    *,
    parent_agent_id: str | None = None,
    team_config: TeamConfig | None = None,
) -> AgentRecord:
    """Build an agent record owned by ``OWNER``."""
    return AgentRecord(
        id=rid,
        user_id=OWNER,
        source="user",
        data=AgentData(
            name=rid,
            system_prompt="You are a helpful assistant.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            parent_agent_id=parent_agent_id,
            team_config=team_config,
        ),
    )


class _FakeStorage:
    """In-memory storage exposing only the calls set_team_config needs."""

    def __init__(self, records: list[AgentRecord]) -> None:
        self._records = {r.id: r for r in records}

    async def list_agents(self, owner_id: str) -> list[AgentRecord]:
        return [r for r in self._records.values() if r.user_id == owner_id]

    async def get_agent(
        self,
        owner_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        return self._records.get(agent_id)

    async def upsert_agent(
        self,
        owner_id: str,
        record: AgentRecord,
    ) -> None:
        self._records[record.id] = record

    def get(self, rid: str) -> AgentRecord:
        return self._records[rid]


class TeamConfigReconcileTest(IsolatedAsyncioTestCase):
    """The wholesale replace must never re-stamp invited members."""

    def _build_fixture(self) -> tuple[_FakeStorage, MagicMock]:
        """Leader with one self-built and one invited-by-reference member."""
        storage = _FakeStorage(
            [
                _make_record(
                    LEADER_ID,
                    team_config=TeamConfig(
                        member_ids=["self-built", "invited-a"],
                    ),
                ),
                _make_record("self-built", parent_agent_id=LEADER_ID),
                _make_record("invited-a"),
            ],
        )
        access = MagicMock()

        async def resolve_for_edit(user_id: str, kind, agent_id: str):
            return OWNER, storage.get(agent_id)

        access.resolve_for_edit = AsyncMock(side_effect=resolve_for_edit)
        return storage, access

    async def test_replace_keeps_invited_member_unstamped(self) -> None:
        """Keeping an invited member in member_ids leaves it unstamped."""
        storage, access = self._build_fixture()
        resp = await set_team_config(
            LEADER_ID,
            SetTeamConfigRequest(member_ids=["self-built", "invited-a"]),
            user_id=OWNER,
            access=access,
            storage=storage,
        )

        # The invited member keeps its None backlink; the self-built one
        # keeps pointing at the leader.
        self.assertIsNone(storage.get("invited-a").data.parent_agent_id)
        self.assertEqual(
            storage.get("self-built").data.parent_agent_id,
            LEADER_ID,
        )
        # The response still classifies them correctly.
        by_id = {m.agent_id: m for m in resp.members}
        self.assertTrue(by_id["self-built"].is_self_built)
        self.assertFalse(by_id["invited-a"].is_self_built)

    async def test_replace_clears_removed_self_built_backlink(self) -> None:
        """Removing a self-built member clears its backlink."""
        storage, access = self._build_fixture()
        await set_team_config(
            LEADER_ID,
            SetTeamConfigRequest(member_ids=["invited-a"]),
            user_id=OWNER,
            access=access,
            storage=storage,
        )

        self.assertIsNone(storage.get("self-built").data.parent_agent_id)
        self.assertIsNone(storage.get("invited-a").data.parent_agent_id)

    async def test_replace_does_not_steal_foreign_member(self) -> None:
        """A member owned here but self-built under another leader stays
        with its original leader."""
        storage = _FakeStorage(
            [
                _make_record(
                    LEADER_ID,
                    team_config=TeamConfig(member_ids=["foreign-built"]),
                ),
                _make_record("foreign-built", parent_agent_id="other-leader"),
            ],
        )
        access = MagicMock()

        async def resolve_for_edit(user_id: str, kind, agent_id: str):
            return OWNER, storage.get(agent_id)

        access.resolve_for_edit = AsyncMock(side_effect=resolve_for_edit)
        await set_team_config(
            LEADER_ID,
            SetTeamConfigRequest(member_ids=["foreign-built"]),
            user_id=OWNER,
            access=access,
            storage=storage,
        )

        # Still registered under its real leader, not stolen.
        self.assertEqual(
            storage.get("foreign-built").data.parent_agent_id,
            "other-leader",
        )
