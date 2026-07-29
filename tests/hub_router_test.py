# -*- coding: utf-8 -*-
"""Hub router test case — browse and install, without any network."""
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


def _zip_bytes(files: dict) -> bytes:
    """Build an in-memory ZIP from ``{name: text}``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class FakeMCPHub(MCPHubBase):
    """A two-card MCP hub: one public, one behind an API key."""

    def __init__(self) -> None:
        """Register the fixture cards."""
        super().__init__("fake", "Fake MCP Hub", "For testing.")
        self.cards = {
            "echo": MCPCard(
                hub_id="fake",
                name="echo",
                auth="none",
                config_template={
                    "type": "http_mcp",
                    "url": "https://echo.invalid/sse",
                },
            ),
            "notion": MCPCard(
                hub_id="fake",
                name="notion",
                inputs_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {
                            "type": "string",
                            "writeOnly": True,
                            "format": "password",
                        },
                    },
                    "required": ["api_key"],
                },
                config_template={
                    "type": "http_mcp",
                    "url": "https://notion.invalid/sse",
                    "headers": {"Authorization": "Bearer ${api_key}"},
                },
            ),
        }

    async def list_mcps(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> MCPHubPage:
        """Return the fixture cards, filtered by ``q``."""
        cards = [
            card for card in self.cards.values() if not q or q in card.name
        ]
        return MCPHubPage(cards=cards[:limit])

    async def get_mcp(self, user_id: str, card_id: str) -> MCPCard:
        """Return one fixture card."""
        return self.cards[card_id]


class FakeSkillHub(SkillHubBase):
    """A one-card skill hub serving an in-memory archive."""

    def __init__(self) -> None:
        """Register the fixture card."""
        super().__init__("fakeskills", "Fake Skill Hub")
        self.card = SkillCard(hub_id="fakeskills", name="gifgrep")

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """Return the single fixture card."""
        return SkillHubPage(cards=[self.card], next_cursor="page-2")

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """Return the fixture card, or raise for anything else."""
        if card_id != "gifgrep":
            raise KeyError(card_id)
        return self.card

    async def download(
        self,
        card_id: str,
        version: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield an in-memory ZIP, or raise for an unknown card."""
        if card_id == "nozip":
            yield b"not a zip at all"
            return
        if card_id == "nomd":
            yield _zip_bytes({"README.md": "nothing here"})
            return
        if card_id != "gifgrep":
            raise KeyError(card_id)
        yield _zip_bytes({"SKILL.md": SKILL_MD, "notes.md": "x"})


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


