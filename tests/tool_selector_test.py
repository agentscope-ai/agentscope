# -*- coding: utf-8 -*-
"""Tests for task-aware tool selection without external model calls."""
import asyncio
import json
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.credential import CredentialBase, OpenAICredential
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.tool import EmbeddingToolSelector, ToolChoice


def _schema(name: str, cost: int = 1) -> dict:
    """Build a schema with a deterministic test token cost."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
        "cost": cost,
    }


async def _count(tools: list[dict]) -> int:
    """Count the complete candidate set supplied by the selector."""
    return sum(tool["cost"] for tool in tools)


class _Embedding(EmbeddingModelBase[str]):
    """An embedding model that embeds names with predetermined vectors."""

    def __init__(self) -> None:
        """Initialize a text-only local model."""
        super().__init__(
            credential=CredentialBase(),
            model="test-embedding",
            dimensions=2,
            parameters=None,
            context_size=8192,
            batch_size=100,
            max_retries=0,
            retry_delay=0,
        )
        self.calls: list[list[str]] = []
        self.error: BaseException | None = None
        self.vectors = {
            "query": [1.0, 0.0],
            "low": [0.0, 1.0],
            "middle": [1.0, 1.0],
            "high": [1.0, 0.0],
            "pin": [-1.0, 0.0],
        }

    async def _call_api(self, inputs: list[str]) -> EmbeddingResponse:
        """Return controlled vectors or a configured retrieval failure."""
        self.calls.append(list(inputs))
        if self.error is not None:
            raise self.error
        vectors = []
        for text in inputs:
            name = (
                json.loads(text)["function"]["name"]
                if text.startswith("{")
                else "query"
            )
            vectors.append(self.vectors.get(name, [1.0, 0.0]))
        return EmbeddingResponse(embeddings=vectors)


class ToolSelectorTest(IsolatedAsyncioTestCase):
    """Verify selection, accounting, errors and bounded cache behavior."""

    def setUp(self) -> None:
        """Create deterministic candidates and a fresh embedding model."""
        self.model = _Embedding()
        self.tools = [_schema("low"), _schema("middle"), _schema("high")]

    async def test_ranking_preserves_candidate_order_and_inputs(self) -> None:
        """Selection ranks membership but returns original schema order."""
        before = deepcopy(self.tools)
        selector = EmbeddingToolSelector(self.model, top_k=2)
        result = await selector.select(
            "query",
            self.tools,
            count_tokens=_count,
        )
        self.assertEqual(result.tools, self.tools[1:])
        self.assertEqual(result.schema_tokens, 2)
        self.assertFalse(result.used_fallback)
        self.assertEqual(self.tools, before)

    async def test_required_and_forced_tools_do_not_consume_top_k(
        self,
    ) -> None:
        """Pins and explicit ToolChoice names survive semantic filtering."""
        tools = [_schema("pin")] + self.tools
        selector = EmbeddingToolSelector(self.model, top_k=1)
        result = await selector.select(
            "query",
            tools,
            count_tokens=_count,
            required_tools=["pin"],
            tool_choice=ToolChoice(mode="low", tools=["low", "middle"]),
        )
        self.assertEqual(result.tools, tools)

    async def test_budget_skips_large_tool_and_counts_whole_set(self) -> None:
        """A smaller candidate can fit after the highest score is skipped."""
        tools = [_schema("low", 2), _schema("middle", 2), _schema("high", 9)]
        counter = AsyncMock(side_effect=_count)
        result = await EmbeddingToolSelector(self.model, top_k=2).select(
            "query",
            tools,
            count_tokens=counter,
            max_tokens=4,
        )
        self.assertEqual(result.tools, tools[:2])
        self.assertEqual(result.schema_tokens, 4)
        self.assertIn(
            [tools[0], tools[1]],
            [c.args[0] for c in counter.call_args_list],
        )

    async def test_empty_inputs_queries_and_zero_limits(self) -> None:
        """Empty queries avoid retrieval but still observe count and budget."""
        for query in (None, "", "   "):
            with self.subTest(query=query):
                result = await EmbeddingToolSelector(
                    self.model,
                    top_k=2,
                ).select(
                    query,
                    self.tools,
                    count_tokens=_count,
                    max_tokens=1,
                )
                self.assertEqual(result.tools, self.tools[:1])
                self.assertFalse(result.used_fallback)
        result = await EmbeddingToolSelector(self.model).select(
            "query",
            [],
            count_tokens=_count,
        )
        self.assertEqual(result.tools, [])
        result = await EmbeddingToolSelector(self.model, top_k=0).select(
            "query",
            self.tools,
            count_tokens=_count,
            required_tools=["low"],
        )
        self.assertEqual(result.tools, self.tools[:1])
        result = await EmbeddingToolSelector(self.model).select(
            None,
            self.tools,
            count_tokens=_count,
            max_tokens=0,
        )
        self.assertEqual(result.tools, [])
        self.assertEqual(self.model.calls, [])

    async def test_fallback_restores_all_and_reports_budget(self) -> None:
        """Only retrieval failures permit all-tools budget overflow."""
        self.model.error = RuntimeError("embedding unavailable")
        result = await EmbeddingToolSelector(self.model, top_k=1).select(
            "query",
            self.tools,
            count_tokens=_count,
            max_tokens=1,
        )
        self.assertEqual(result.tools, self.tools)
        self.assertTrue(result.used_fallback)
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.schema_tokens, 3)
        self.assertIn("embedding unavailable", result.fallback_reason)

    async def test_raise_policy_and_cancellation(self) -> None:
        """Explicit raise policy and cancellation never become fallback."""
        self.model.error = RuntimeError("offline")
        selector = EmbeddingToolSelector(self.model, failure_policy="raise")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await selector.select("query", self.tools, count_tokens=_count)
        self.model.error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await EmbeddingToolSelector(self.model).select(
                "query",
                self.tools,
                count_tokens=_count,
            )

    async def test_invalid_vectors_are_retrieval_failures(self) -> None:
        """Zero, nonfinite and mismatched vectors follow failure policy."""
        for vector in ([0.0, 0.0], [float("nan"), 1.0], [1.0], []):
            with self.subTest(vector=vector):
                self.model.vectors["query"] = vector
                result = await EmbeddingToolSelector(self.model).select(
                    "query",
                    self.tools,
                    count_tokens=_count,
                )
                self.assertTrue(result.used_fallback)

    async def test_required_budget_and_invalid_constraints_raise(self) -> None:
        """Input errors are never hidden by the retrieval fallback policy."""
        selector = EmbeddingToolSelector(self.model)
        for kwargs in (
            {"required_tools": ["missing"]},
            {"required_tools": ["low"], "max_tokens": 0},
            {"required_tools": "low"},
            {"max_tokens": -1},
            {"tool_choice": ToolChoice(mode="high", tools=["low"])},
            {"tool_choice": ToolChoice(mode="auto", tools=["missing"])},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await selector.select(
                    "query",
                    self.tools,
                    count_tokens=_count,
                    **kwargs,
                )
        self.assertEqual(self.model.calls, [])
        with self.assertRaises(ValueError):
            await selector.select(
                None,
                [],
                count_tokens=_count,
                tool_choice=ToolChoice(mode="required"),
            )

    async def test_counter_failure_and_bad_configuration_propagate(
        self,
    ) -> None:
        """Counter failures are outside the embedding error boundary."""
        with self.assertRaisesRegex(RuntimeError, "counter failed"):
            await EmbeddingToolSelector(self.model).select(
                "query",
                self.tools,
                count_tokens=AsyncMock(
                    side_effect=RuntimeError("counter failed"),
                ),
            )
        for kwargs in (
            {"top_k": -1},
            {"top_k": True},
            {"cache_size": -1},
            {"failure_policy": "other"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                EmbeddingToolSelector(self.model, **kwargs)
        self.assertEqual(self.model.calls, [])

    async def test_cache_reuses_vectors_and_invalidates_schema_and_model(
        self,
    ) -> None:
        """Queries are fresh while schema and model changes invalidate keys."""
        selector = EmbeddingToolSelector(self.model, top_k=1)
        await selector.select("query", self.tools, count_tokens=_count)
        await selector.select("query", self.tools, count_tokens=_count)
        self.assertEqual(len(self.model.calls[0]), 4)
        self.assertEqual(len(self.model.calls[1]), 1)
        changed = deepcopy(self.tools)
        changed[0]["function"]["parameters"]["properties"]["new"] = {
            "type": "string",
        }
        await selector.select("query", changed, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 2)
        self.model.model = "changed-model"
        await selector.select("query", changed, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 4)
        selector.clear_cache()
        await selector.select("query", changed, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 4)

    async def test_cache_bound_and_dynamic_membership(self) -> None:
        """Removed candidates never reappear through cache or fallback."""
        selector = EmbeddingToolSelector(self.model, top_k=1, cache_size=1)
        await selector.select("query", self.tools, count_tokens=_count)
        result = await selector.select(
            "query",
            self.tools[:1],
            count_tokens=_count,
        )
        self.assertEqual(result.tools, self.tools[:1])
        self.assertEqual(len(self.model.calls[-1]), 2)
        self.model.error = RuntimeError("offline")
        result = await selector.select(
            "query",
            self.tools[1:2],
            count_tokens=_count,
        )
        self.assertEqual(result.tools, self.tools[1:2])
        self.assertTrue(result.used_fallback)

    async def test_disabled_cache_and_equal_score_order(self) -> None:
        """Disabling cache re-embeds and tied scores preserve input order."""
        self.model.vectors["low"] = self.model.vectors["high"]
        selector = EmbeddingToolSelector(self.model, top_k=1, cache_size=0)
        for _ in range(2):
            result = await selector.select(
                "query",
                self.tools,
                count_tokens=_count,
            )
            self.assertEqual(result.tools, self.tools[:1])
            self.assertEqual(len(self.model.calls[-1]), 4)

    async def test_invalid_schema_and_token_counts_are_not_retrieval(
        self,
    ) -> None:
        """Invalid inputs cannot trigger the permissive retrieval fallback."""
        selector = EmbeddingToolSelector(self.model)
        for tools in ([{}], [self.tools[0], self.tools[0]]):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                await selector.select("query", tools, count_tokens=_count)
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await selector.select(
                    "query",
                    self.tools,
                    count_tokens=AsyncMock(return_value=value),
                )
        self.assertEqual(self.model.calls, [])

    async def test_fallback_token_counter_error_propagates(self) -> None:
        """A failed counter during fallback cannot report a fabricated cost."""
        self.model.error = RuntimeError("embedding offline")
        with self.assertRaisesRegex(RuntimeError, "count unavailable"):
            await EmbeddingToolSelector(self.model).select(
                "query",
                self.tools,
                count_tokens=AsyncMock(
                    side_effect=[0, RuntimeError("count unavailable")],
                ),
            )

    async def test_missing_vectors_and_large_query(self) -> None:
        """Invalid batch lengths use fallback, including long-query errors."""
        with patch.object(
            self.model,
            "_call_api",
            AsyncMock(return_value=EmbeddingResponse(embeddings=[])),
        ):
            result = await EmbeddingToolSelector(self.model).select(
                "query" * 10000,
                self.tools,
                count_tokens=_count,
            )
        self.assertTrue(result.used_fallback)
        self.assertIn("unexpected length", result.fallback_reason)

    async def test_endpoint_and_credential_replacement_invalidate_cache(
        self,
    ) -> None:
        """Different endpoints with the same model name use fresh vectors."""
        self.model.credential = OpenAICredential(
            api_key="unused-test-key",
            base_url="https://first.invalid/v1",
        )
        selector = EmbeddingToolSelector(self.model)
        await selector.select("query", self.tools, count_tokens=_count)
        await selector.select("query", self.tools, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 1)
        self.model.credential.base_url = "https://second.invalid/v1"
        await selector.select("query", self.tools, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 4)
        self.model.credential = self.model.credential.model_copy()
        await selector.select("query", self.tools, count_tokens=_count)
        self.assertEqual(len(self.model.calls[-1]), 4)
