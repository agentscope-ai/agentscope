# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""Hub router test case — browse and install, without any network."""
import io
import json
import tempfile
import zipfile
from typing import Any, AsyncIterator
from urllib.parse import quote
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
    """A three-card MCP hub: one public, one behind an API key, and one
    carrying the display metadata an install snapshots."""

    def __init__(self) -> None:
        """Register the fixture cards."""
        super().__init__("fake", "Fake MCP Hub", "For testing.")
        self.cards = {
            "tagged": MCPCard(
                hub_id="fake",
                name="tagged",
                display_name="Tagged",
                description="A card with metadata.",
                tags=["search", "docs"],
                auth="none",
                config_template={
                    "type": "http_mcp",
                    "url": "https://tagged.invalid/sse",
                },
            ),
            "echo": MCPCard(
                hub_id="fake",
                name="echo",
                auth="none",
                is_stateful=False,
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
            "gitlab": MCPCard(
                hub_id="fake",
                name="gitlab",
                auth="inputs",
                is_stateful=True,
                inputs_schema={
                    "type": "object",
                    "properties": {
                        "api_key": {
                            "type": "string",
                            "writeOnly": True,
                            "format": "password",
                        },
                        "base_url": {
                            "type": "string",
                            "default": "https://gitlab.com/api/v4",
                        },
                    },
                    "required": ["api_key"],
                },
                config_template={
                    "type": "stdio_mcp",
                    "command": "uvx",
                    "args": ["gitlab-mcp"],
                    "env": {
                        "GITLAB_TOKEN": "${api_key}",
                        "GITLAB_API_URL": "${base_url}",
                    },
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
        self.card = SkillCard(
            hub_id="fakeskills",
            name="gifgrep",
            display_name="GIF Grep",
            description="Find GIFs.",
            tags=["media"],
            author="Len",
            icon_url="https://avatars.invalid/len",
            url="https://hub.invalid/skills/gifgrep",
            markdown=SKILL_MD,
        )
        self.owner_scoped_card = SkillCard(
            hub_id="fakeskills",
            id="runware/music",
            name="music",
            markdown=SKILL_MD,
        )
        self.download_users: list[str] = []

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
        if card_id == self.owner_scoped_card.id:
            return self.owner_scoped_card
        if card_id != "gifgrep":
            raise KeyError(card_id)
        return self.card

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield an in-memory ZIP, or raise for an unknown card."""
        self.download_users.append(user_id)
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
        self._skill_hub = FakeSkillHub()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            mcp_hubs=[FakeMCPHub()],
            skill_hubs=[self._skill_hub],
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
        self.assertEqual(sorted(names), ["echo", "gitlab", "notion", "tagged"])
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

    def test_gets_owner_scoped_skill_card(self) -> None:
        """A slash in an opaque card id stays inside the path parameter."""
        card_id = quote("runware/music", safe="")

        response = self._client.get(
            f"/hub/skill/fakeskills/cards/{card_id}",
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "runware/music")

    # ── install: MCP ──────────────────────────────────────────────

    def _install_mcp(self, card_id: str, **body: Any) -> Any:
        """POST an MCP install. Installing is user-level, so unlike the
        skill endpoint it takes no session scope."""
        return self._client.post(
            f"/hub/mcp/fake/cards/{card_id}/install",
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

    def test_install_lands_in_the_library(self) -> None:
        """A successful install shows up under ``GET /mcp`` carrying the
        provenance of the card it came from."""
        response = self._install_mcp("notion", values={"api_key": "sk"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "notion")

        listed = self._client.get("/mcp", headers=HEADERS).json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["hub_id"], "fake")
        self.assertEqual(listed[0]["card_id"], "notion")
        self.assertTrue(listed[0]["enabled"])

    def test_install_snapshots_the_display_fields(self) -> None:
        """Description and tags are copied, so the library can still show
        them when the hub is gone."""
        self._install_mcp("tagged")

        listed = self._client.get("/mcp", headers=HEADERS).json()
        self.assertEqual(listed[0]["display_name"], "Tagged")
        self.assertEqual(listed[0]["description"], "A card with metadata.")
        self.assertEqual(listed[0]["tags"], ["search", "docs"])

    def test_install_does_not_touch_the_workspace(self) -> None:
        """Installing is user-level — no session's workspace changes."""
        self._install_mcp("echo")

        listed = self._client.get(
            "/workspace/mcp",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual([m["name"] for m in listed], [])

    def test_install_never_echoes_the_rendered_config(self) -> None:
        """The response must not hand the submitted secret back."""
        body = self._install_mcp(
            "notion",
            values={"api_key": "sk"},
        ).json()

        self.assertNotIn("mcp_config", body)
        self.assertNotIn("sk", json.dumps(body))

    def test_install_rejects_duplicate_name(self) -> None:
        """The MCP name is unique per user."""
        self._install_mcp("echo")
        response = self._install_mcp("echo")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            len(self._client.get("/mcp", headers=HEADERS).json()),
            1,
        )

    def test_install_under_a_custom_name(self) -> None:
        """A caller can sidestep a clash by naming the install."""
        self._install_mcp("echo")
        response = self._install_mcp("echo", name="echo-2")

        self.assertEqual(response.status_code, 201)
        names = [
            m["name"] for m in self._client.get("/mcp", headers=HEADERS).json()
        ]
        self.assertEqual(names, ["echo", "echo-2"])

    def test_uninstall_removes_it_from_the_library(self) -> None:
        """Deleting frees the record and its name."""
        mcp_id = self._install_mcp("echo").json()["id"]

        self.assertEqual(
            self._client.delete(f"/mcp/{mcp_id}", headers=HEADERS).status_code,
            204,
        )
        self.assertEqual(self._client.get("/mcp", headers=HEADERS).json(), [])
        self.assertEqual(
            self._client.delete(f"/mcp/{mcp_id}", headers=HEADERS).status_code,
            404,
        )

    def test_library_is_per_user(self) -> None:
        """One user's install is invisible to another."""
        self._install_mcp("echo")

        listed = self._client.get(
            "/mcp",
            headers={"X-User-ID": "someone-else"},
        ).json()
        self.assertEqual(listed, [])

    async def test_rekey_rerenders_the_config(self) -> None:
        """A new API key is a re-render: the secret can sit anywhere in
        the config, so only the card's template knows where to put it."""
        mcp_id = self._install_mcp(
            "notion",
            values={"api_key": "old"},
        ).json()["id"]

        response = self._client.patch(
            f"/mcp/{mcp_id}",
            json={"values": {"api_key": "new"}},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        # Never echoed back — the response carries no config at all.
        self.assertNotIn("new", json.dumps(response.json()))

        # Read the record back to see the key actually moved into the
        # header the template puts it in.
        storage = self._client.app.state.storage
        record = await storage.get_mcp("alice", mcp_id)
        auth = record.client.mcp_config.headers["Authorization"]
        self.assertEqual(auth, "Bearer new")
        self.assertEqual(record.values, {"api_key": "new"})

    async def test_rekey_merges_over_the_stored_answers(self) -> None:
        """Only the changed key is sent, so a write-only field the form
        never echoed back must survive untouched."""
        mcp_id = self._install_mcp(
            "notion",
            values={"api_key": "old"},
        ).json()["id"]

        self._client.patch(
            f"/mcp/{mcp_id}",
            json={"name": "notion-2"},
            headers=HEADERS,
        )

        storage = self._client.app.state.storage
        record = await storage.get_mcp("alice", mcp_id)
        self.assertEqual(record.values, {"api_key": "old"})
        self.assertEqual(record.client.name, "notion-2")

    async def test_rekey_preserves_custom_non_secret_value(self) -> None:
        """Editing one input must not replace another with its schema
        default."""
        mcp_id = self._install_mcp(
            "gitlab",
            values={
                "api_key": "old",
                "base_url": "https://gitlab.internal/api/v4",
            },
        ).json()["id"]

        response = self._client.patch(
            f"/mcp/{mcp_id}",
            json={"values": {"api_key": "new"}},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        storage = self._client.app.state.storage
        record = await storage.get_mcp("alice", mcp_id)
        self.assertEqual(
            record.values,
            {
                "api_key": "new",
                "base_url": "https://gitlab.internal/api/v4",
            },
        )
        self.assertEqual(
            record.client.mcp_config.env["GITLAB_API_URL"],
            "https://gitlab.internal/api/v4",
        )

    def test_rename_keeps_the_config(self) -> None:
        """Renaming touches the name, not the rendered config."""
        mcp_id = self._install_mcp("echo").json()["id"]

        response = self._client.patch(
            f"/mcp/{mcp_id}",
            json={"name": "echo-renamed"},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "echo-renamed")

    def test_rename_onto_a_taken_name_is_a_conflict(self) -> None:
        """Names stay unique per user through edits too."""
        self._install_mcp("echo")
        mcp_id = self._install_mcp("tagged").json()["id"]

        response = self._client.patch(
            f"/mcp/{mcp_id}",
            json={"name": "echo"},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 409)

    def test_disable_keeps_the_record(self) -> None:
        """A disabled MCP is kept so re-enabling does not lose config."""
        mcp_id = self._install_mcp("echo").json()["id"]

        response = self._client.patch(
            f"/mcp/{mcp_id}",
            json={"enabled": False},
            headers=HEADERS,
        )

        self.assertFalse(response.json()["enabled"])
        self.assertEqual(
            len(self._client.get("/mcp", headers=HEADERS).json()),
            1,
        )

    def test_update_missing_returns_404(self) -> None:
        """Editing an MCP that is not in the library is a 404."""
        response = self._client.patch(
            "/mcp/no-such-id",
            json={"enabled": False},
            headers=HEADERS,
        )

        self.assertEqual(response.status_code, 404)

    # ── workspace <-> library ─────────────────────────────────────

    def test_hand_added_mcp_lands_in_the_library(self) -> None:
        """One typed in by hand is reusable next session, and carries no
        card, which is what marks it as un-rekeyable."""
        self._client.post(
            "/workspace/mcp",
            params=self._scope,
            json={
                "name": "hand-rolled",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://hand.invalid/sse",
                },
            },
            headers=HEADERS,
        )

        listed = self._client.get("/mcp", headers=HEADERS).json()
        self.assertEqual([m["name"] for m in listed], ["hand-rolled"])
        self.assertIsNone(listed[0]["hub_id"])

    def test_library_definition_is_not_redefined_by_a_second_add(
        self,
    ) -> None:
        """The library is where an MCP is defined; adding it to another
        workspace must not silently overwrite that."""
        mcp_id = self._install_mcp("echo").json()["id"]

        self._client.post(
            "/workspace/mcp",
            params=self._scope,
            json={
                "name": "echo",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "https://different.invalid/sse",
                },
            },
            headers=HEADERS,
        )

        listed = self._client.get("/mcp", headers=HEADERS).json()
        self.assertEqual(len(listed), 1)
        # Still the record the install created, hub provenance intact.
        self.assertEqual(listed[0]["id"], mcp_id)
        self.assertEqual(listed[0]["hub_id"], "fake")

    def test_add_from_library_reports_each_pick(self) -> None:
        """A bad pick must not throw away the good ones."""
        mcp_id = self._install_mcp("echo").json()["id"]

        response = self._client.post(
            "/workspace/mcp/from-library",
            params=self._scope,
            json={"mcp_ids": [mcp_id, "no-such-id"]},
            headers=HEADERS,
        )

        body = response.json()
        self.assertEqual(body["added"], ["echo"])
        self.assertIn("no-such-id", body["failed"])
        self.assertEqual(len(body["failed"]), 1)
        listed = self._client.get(
            "/workspace/mcp",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual([m["name"] for m in listed], ["echo"])

    def test_add_from_library_skips_what_is_already_there(self) -> None:
        """Adding twice is a no-op, not a duplicate or an error."""
        mcp_id = self._install_mcp("echo").json()["id"]
        payload = {"mcp_ids": [mcp_id]}
        self._client.post(
            "/workspace/mcp/from-library",
            params=self._scope,
            json=payload,
            headers=HEADERS,
        )

        again = self._client.post(
            "/workspace/mcp/from-library",
            params=self._scope,
            json=payload,
            headers=HEADERS,
        ).json()

        self.assertEqual(again["added"], [])
        self.assertEqual(again["failed"], {})

    def test_unreachable_mcp_reports_why(self) -> None:
        """A red dot alone is not actionable — the reason has to reach
        the client, unwrapped out of the transport's task group."""
        self._client.post(
            "/workspace/mcp",
            params=self._scope,
            json={
                "name": "broken",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "http://127.0.0.1:1/mcp",
                    "timeout": 1.0,
                },
            },
            headers=HEADERS,
        )

        listed = self._client.get(
            "/workspace/mcp",
            params=self._scope,
            headers=HEADERS,
        ).json()

        self.assertFalse(listed[0]["is_healthy"])
        detail = listed[0]["error"]
        self.assertTrue(detail)
        # The bare ExceptionGroup message says nothing useful; the leaf
        # cause is what has to come through.
        self.assertNotIn("TaskGroup", detail)

    # ── install: skill ────────────────────────────────────────────

    def _install_skill(self, card_id: str, **params: str) -> Any:
        """POST a skill install. User-level, like the MCP one."""
        return self._client.post(
            f"/hub/skill/fakeskills/cards/{card_id}/install",
            params=params,
            headers=HEADERS,
        )

    def test_install_skill_lands_in_the_library(self) -> None:
        """A successful install shows up under ``GET /skill``."""
        response = self._install_skill("gifgrep")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "gifgrep")

        listed = self._client.get("/skill", headers=HEADERS).json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["hub_id"], "fakeskills")
        self.assertEqual(listed[0]["card_id"], "gifgrep")

    def test_installs_owner_scoped_skill_card(self) -> None:
        """An owner-scoped card id is accepted by the install route."""
        card_id = quote("runware/music", safe="")

        response = self._install_skill(card_id)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["card_id"], "runware/music")

    def test_install_snapshots_the_display_identity(self) -> None:
        """Author, icon and link survive the install, so the library
        does not lose the listing's identity the moment it is added."""
        self._install_skill("gifgrep")

        listed = self._client.get("/skill", headers=HEADERS).json()[0]
        self.assertEqual(listed["author"], "Len")
        self.assertEqual(listed["icon_url"], "https://avatars.invalid/len")
        self.assertEqual(listed["url"], "https://hub.invalid/skills/gifgrep")

    def test_install_skill_does_not_touch_the_workspace(self) -> None:
        """Installing is user-level — no session's workspace changes."""
        self._install_skill("gifgrep")

        listed = self._client.get(
            "/workspace/skill",
            params=self._scope,
            headers=HEADERS,
        ).json()
        self.assertEqual([s["name"] for s in listed], [])

    def test_install_skill_does_not_download(self) -> None:
        """The archive is fetched when the skill reaches a workspace,
        not when it is added to the library."""
        self._install_skill("gifgrep")

        self.assertEqual(self._skill_hub.download_users, [])

    def test_skill_detail_carries_the_markdown(self) -> None:
        """The list view omits ``SKILL.md``; the detail view has it."""
        skill_id = self._install_skill("gifgrep").json()["id"]

        self.assertNotIn(
            "markdown",
            self._client.get("/skill", headers=HEADERS).json()[0],
        )
        detail = self._client.get(f"/skill/{skill_id}", headers=HEADERS)
        self.assertEqual(detail.json()["markdown"], SKILL_MD)

    def test_install_skill_rejects_duplicate_name(self) -> None:
        """The skill name is unique per user."""
        self._install_skill("gifgrep")
        response = self._install_skill("gifgrep")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            len(self._client.get("/skill", headers=HEADERS).json()),
            1,
        )

    def test_install_skill_under_a_custom_name(self) -> None:
        """A caller can sidestep a clash by naming the install."""
        self._install_skill("gifgrep")
        response = self._install_skill("gifgrep", name="gifgrep-2")

        self.assertEqual(response.status_code, 201)
        names = [
            s["name"]
            for s in self._client.get("/skill", headers=HEADERS).json()
        ]
        self.assertEqual(names, ["gifgrep", "gifgrep-2"])

    def test_install_unknown_skill(self) -> None:
        """A card the hub does not have is a 404."""
        response = self._install_skill("missing")

        self.assertEqual(response.status_code, 404)

    def test_uninstall_skill(self) -> None:
        """Deleting frees the record and its name."""
        skill_id = self._install_skill("gifgrep").json()["id"]

        self.assertEqual(
            self._client.delete(
                f"/skill/{skill_id}",
                headers=HEADERS,
            ).status_code,
            204,
        )
        self.assertEqual(
            self._client.get("/skill", headers=HEADERS).json(),
            [],
        )
        self.assertEqual(
            self._client.delete(
                f"/skill/{skill_id}",
                headers=HEADERS,
            ).status_code,
            404,
        )

    def test_skill_library_is_per_user(self) -> None:
        """One user's install is invisible to another."""
        self._install_skill("gifgrep")

        listed = self._client.get(
            "/skill",
            headers={"X-User-ID": "someone-else"},
        ).json()
        self.assertEqual(listed, [])


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
