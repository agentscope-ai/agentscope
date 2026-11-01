# -*- coding: utf-8 -*-
"""Mem0 retrieval compatibility without a model or vector-store service."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from typing import Any

from agentscope.memory import Mem0LongTermMemory
from agentscope.message import Msg


class Mem0SearchTest(IsolatedAsyncioTestCase):
    """Exercise both public retrieval paths at the SDK boundary."""

    def memory(self, **identifiers: str) -> Mem0LongTermMemory:
        """Create the adapter without initializing external providers."""
        memory = object.__new__(Mem0LongTermMemory)
        memory.user_id = identifiers.get("user_id")
        memory.agent_id = identifiers.get("agent_id")
        memory.run_id = identifiers.get("run_id")
        memory.long_term_working_memory = AsyncMock()
        return memory

    async def test_modern_search_preserves_all_scopes_and_limit(self) -> None:
        """Modern SDKs reject entity kwargs and use top_k."""
        memory = self.memory(user_id="u", agent_id="a", run_id="r")

        async def search(query: str, *, filters: dict, top_k: int) -> dict:
            """Mirror the modern search contract with no legacy kwargs."""
            self.assertEqual(
                filters,
                {"user_id": "u", "agent_id": "a", "run_id": "r"},
            )
            self.assertEqual(top_k, 3)
            return {"results": [{"memory": query}]}

        memory.long_term_working_memory.search.side_effect = search
        with patch("importlib.metadata.version", return_value="2.0.0"):
            result = await memory.retrieve(Msg("user", "tea", "user"), limit=3)
            self.assertIn("tea", result)
            tool_result = await memory.retrieve_from_memory(
                ["tea", "coffee"],
                limit=3,
            )
        self.assertEqual(tool_result.content[0]["text"], "tea\ncoffee")
        self.assertEqual(memory.long_term_working_memory.search.await_count, 3)

    async def test_modern_search_omits_unconfigured_identifiers(self) -> None:
        """Each individual scope remains effective without None filters."""
        for key in ("user_id", "agent_id", "run_id"):
            with self.subTest(key=key):
                memory = self.memory(**{key: "only"})
                memory.long_term_working_memory.search.return_value = {
                    "results": [],
                }
                with patch("importlib.metadata.version", return_value="2.0.1"):
                    await memory.retrieve_from_memory(["query"], limit=7)
                search = memory.long_term_working_memory.search
                search.assert_awaited_once_with(
                    query="query",
                    filters={key: "only"},
                    top_k=7,
                )

    async def test_legacy_search_keeps_existing_argument_contract(
        self,
    ) -> None:
        """Old SDKs retain top-level entities and limit in both paths."""
        for version in ("0.1.115", "1.0.3"):
            with self.subTest(version=version):
                memory = self.memory(user_id="u", agent_id="a", run_id="r")

                async def search(
                    query: str,
                    *,
                    user_id: str,
                    agent_id: str,
                    run_id: str,
                    limit: int,
                ) -> dict[str, Any]:
                    """Reject the new SDK argument names."""
                    self.assertEqual(
                        (user_id, agent_id, run_id, limit),
                        ("u", "a", "r", 4),
                    )
                    return {"results": [{"memory": query}]}

                memory.long_term_working_memory.search.side_effect = search
                with patch("importlib.metadata.version", return_value=version):
                    await memory.retrieve(
                        Msg("user", "query", "user"),
                        limit=4,
                    )
                    result = await memory.retrieve_from_memory(
                        ["query"],
                        limit=4,
                    )
                self.assertEqual(result.content[0]["text"], "query")

    async def test_backend_errors_are_not_retried_with_broader_scope(
        self,
    ) -> None:
        """Keep errors visible without retrying an unscoped query."""
        memory = self.memory(user_id="u")
        memory.long_term_working_memory.search.side_effect = ValueError(
            "backend rejected request",
        )
        with patch("importlib.metadata.version", return_value="2.0.0"):
            with self.assertRaisesRegex(ValueError, "backend rejected"):
                await memory.retrieve(Msg("user", "query", "user"))
            response = await memory.retrieve_from_memory(["query"])
        self.assertIn("backend rejected", response.content[0]["text"])
        self.assertEqual(memory.long_term_working_memory.search.await_count, 2)