class HubRouterTest(IsolatedAsyncioTestCase):
    """Browse and install through the hub endpoints."""

    def setUp(self) -> None:
        """Start an app wired to the fake hubs and a local workspace."""
        # enterContext is the unittest-native way to bind a context
        # manager to the test's lifetime; pylint does not recognise it.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_backends()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            mcp_hubs=[FakeMCPHub()],
            skill_hubs=[FakeSkillHub()],
            enable_index_worker=False,
        )
        self._client = self.enterContext(TestClient(app))

        agent_id = self._client.post(
            "/agent/",
            json={"name": "tester", "system_prompt": "hi"},
            headers=HEADERS,
        ).json()["agent_id"]
        session_id = self._client.post(
            "/sessions/",
            json={"agent_id": agent_id},
            headers=HEADERS,
        ).json()["session_id"]
        self._scope = {"agent_id": agent_id, "session_id": session_id}

    # ── browse ────────────────────────────────────────────────────

    def test_lists_hubs(self) -> None:
        """Each kind lists only its own hubs."""
        mcp = self._client.get("/hub/mcp", headers=HEADERS).json()
        skill = self._client.get("/hub/skill", headers=HEADERS).json()

        self.assertEqual([h["hub_id"] for h in mcp], ["fake"])
        self.assertEqual([h["hub_id"] for h in skill], ["fakeskills"])
        self.assertEqual(mcp[0]["display_name"], "Fake MCP Hub")

    def test_browses_cards(self) -> None:
        """Cards come back with the inputs the user must fill."""
        body = self._client.get(
            "/hub/mcp/fake/cards",
            headers=HEADERS,
        ).json()

        names = [c["name"] for c in body["cards"]]
        self.assertEqual(sorted(names), ["echo", "notion"])
        notion = next(c for c in body["cards"] if c["name"] == "notion")
        prop = notion["inputs_schema"]["properties"]["api_key"]
        self.assertTrue(prop["writeOnly"])

    def test_search_and_cursor_are_forwarded(self) -> None:
        """``q`` filters, and the hub's cursor reaches the caller."""
        filtered = self._client.get(
            "/hub/mcp/fake/cards",
            params={"q": "not"},
            headers=HEADERS,
        ).json()
        skills = self._client.get(
            "/hub/skill/fakeskills/cards",
            headers=HEADERS,
        ).json()

        self.assertEqual([c["name"] for c in filtered["cards"]], ["notion"])
        self.assertEqual(skills["next_cursor"], "page-2")

    def test_unknown_hub(self) -> None:
        """An unregistered hub id is a 404."""
        response = self._client.get("/hub/mcp/nope/cards", headers=HEADERS)

        self.assertEqual(response.status_code, 404)

    def test_unknown_card(self) -> None:
        """An unknown card id is a 404."""
        response = self._client.get(
            "/hub/skill/fakeskills/cards/missing",
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 404)

    # ── install: MCP ──────────────────────────────────────────────

    def _install_mcp(self, card_id: str, **body: Any) -> Any:
        """POST an MCP install for the fixture session."""
        return self._client.post(
            f"/hub/mcp/fake/cards/{card_id}/install",
            params=self._scope,
            json=body,
            headers=HEADERS,
        )

    def test_install_rejects_missing_value(self) -> None:
        """A required input the caller omitted is a 400."""
        response = self._install_mcp("notion", values={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("api_key", response.json()["detail"])

    def test_install_rejects_wrong_type(self) -> None:
        """A value violating the schema type is a 400."""
        response = self._install_mcp("notion", values={"api_key": 123})

        self.assertEqual(response.status_code, 400)

    def test_install_reports_connect_failure(self) -> None:
        """An unreachable server fails the install rather than
        silently adding a broken MCP."""
        response = self._install_mcp("notion", values={"api_key": "sk"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Failed to connect", response.json()["detail"])
        listed = self._client.get(
            "/workspace/mcp",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual(listed, [])

    # ── install: skill ────────────────────────────────────────────

    def _install_skill(self, card_id: str) -> Any:
        """POST a skill install for the fixture session."""
        return self._client.post(
            f"/hub/skill/fakeskills/cards/{card_id}/install",
            params=self._scope,
            headers=HEADERS,
        )

    def test_install_skill(self) -> None:
        """The archive is unpacked and the real name reported back."""
        response = self._install_skill("gifgrep")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "gifgrep")
        self.assertEqual(body["hub_id"], "fakeskills")
        self.assertFalse(body["already_present"])

        listed = self._client.get(
            "/workspace/skill",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual([s["name"] for s in listed], ["gifgrep"])

    def test_reinstall_is_reported_not_faked(self) -> None:
        """A second install adds nothing, and says so."""
        self._install_skill("gifgrep")
        response = self._install_skill("gifgrep")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["already_present"])
        listed = self._client.get(
            "/workspace/skill",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual(len(listed), 1)

    def test_install_rejects_non_zip(self) -> None:
        """A payload that is not a ZIP is a 400."""
        response = self._install_skill("nozip")

        self.assertEqual(response.status_code, 400)

    def test_install_rejects_archive_without_skill_md(self) -> None:
        """An archive with no ``SKILL.md`` is a 400."""
        response = self._install_skill("nomd")

        self.assertEqual(response.status_code, 400)

    def test_install_unknown_skill(self) -> None:
        """A card the hub cannot download is a 404."""
        response = self._install_skill("missing")

        self.assertEqual(response.status_code, 404)


class HubRegistrationTest(IsolatedAsyncioTestCase):
    """Hub registration invariants and workspace name uniqueness."""

    def test_rejects_duplicate_hub_ids(self) -> None:
        """Two hubs sharing an id could not be told apart in the routes."""
        storage, bus = _fake_backends()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                create_app(
                    storage=storage,
                    message_bus=bus,
                    workspace_manager=LocalWorkspaceManager(tmp),
                    skill_hubs=[FakeSkillHub(), FakeSkillHub()],
                    enable_index_worker=False,
                )

        self.assertIn("fakeskills", str(ctx.exception))

    async def test_local_workspace_rejects_duplicate_mcp(self) -> None:
        """Names compose ``mcp__{name}__{tool}``, so they must be unique.

        The sandboxed backends already enforced this; the local one used
        to append silently.
        """
        from agentscope.mcp import MCPClient
        from agentscope.workspace import LocalWorkspace

        with tempfile.TemporaryDirectory() as tmp:
            workspace = LocalWorkspace(workdir=tmp)
            await workspace.initialize()

            def _client() -> MCPClient:
                return MCPClient(
                    name="git",
                    is_stateful=False,
                    mcp_config={
                        "type": "http_mcp",
                        "url": "https://example.invalid/sse",
                    },
                )

            await workspace.add_mcp(_client())
            with self.assertRaises(ValueError) as ctx:
                await workspace.add_mcp(_client())

            self.assertIn("already exists", str(ctx.exception))
            self.assertEqual(len(await workspace.list_mcps()), 1)
