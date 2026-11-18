# -*- coding: utf-8 -*-
"""Tests for Feishu user OAuth and Wiki browsing."""

# pylint: disable=protected-access
import time
from datetime import timezone
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.channel._feishu import FeishuChannel
from agentscope.app.channel._feishu._user_oauth import (
    _FeishuDeviceFlowClient,
)

_SCOPES = [
    "docx:document:readonly",
    "wiki:node:read",
    "wiki:node:retrieve",
    "wiki:space:retrieve",
]


class _Storage:
    """Minimal channel-user credential storage fake."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_channel_user_credentials(
        self,
        channel_id: str,
        channel_user_id: str,
    ) -> dict[str, Any] | None:
        """Return stored credentials for one channel user."""
        return self.values.get((channel_id, channel_user_id))

    async def upsert_channel_user_credentials(
        self,
        channel_id: str,
        channel_user_id: str,
        credentials: dict[str, Any],
    ) -> None:
        """Insert or replace credentials for one channel user."""
        self.values[(channel_id, channel_user_id)] = credentials

    async def delete_channel_user_credentials(
        self,
        channel_id: str,
        channel_user_id: str,
    ) -> bool:
        """Delete credentials for one channel user."""
        return self.values.pop((channel_id, channel_user_id), None) is not None


class _Response:
    """Small httpx response stand-in."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def json(self) -> dict[str, Any]:
        """Return the scripted JSON body."""
        return self._data


def _channel() -> FeishuChannel:
    """Build a Feishu channel without opening network resources."""
    return FeishuChannel(
        "channel-1",
        FeishuChannel.Credentials(app_id="app", app_secret="secret"),
        FeishuChannel.Config(),
    )


def _valid_token(value: str = "user-token") -> dict[str, Any]:
    """Build a non-expiring-enough stored user token."""
    return {
        "access_token": value,
        "refresh_token": "refresh",
        "expires_at": time.time() + 3600,
        "refresh_expires_at": time.time() + 7200,
        "scopes": _SCOPES,
        "open_id": "ou-user",
    }


