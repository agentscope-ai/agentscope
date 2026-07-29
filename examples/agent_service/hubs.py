# -*- coding: utf-8 -*-
"""An example MCP hub serving a hand-written catalog.

The repository ships :class:`ClawSkillHub` for skills but no concrete MCP
hub, so this module provides one to demonstrate the interface — including
the part that matters most in the UI: a card whose config template holds a
``${...}`` placeholder the user fills in at install time.

The hub itself needs no credentials. Whether a *card* needs one is the
card's own business, declared in its ``inputs_schema``.
"""
from agentscope.app.hub import MCPCard, MCPHubBase, MCPHubPage

HUB_ID = "examples"

CARDS: list[MCPCard] = [
    MCPCard(
        hub_id=HUB_ID,
        name="deepwiki",
        author="Devin",
        icon_url="https://avatars.githubusercontent.com/u/128686189?s=64",
        url="https://deepwiki.com",
        installs=4821,
        display_name="DeepWiki",
        description=(
            "Ask questions about any public GitHub repository. Needs no "
            "credentials."
        ),
        tags=["docs", "search"],
        version="1.0.0",
        is_stateful=False,
        auth="none",
        config_template={
            "type": "http_mcp",
            "url": "https://mcp.deepwiki.com/mcp",
        },
    ),
    MCPCard(
        hub_id=HUB_ID,
        name="amap",
        author="Amap",
        url="https://lbs.amap.com/api/mcp-server/summary",
        installs=1290,
        display_name="Amap (Gaode Maps)",
        description="Geocoding, routing and POI search across China.",
        tags=["maps", "geo"],
        version="1.0.0",
        is_stateful=False,
        auth="inputs",
        inputs_schema={
            "type": "object",
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API Key",
                    "description": (
                        "Create one at console.amap.com under 'Web 服务'."
                    ),
                    # The two keywords the frontend keys off to mask a
                    # field; both are standard, neither is invented here.
                    "writeOnly": True,
                    "format": "password",
                },
            },
            "required": ["api_key"],
        },
        config_template={
            "type": "http_mcp",
            "url": "https://mcp.amap.com/mcp?key=${api_key}",
        },
    ),
    MCPCard(
        hub_id=HUB_ID,
        name="playwright",
        author="Microsoft",
        icon_url="https://avatars.githubusercontent.com/u/6154722?s=64",
        url="https://github.com/microsoft/playwright-mcp",
        installs=9764,
        display_name="Playwright Browser",
        description="Drive a real browser: navigate, click, fill, screenshot.",
        tags=["browser", "automation"],
        version="1.0.0",
        # STDIO servers must be stateful — the process is the session.
        is_stateful=True,
        auth="none",
        config_template={
            "type": "stdio_mcp",
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        },
    ),
]


class StaticMCPHub(MCPHubBase):
    """An MCP hub backed by an in-memory catalog.

    Shows how an offset-based source satisfies the cursor-paginated
    interface: the cursor is just the next offset, rendered as a string.
    Real registries hand back an opaque token instead, which is why the
    frontend must never try to interpret it.
    """

    def __init__(
        self,
        hub_id: str = HUB_ID,
        display_name: str = "Example MCPs",
        description: str = "A small hand-written catalog for demos.",
    ) -> None:
        """Initialize the hub identity.

        Args:
            hub_id (`str`, defaults to `"examples"`):
                The stable identifier addressing this hub in the routes.
            display_name (`str`, defaults to `"Example MCPs"`):
                The user-facing hub name.
            description (`str`, optional):
                The user-facing hub description.
        """
        super().__init__(hub_id, display_name, description)
        self._cards = {card.id: card for card in CARDS}

    async def list_mcps(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> MCPHubPage:
        """Return one page of the catalog, filtered by ``q``.

        Args:
            user_id (`str`):
                The user identifier to query MCP cards for. A real hub
                would filter the catalog by it; this one shows everything.
            q (`str | None`, optional):
                A keyword matched against the name and description.
            cursor (`str | None`, optional):
                The offset returned by the previous page.
            limit (`int`, defaults to `20`):
                The maximum number of cards per page.

        Returns:
            `MCPHubPage`:
                The requested page of cards plus the next cursor.
        """
        matched = [
            card
            for card in self._cards.values()
            if not q
            or q.lower() in card.name.lower()
            or q.lower() in card.description.lower()
        ]

        offset = int(cursor) if cursor and cursor.isdigit() else 0
        page = matched[offset : offset + limit]
        end = offset + len(page)

        return MCPHubPage(
            cards=page,
            next_cursor=str(end) if end < len(matched) else None,
        )

    async def get_mcp(self, user_id: str, card_id: str) -> MCPCard:
        """Return one card by id.

        Args:
            user_id (`str`):
                The user identifier to query the card for. Unused here.
            card_id (`str`):
                The card id, which for this hub equals the card name.

        Returns:
            `MCPCard`:
                The matching card.

        Raises:
            `KeyError`:
                When no card is registered under ``card_id``.
        """
        return self._cards[card_id]
