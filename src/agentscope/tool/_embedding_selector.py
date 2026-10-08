# -*- coding: utf-8 -*-
"""Embedding-based tool selection with bounded schema caching."""
from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from typing import Awaitable, Callable, Literal, Sequence, TYPE_CHECKING

from ._selector import ToolSelection, ToolSelectorBase
from ._types import ToolChoice

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase


class EmbeddingToolSelector(ToolSelectorBase):
    """Rank current tool candidates with a bounded in-memory embedding cache.

    Only schema embeddings are cached, keyed by schema contents and the
    embedding configuration. Candidate membership is refreshed on each call.
    Ranking ties and returned schema order follow candidate order. Only the
    supplied candidates can be selected, including after a retrieval failure.
    """

    def __init__(
        self,
        embedding_model: EmbeddingModelBase[str],
        top_k: int = 10,
        *,
        failure_policy: Literal["all", "raise"] = "all",
        cache_size: int = 1024,
    ) -> None:
        """Initialize the embedding selector.

        Args:
            embedding_model (`EmbeddingModelBase[str]`):
                Existing text embedding model; no external index is needed.
            top_k (`int`):
                Maximum number of retrieved tools in addition to required
                tools. Must be nonnegative; required tools are never dropped
                to satisfy this count.
            failure_policy (`Literal["all", "raise"]`):
                On an embedding failure or invalid vectors, all restores
                the complete current candidate snapshot, even above budget,
                and reports used_fallback, fallback_reason and
                budget_exceeded. Raise propagates the retrieval error.
                Neither option hides invalid input, token counter errors
                or cancellation. An empty query is not a retrieval error.
            cache_size (`int`):
                Nonnegative maximum cached schema embeddings. Zero disables
                caching.
        """
        self._validate_limit("top_k", top_k)
        self._validate_limit("cache_size", cache_size)
        if failure_policy not in ("all", "raise"):
            raise ValueError("failure_policy must be 'all' or 'raise'.")
        if not callable(embedding_model):
            raise TypeError("embedding_model must be callable.")
        self.embedding_model = embedding_model
        self.top_k = top_k
        self.failure_policy = failure_policy
        self.cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()

    async def select(
        self,
        query: str | None,
        tools: list[dict],
        *,
        count_tokens: Callable[[list[dict]], Awaitable[int]],
        max_tokens: int | None = None,
        required_tools: Sequence[str] = (),
        tool_choice: ToolChoice | None = None,
    ) -> ToolSelection:
        """Apply the ToolSelectorBase contract using cosine similarity.

        Required tools alone exceeding the budget are a configuration error,
        not a retrieval failure eligible for the all-tools fallback.
        """
        if query is not None and not isinstance(query, str):
            raise TypeError("query must be a string or None.")
        if max_tokens is not None:
            self._validate_limit("max_tokens", max_tokens)
        names, texts = self._describe_tools(tools)
        required = self._required_names(names, required_tools, tool_choice)

        async def count(schemas: list[dict]) -> int:
            """Validate costs outside the retrieval failure handler."""
            value = await count_tokens(schemas)
            self._validate_limit("token count", value)
            return value

        selected = {i for i, name in enumerate(names) if name in required}
        selected_tools = [tools[i] for i in sorted(selected)]
        tokens = await count(selected_tools)
        if max_tokens is not None and tokens > max_tokens:
            raise ValueError("Required tool schemas exceed max_tokens.")

        optional = [i for i in range(len(tools)) if i not in selected]
        if optional and self.top_k and query and query.strip():
            # Invalid schemas/configuration are caller errors, not retrieval
            # failures. Construct cache keys before entering the handler.
            keys = self._cache_keys([texts[i] for i in optional])
            try:
                scores = await self._similarities(
                    query,
                    [texts[i] for i in optional],
                    keys,
                )
            except Exception as error:
                if self.failure_policy == "raise":
                    raise
                tokens = await count(tools)
                return ToolSelection(
                    tools=list(tools),
                    schema_tokens=tokens,
                    used_fallback=True,
                    fallback_reason=f"{type(error).__name__}: {error}",
                    budget_exceeded=(
                        max_tokens is not None and tokens > max_tokens
                    ),
                )
            score_by_index = dict(zip(optional, scores))
            optional.sort(key=lambda i: -score_by_index[i])

        added = 0
        for index in optional:
            if added >= self.top_k:
                break
            candidate = selected | {index}
            candidate_tools = [tools[i] for i in sorted(candidate)]
            candidate_tokens = await count(candidate_tools)
            if max_tokens is None or candidate_tokens <= max_tokens:
                selected = candidate
                selected_tools = candidate_tools
                tokens = candidate_tokens
                added += 1

        if (
            tool_choice is not None
            and tool_choice.mode == "required"
            and not selected_tools
        ):
            raise ValueError("tool_choice='required' needs an available tool.")
        return ToolSelection(tools=selected_tools, schema_tokens=tokens)

    def clear_cache(self) -> None:
        """Discard cached schema embeddings without changing the toolkit."""
        self._cache.clear()

    @staticmethod
    def _validate_limit(name: str, value: int) -> None:
        """Require nonnegative integer counts, excluding booleans."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer.")

    @staticmethod
    def _describe_tools(tools: list[dict]) -> tuple[list[str], list[str]]:
        """Validate unique function names and serialize complete schemas."""
        names = []
        texts = []
        for schema in tools:
            if (
                not isinstance(schema, dict)
                or schema.get("type") != "function"
                or not isinstance(schema.get("function"), dict)
            ):
                raise ValueError("Each candidate must be a function schema.")
            name = schema["function"].get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError("Tool names must be unique nonempty strings.")
            names.append(name)
            texts.append(
                json.dumps(
                    schema,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            )
        return names, texts

    @staticmethod
    def _required_names(
        names: list[str],
        required_tools: Sequence[str],
        tool_choice: ToolChoice | None,
    ) -> set[str]:
        """Preserve explicit model constraints as well as caller pins."""
        if isinstance(required_tools, (str, bytes)) or any(
            not isinstance(name, str) for name in required_tools
        ):
            raise ValueError("required_tools must be a sequence of names.")
        required = set(required_tools)
        if tool_choice is not None:
            if not isinstance(tool_choice, ToolChoice):
                raise TypeError("tool_choice must be a ToolChoice or None.")
            required.update(tool_choice.tools or [])
            if tool_choice.mode not in ("auto", "none", "required"):
                if tool_choice.tools and (
                    tool_choice.mode not in tool_choice.tools
                ):
                    raise ValueError("Forced tool is absent from tools list.")
                required.add(tool_choice.mode)
        missing = required.difference(names)
        if missing:
            raise ValueError(
                f"Required tools are unavailable: {sorted(missing)}",
            )
        return required

    def _cache_keys(self, texts: list[str]) -> list[str]:
        """Invalidate embeddings when schemas or model configuration change."""
        model = self.embedding_model
        config = json.dumps(
            {
                "instance": id(model),
                "model": model.model,
                "dimensions": model.dimensions,
                "parameters": model.parameters.model_dump(mode="json"),
                # Endpoint/account routing can change the vector space even
                # when the model name is unchanged. Pydantic's JSON dump
                # masks SecretStr values; never unwrap or retain credentials.
                "credential_instance": id(model.credential),
                "credential_config": model.credential.model_dump(mode="json"),
            },
            sort_keys=True,
            allow_nan=False,
        )
        return [
            hashlib.sha256((config + text).encode("utf-8")).hexdigest()
            for text in texts
        ]

    async def _similarities(
        self,
        query: str,
        texts: list[str],
        keys: list[str],
    ) -> list[float]:
        """Embed the query and uncached schemas, returning cosine scores."""
        vectors: dict[int, tuple[float, ...]] = {}
        missing = []
        for index, key in enumerate(keys):
            if key in self._cache:
                vectors[index] = self._cache[key]
                self._cache.move_to_end(key)
            else:
                missing.append(index)
        response = await self.embedding_model(
            [query] + [texts[i] for i in missing],
        )
        if len(response.embeddings) != len(missing) + 1:
            raise ValueError("Embedding response has an unexpected length.")
        normalized = [self._normalize(v) for v in response.embeddings]
        query_vector = normalized[0]
        dimensions = self.embedding_model.dimensions
        if any(len(v) != dimensions for v in normalized):
            raise ValueError("Embedding dimensions do not match the model.")
        for index, vector in zip(missing, normalized[1:]):
            vectors[index] = vector
            if self.cache_size:
                self._cache[keys[index]] = vector
                self._cache.move_to_end(keys[index])
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
        return [
            math.fsum(a * b for a, b in zip(query_vector, vectors[i]))
            for i in range(len(texts))
        ]

    @staticmethod
    def _normalize(vector: list[float]) -> tuple[float, ...]:
        """Normalize a finite nonzero vector without overflowing its norm."""
        values = tuple(float(value) for value in vector)
        if not values or not all(math.isfinite(value) for value in values):
            raise ValueError("Embeddings must contain finite numeric values.")
        scale = max(abs(value) for value in values)
        if not scale:
            raise ValueError("Embeddings must have nonzero norm.")
        scaled = tuple(value / scale for value in values)
        norm = math.hypot(*scaled)
        return tuple(value / norm for value in scaled)