class FeishuWikiTest(IsolatedAsyncioTestCase):
    """Exercise Feishu's sender-scoped Wiki implementation."""

    async def test_user_api_uses_stored_user_token(self) -> None:
        """Wiki REST calls never reuse the tenant token."""
        channel = _channel()
        storage = _Storage()
        storage.values[("channel-1", "ou-user")] = _valid_token()
        channel._bind_storage(storage)  # type: ignore[arg-type]
        channel._token = "tenant-token"
        http = SimpleNamespace(
            request=AsyncMock(return_value=_Response({"code": 0, "data": {}})),
        )
        channel._http = http

        await channel._user_api(
            "ou-user",
            "list Wiki spaces",
            "GET",
            "/wiki/v2/spaces",
        )

        headers = http.request.await_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer user-token")
        self.assertNotIn("tenant-token", str(http.request.await_args))

    async def test_wiki_tools_require_a_trusted_sender(self) -> None:
        """Generic Wiki tools load only when a sender id is available."""
        channel = _channel()
        workspace = SimpleNamespace(get_backend=lambda: None)

        without_sender = await channel.list_tools(workspace)
        with_sender = await channel.list_tools(workspace, "ou-user")
        channel.capabilities = channel.capabilities.model_copy(
            update={"wiki": False},
        )
        disabled = await channel.list_tools(workspace, "ou-user")

        self.assertEqual(len(without_sender), 5)
        self.assertEqual(len(with_sender), 8)
        self.assertEqual(len(disabled), 5)

    async def test_near_expiry_token_is_refreshed_and_saved(self) -> None:
        """User tokens are refreshed five minutes before expiry."""
        channel = _channel()
        storage = _Storage()
        token = _valid_token("old-token")
        token["expires_at"] = time.time() + 20
        storage.values[("channel-1", "ou-user")] = token
        channel._bind_storage(storage)  # type: ignore[arg-type]
        channel._device_flow = SimpleNamespace(
            refresh=AsyncMock(
                return_value={
                    "access_token": "new-token",
                    "refresh_token": "new-refresh",
                    "expires_at": time.time() + 3600,
                    "refresh_expires_at": time.time() + 7200,
                    "scopes": _SCOPES,
                    "open_id": "",
                },
            ),
        )

        refreshed = await channel._require_user_token("ou-user")

        self.assertEqual(refreshed["access_token"], "new-token")
        self.assertEqual(refreshed["open_id"], "ou-user")
        self.assertEqual(
            storage.values[("channel-1", "ou-user")]["access_token"],
            "new-token",
        )

    async def test_invalid_token_reauthorizes_once(self) -> None:
        """An API invalid-token response clears state and retries once."""
        channel = _channel()
        storage = _Storage()
        storage.values[("channel-1", "ou-user")] = _valid_token("old-token")
        channel._bind_storage(storage)  # type: ignore[arg-type]
        channel._authorize_user = AsyncMock(
            return_value={**_valid_token("new-token")},
        )
        channel._device_flow = SimpleNamespace(
            refresh=AsyncMock(side_effect=RuntimeError("refresh failed")),
        )
        http = SimpleNamespace(
            request=AsyncMock(
                side_effect=[
                    _Response({"code": 99991671, "msg": "invalid"}),
                    _Response({"code": 0, "data": {"items": []}}),
                ],
            ),
        )
        channel._http = http

        result = await channel._user_api(
            "ou-user",
            "list Wiki spaces",
            "GET",
            "/wiki/v2/spaces",
        )

        self.assertEqual(result["code"], 0)
        self.assertEqual(channel._authorize_user.await_count, 1)
        first = http.request.await_args_list[0].kwargs["headers"]
        second = http.request.await_args_list[1].kwargs["headers"]
        self.assertEqual(first["Authorization"], "Bearer old-token")
        self.assertEqual(second["Authorization"], "Bearer new-token")

    async def test_invalid_token_refreshes_before_reauthorizing(self) -> None:
        """An expired API token is refreshed without another device flow."""
        channel = _channel()
        storage = _Storage()
        storage.values[("channel-1", "ou-user")] = _valid_token("old-token")
        channel._bind_storage(storage)  # type: ignore[arg-type]
        refreshed = _valid_token("new-token")
        channel._device_flow = SimpleNamespace(
            refresh=AsyncMock(return_value=refreshed),
        )
        channel._authorize_user = AsyncMock()
        channel._http = SimpleNamespace(
            request=AsyncMock(
                side_effect=[
                    _Response({"code": 99991668, "msg": "expired"}),
                    _Response({"code": 0, "data": {"items": []}}),
                ],
            ),
        )

        result = await channel._user_api(
            "ou-user",
            "list Wiki spaces",
            "GET",
            "/wiki/v2/spaces",
        )

        self.assertEqual(result["code"], 0)
        channel._authorize_user.assert_not_awaited()
        self.assertEqual(
            storage.values[("channel-1", "ou-user")]["access_token"],
            "new-token",
        )

    async def test_list_spaces_maps_page(self) -> None:
        """Space ids double as browsing roots and pagination is bounded."""
        channel = _channel()
        channel._user_api = AsyncMock(
            return_value={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "space_id": "spc-1",
                            "name": "Engineering",
                            "description": "Docs",
                        },
                    ],
                    "has_more": True,
                    "page_token": "next",
                },
            },
        )

        page = await channel.list_wiki_spaces("ou-user", 100)

        self.assertEqual(page.items[0].root_node_id, "spc-1")
        self.assertIsNone(page.items[0].url)
        self.assertEqual(page.next_token, "next")
        self.assertEqual(
            channel._user_api.await_args.kwargs["query"]["page_size"],
            50,
        )

    async def test_nested_node_resolves_space_and_maps_fields(self) -> None:
        """A Wiki node token is resolved before listing its children."""
        channel = _channel()
        channel._get_wiki_node = AsyncMock(
            return_value={"space_id": "spc-1"},
        )
        channel._user_api = AsyncMock(
            return_value={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "node_token": "wik-child",
                            "title": "Roadmap",
                            "has_child": True,
                            "obj_type": "docx",
                            "node_url": "https://example.test/wiki",
                            "obj_edit_time": "1700000000",
                        },
                    ],
                    "has_more": False,
                },
            },
        )

        page = await channel.list_wiki_nodes("ou-user", "wik-parent", 20)

        node = page.items[0]
        self.assertTrue(node.has_children)
        self.assertTrue(node.is_document)
        self.assertEqual(node.updated_at.tzinfo, timezone.utc)
        call = channel._user_api.await_args
        self.assertIn("/spaces/spc-1/nodes", call.args[3])
        self.assertEqual(
            call.kwargs["query"]["parent_node_token"],
            "wik-parent",
        )

    async def test_read_document_paginates_and_renders_markdown(self) -> None:
        """Flat block pages become ordered Markdown block trees."""
        channel = _channel()
        channel._get_wiki_node = AsyncMock(
            return_value={
                "obj_type": "docx",
                "obj_token": "doc/1",
                "title": "Guide",
            },
        )
        pages = [
            {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "page",
                            "block_type": 1,
                            "children": ["heading", "list", "table"],
                        },
                        {
                            "block_id": "heading",
                            "block_type": 3,
                            "heading1": {
                                "elements": [
                                    {"text_run": {"content": "Title"}},
                                ],
                            },
                        },
                    ],
                    "has_more": True,
                    "page_token": "p2",
                },
            },
            {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "list",
                            "block_type": 12,
                            "bullet": {
                                "elements": [
                                    {
                                        "text_run": {
                                            "content": "linked",
                                            "text_element_style": {
                                                "link": {
                                                    "url": (
                                                        "https://example.test"
                                                    ),
                                                },
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                        {
                            "block_id": "table",
                            "block_type": 31,
                            "table": {
                                "property": {
                                    "row_size": 2,
                                    "column_size": 1,
                                },
                                "cells": ["c1", "c2"],
                            },
                        },
                        {
                            "block_id": "c1",
                            "block_type": 32,
                            "children": ["t1"],
                        },
                        {
                            "block_id": "c2",
                            "block_type": 32,
                            "children": ["t2"],
                        },
                        {
                            "block_id": "t1",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": "Header"}},
                                ],
                            },
                        },
                        {
                            "block_id": "t2",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"equation": {"content": "x|y"}},
                                ],
                            },
                        },
                    ],
                    "has_more": False,
                },
            },
        ]
        channel._user_api = AsyncMock(side_effect=pages)

        document = await channel.read_wiki_document(
            "ou-user",
            "wik-node",
            0,
            3,
        )

        self.assertEqual(document.name, "Guide")
        text = document.content[0].text
        self.assertIn("# Title", text)
        self.assertIn("- [linked](https://example.test)", text)
        self.assertIn("| Header |", text)
        self.assertIn("$x\\|y$", text)
        self.assertIsNone(document.next_start_index)
        self.assertIn("doc%2F1", channel._user_api.await_args.args[3])

    async def test_non_docx_node_is_not_readable(self) -> None:
        """Legacy documents and other Wiki objects stay unsupported."""
        channel = _channel()
        channel._get_wiki_node = AsyncMock(
            return_value={"obj_type": "sheet", "title": "Sheet"},
        )
        self.assertIsNone(
            await channel.read_wiki_document("ou-user", "wik-node", 0, 20),
        )

    async def test_character_budget_never_splits_a_top_level_block(
        self,
    ) -> None:
        """An oversized first block is returned whole with a resume index."""
        channel = _channel()
        large = "x" * 20_001
        channel._user_api = AsyncMock(
            return_value={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "block_id": "page",
                            "block_type": 1,
                            "children": ["first", "second"],
                        },
                        {
                            "block_id": "first",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": large}},
                                ],
                            },
                        },
                        {
                            "block_id": "second",
                            "block_type": 2,
                            "text": {
                                "elements": [
                                    {"text_run": {"content": "next"}},
                                ],
                            },
                        },
                    ],
                    "has_more": False,
                },
            },
        )

        content, next_index = await channel._read_docx_blocks(
            "ou-user",
            "document",
            0,
            2,
        )

        self.assertEqual(content, large)
        self.assertEqual(next_index, 1)

    async def test_device_flow_verifies_sender_before_save(self) -> None:
        """An authorization from a different account is discarded."""
        channel = _channel()
        storage = _Storage()
        channel._bind_storage(storage)  # type: ignore[arg-type]
        channel._api = AsyncMock(return_value={"code": 0})
        flow = SimpleNamespace(
            start=AsyncMock(
                return_value={
                    "verification_uri_complete": "https://example.test/auth",
                    "user_code": "ABCD",
                    "device_code": "device",
                    "expires_in": 60,
                    "interval": 1,
                },
            ),
            poll=AsyncMock(
                return_value={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_at": time.time() + 3600,
                    "refresh_expires_at": time.time() + 7200,
                    "scopes": _SCOPES,
                    "open_id": "",
                },
            ),
        )
        channel._device_flow = flow
        channel._get_user_open_id = AsyncMock(return_value="ou-other")

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            await channel._require_user_token("ou-user")
        self.assertEqual(storage.values, {})
        self.assertEqual(channel._api.await_args.args[0], "POST")


