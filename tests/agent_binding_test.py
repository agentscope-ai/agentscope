# -*- coding: utf-8 -*-
"""Agents that come with their own MCPs and skills, without any network."""
import io
import tempfile
import zipfile
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.hub import (
    MCPCard,
    MCPHubBase,
    MCPHubPage,
    SkillArchive,
    SkillCard,
    SkillHubBase,
    SkillHubPage,
)
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}

SKILL_MD = """---
name: gifgrep
description: Find GIFs.
---

# gifgrep
"""


class _MCPHub(MCPHubBase):
    """One stateless card, so installing needs no connection."""

    def __init__(self) -> None:
        """Register the fixture card."""
        super().__init__("fake", "Fake MCP Hub")
        self.card = MCPCard(
            hub_id="fake",
            name="echo",
            auth="none",
            is_stateful=False,
            config_template={
                "type": "http_mcp",
                "url": "https://echo.invalid/sse",
            },
        )

    async def list_mcps(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> MCPHubPage:
        """Return the single fixture card."""
        return MCPHubPage(cards=[self.card])

    async def get_mcp(self, user_id: str, card_id: str) -> MCPCard:
        """Return the fixture card, or raise for anything else."""
        if card_id != "echo":
            raise KeyError(card_id)
        return self.card


class _SkillHub(SkillHubBase):
    """One card, counting how often its archive is fetched."""

    def __init__(self) -> None:
        """Register the fixture card."""
        super().__init__("fakeskills", "Fake Skill Hub")
        self.card = SkillCard(
            hub_id="fakeskills",
            name="gifgrep",
            description="Find GIFs.",
            markdown=SKILL_MD,
        )
        self.downloads = 0

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """Return the single fixture card."""
        return SkillHubPage(cards=[self.card])

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """Return the fixture card, or raise for anything else."""
        if card_id != "gifgrep":
            raise KeyError(card_id)
        return self.card

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """Open an in-memory ZIP holding one skill."""
        self.downloads += 1
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SKILL.md", SKILL_MD)
        payload = buffer.getvalue()

        async def _stream() -> AsyncIterator[bytes]:
            yield payload

        return SkillArchive(format="zip", stream=_stream())


def _fake_backends() -> tuple:
    """Build a fakeredis-backed storage and message bus."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    class _Storage(RedisStorage):
        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    class _Bus(RedisMessageBus):
        async def __aenter__(self) -> Any:
            self._client = redis
            return self

        async def aclose(self) -> None:
            self._client = None

    return _Storage(), _Bus()


class AgentBindingTest(IsolatedAsyncioTestCase):
    """An agent's own MCPs and skills reaching its workspace."""

    def setUp(self) -> None:
        """Start an app with one MCP and one skill in the library."""
        # pylint: disable=consider-using-with
        self._workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_backends()
        self._skill_hub = _SkillHub()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(self._workdir),
            mcp_hubs=[_MCPHub()],
            skill_hubs=[self._skill_hub],
            enable_index_worker=False,
        )
        self._client = self.enterContext(TestClient(app))

        self._mcp_id = self._client.post(
            "/hub/mcp/fake/install",
            params={"card_id": "echo"},
            json={},
            headers=HEADERS,
        ).json()["id"]
        self._skill_id = self._client.post(
            "/hub/skill/fakeskills/install",
            params={"card_id": "gifgrep"},
            json={},
            headers=HEADERS,
        ).json()["id"]

    # ── helpers ───────────────────────────────────────────────────

    def _agent(self, **bindings: list) -> str:
        """Create an agent, optionally bound to library records."""
        return self._client.post(
            "/agent/",
            json={"name": "tester", **bindings},
            headers=HEADERS,
        ).json()["agent_id"]

    def _session(self, agent_id: str) -> dict:
        """Open a session on an agent and return the workspace scope."""
        session_id = self._client.post(
            "/sessions/",
            json={"agent_id": agent_id},
            headers=HEADERS,
        ).json()["session_id"]
        return {"agent_id": agent_id, "session_id": session_id}

    def _workspace_mcps(self, scope: dict) -> dict:
        """Read the workspace's MCPs."""
        return self._client.get(
            "/workspace/mcp",
            params=scope,
            headers=HEADERS,
        ).json()

    def _workspace_skills(self, scope: dict) -> dict:
        """Read the workspace's skills."""
        return self._client.get(
            "/workspace/skill",
            params=scope,
            headers=HEADERS,
        ).json()

    # ── seeding ───────────────────────────────────────────────────

    def test_bound_mcp_and_skill_reach_a_new_workspace(self) -> None:
        """What the agent comes with is there the first time it opens."""
        scope = self._session(
            self._agent(
                mcp_ids=[self._mcp_id],
                skill_ids=[self._skill_id],
            ),
        )

        mcps = self._workspace_mcps(scope)
        skills = self._workspace_skills(scope)

        self.assertEqual([m["name"] for m in mcps["mcps"]], ["echo"])
        self.assertEqual([s["name"] for s in skills["skills"]], ["gifgrep"])
        self.assertEqual(mcps["seed_errors"], {})

    def test_no_bindings_leaves_the_workspace_empty(self) -> None:
        """An agent that comes with nothing seeds nothing."""
        scope = self._session(self._agent())

        self.assertEqual(self._workspace_mcps(scope)["mcps"], [])
        self.assertEqual(self._workspace_skills(scope)["skills"], [])
        self.assertEqual(self._skill_hub.downloads, 0)

    def test_seeding_happens_once_per_workspace(self) -> None:
        """Reading the workspace again must not re-fetch the archive."""
        scope = self._session(self._agent(skill_ids=[self._skill_id]))

        self._workspace_skills(scope)
        self._workspace_skills(scope)

        self.assertEqual(self._skill_hub.downloads, 1)

    def test_a_second_session_does_not_reseed(self) -> None:
        """Sessions of one agent share a workspace under PER_AGENT."""
        agent_id = self._agent(skill_ids=[self._skill_id])
        self._workspace_skills(self._session(agent_id))

        second = self._workspace_skills(self._session(agent_id))

        self.assertEqual([s["name"] for s in second["skills"]], ["gifgrep"])
        self.assertEqual(self._skill_hub.downloads, 1)

    def test_a_removed_skill_is_not_reinstated(self) -> None:
        """Deleting one is a decision, not damage to repair."""
        agent_id = self._agent(skill_ids=[self._skill_id])
        scope = self._session(agent_id)
        self._workspace_skills(scope)

        self._client.delete(
            "/workspace/skill/gifgrep",
            params=scope,
            headers=HEADERS,
        )
        after = self._workspace_skills(self._session(agent_id))

        self.assertEqual(after["skills"], [])
        self.assertEqual(self._skill_hub.downloads, 1)

    def test_a_removed_mcp_is_not_reinstated(self) -> None:
        """The MCP side of the same rule."""
        agent_id = self._agent(mcp_ids=[self._mcp_id])
        scope = self._session(agent_id)
        self._workspace_mcps(scope)

        self._client.delete(
            "/workspace/mcp/echo",
            params=scope,
            headers=HEADERS,
        )
        after = self._workspace_mcps(self._session(agent_id))

        self.assertEqual(after["mcps"], [])

    # ── failures ──────────────────────────────────────────────────

    def test_a_deleted_library_record_is_reported(self) -> None:
        """The binding outlives the record, and says so rather than
        failing the whole workspace."""
        agent_id = self._agent(
            mcp_ids=[self._mcp_id],
            skill_ids=[self._skill_id],
        )
        self._client.delete(f"/skill/{self._skill_id}", headers=HEADERS)
        scope = self._session(agent_id)

        skills = self._workspace_skills(scope)

        self.assertEqual(skills["skills"], [])
        self.assertEqual(
            list(skills["seed_errors"]),
            [self._skill_id],
        )
        # The MCP beside it still landed.
        self.assertEqual(
            [m["name"] for m in self._workspace_mcps(scope)["mcps"]],
            ["echo"],
        )

    def test_binding_an_unknown_id_is_rejected_on_write(self) -> None:
        """A typo surfaces at the form, not at some later boot."""
        created = self._client.post(
            "/agent/",
            json={"name": "tester", "mcp_ids": ["no-such-id"]},
            headers=HEADERS,
        )
        patched = self._client.patch(
            f"/agent/{self._agent()}",
            json={"skill_ids": ["no-such-id"]},
            headers=HEADERS,
        )

        self.assertEqual(created.status_code, 404)
        self.assertEqual(patched.status_code, 404)

    # ── round trip ────────────────────────────────────────────────

    def test_bindings_round_trip_through_the_record(self) -> None:
        """Both fields survive create and PATCH."""
        agent_id = self._agent(mcp_ids=[self._mcp_id])

        patched = self._client.patch(
            f"/agent/{agent_id}",
            json={"skill_ids": [self._skill_id]},
            headers=HEADERS,
        ).json()

        self.assertEqual(patched["data"]["mcp_ids"], [self._mcp_id])
        self.assertEqual(patched["data"]["skill_ids"], [self._skill_id])

    def test_an_old_record_reads_as_unbound(self) -> None:
        """Records written before the fields existed need no migration."""
        # pylint: disable=import-outside-toplevel
        from agentscope.app.storage import AgentData

        data = AgentData.model_validate(
            {
                "name": "legacy",
                "context_config": {},
                "react_config": {},
            },
        )

        self.assertEqual(data.mcp_ids, [])
        self.assertEqual(data.skill_ids, [])