class FeishuDeviceFlowTest(IsolatedAsyncioTestCase):
    """Exercise the minimal Feishu device-flow protocol adapter."""

    async def test_device_flow_protocol(self) -> None:
        """Start, poll, and refresh follow Feishu's live API contract."""
        http = SimpleNamespace(
            post=AsyncMock(
                side_effect=[
                    _Response(
                        {
                            "verification_uri": "https://example.test/auth",
                            "user_code": "ABCD",
                            "device_code": "device",
                            "expires_in": 600,
                            "interval": 5,
                        },
                    ),
                    _Response({"error": "authorization_pending"}),
                    _Response(
                        {
                            "access_token": "access",
                            "refresh_token": "refresh",
                            "expires_in": 3600,
                            "scope": "wiki:node:read offline_access",
                        },
                    ),
                    _Response(
                        {
                            "access_token": "new-access",
                            "refresh_token": "new-refresh",
                            "expires_in": 3600,
                            "scope": "wiki:node:read offline_access",
                        },
                    ),
                ],
            ),
        )
        client = _FeishuDeviceFlowClient(
            "app",
            "secret",
            http_client=http,
        )

        request = await client.start(["wiki:node:read"])

        call = http.post.await_args_list[0]
        self.assertEqual(
            call.args[0],
            "https://accounts.feishu.cn/oauth/v1/device_authorization",
        )
        self.assertEqual(call.kwargs["auth"], ("app", "secret"))
        self.assertIn("offline_access", call.kwargs["data"]["scope"])
        self.assertEqual(request["device_code"], "device")

        with patch(
            "agentscope.app.channel._feishu._user_oauth.asyncio.sleep",
            new=AsyncMock(),
        ):
            token = await client.poll("device", interval=1)

        self.assertEqual(token["access_token"], "access")
        self.assertGreater(token["expires_at"] or 0, time.time())
        refreshed = await client.refresh("refresh")

        call = http.post.await_args_list[3]
        self.assertEqual(
            call.args[0],
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        )
        self.assertEqual(call.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(refreshed["access_token"], "new-access")
