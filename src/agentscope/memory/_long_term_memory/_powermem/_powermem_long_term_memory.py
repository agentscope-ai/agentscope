# -*- coding: utf-8 -*-
"""Long-term memory implementation using powermem library."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, TYPE_CHECKING

from .._long_term_memory_base import LongTermMemoryBase
from ....message import Msg, TextBlock
from ....tool import ToolResponse


if TYPE_CHECKING:
    from powermem import AsyncMemory, Memory
    from powermem.configs import MemoryConfig
else:
    AsyncMemory = Any
    Memory = Any
    MemoryConfig = Any


class PowerMemLongTermMemory(LongTermMemoryBase):
    """A long-term memory implementation backed by powermem."""

    def __init__(
        self,
        config: dict | MemoryConfig | None = None,
        memory: AsyncMemory | Memory | None = None,
        *,
        agent_name: str | None = None,
        user_name: str | None = None,
        run_name: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        infer: bool = True,
        auto_config: bool = True,
    ) -> None:
        """Initialize a PowerMemLongTermMemory instance.

        Args:
            config (`dict | MemoryConfig | None`, optional):
                The powermem configuration object or dict. If None and
                auto_config is True, powermem.auto_config() will be used.
            memory (`AsyncMemory | Memory | None`, optional):
                A prebuilt powermem memory instance. If provided, config is
                ignored.
            agent_name (`str | None`, optional):
                The agent identifier for multi-agent isolation.
            user_name (`str | None`, optional):
                The user identifier for per-user memory scoping.
            run_name (`str | None`, optional):
                The run/session identifier for per-session memory scoping.
            agent_id (`str | None`, optional):
                Alias for agent_name.
            user_id (`str | None`, optional):
                Alias for user_name.
            run_id (`str | None`, optional):
                Alias for run_name.
            infer (`bool`, optional):
                Whether to enable intelligent inference when recording.
            auto_config (`bool`, optional):
                Whether to auto-load configuration from environment when
                config is not provided.
        """
        super().__init__()

        try:
            import powermem
        except ImportError as e:
            raise ImportError(
                "Please install powermem by `pip install powermem`.",
            ) from e

        # Resolve legacy aliases for backward compatibility
        resolved_agent_id = self._resolve_identifier(
            "agent",
            agent_name,
            agent_id,
        )
        resolved_user_id = self._resolve_identifier("user", user_name, user_id)
        resolved_run_id = self._resolve_identifier("run", run_name, run_id)

        if memory is None:
            if config is None and auto_config:
                config = powermem.auto_config()
            memory = powermem.AsyncMemory(
                config=config,
                agent_id=resolved_agent_id,
            )

        self._memory = memory
        self._agent_id = resolved_agent_id
        self._user_id = resolved_user_id
        self._run_id = resolved_run_id
        self._infer = infer
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        **kwargs: Any,
    ) -> ToolResponse:
        """Record important information to long-term memory.

        Args:
            thinking (`str`):
                Your reasoning about what to record.
            content (`list[str]`):
                The content to remember.
            **kwargs (`Any`):
                Extra keyword arguments passed to the memory backend.

        Returns:
            `ToolResponse`:
                A response object describing the result.
        """
        try:
            await self._ensure_initialized()
            payload = self._join_record_content(thinking, content)
            infer = kwargs.pop("infer", self._infer)
            await self._add_messages(payload, infer=infer, **kwargs)
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Successfully recorded content to memory.",
                    ),
                ],
            )
        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error recording memory: {str(e)}",
                    ),
                ],
            )

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        limit: int = 5,
        **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve memories based on the given keywords.

        Args:
            keywords (`list[str]`):
                Search terms for memory retrieval.
            limit (`int`, optional):
                Maximum number of results per keyword.
            **kwargs (`Any`):
                Extra keyword arguments passed to the memory backend.

        Returns:
            `ToolResponse`:
                A response object describing the retrieved memories.
        """
        try:
            await self._ensure_initialized()
            results = await self._search_keywords(keywords, limit, **kwargs)
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="\n".join(results),
                    ),
                ],
            )
        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error retrieving memory: {str(e)}",
                    ),
                ],
            )

    async def record(
        self,
        msgs: list[Msg | None],
        memory_type: str | None = None,
        infer: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Record messages to long-term memory.

        Args:
            msgs (`list[Msg | None]`):
                Messages to record.
            memory_type (`str | None`, optional):
                Memory type passed to the backend.
            infer (`bool | None`, optional):
                Override inference behavior.
            **kwargs (`Any`):
                Extra keyword arguments passed to the backend.
        """
        if isinstance(msgs, Msg):
            msgs = [msgs]

        msg_list = [_ for _ in msgs if _]
        if not msg_list:
            return

        if not all(isinstance(_, Msg) for _ in msg_list):
            raise TypeError(
                "The input messages must be a list of Msg objects.",
            )

        await self._ensure_initialized()
        messages = self._to_powermem_messages(msg_list)
        await self._call_memory(
            self._memory.add,
            messages=messages,
            user_id=self._user_id,
            agent_id=self._agent_id,
            run_id=self._run_id,
            memory_type=memory_type,
            infer=self._infer if infer is None else infer,
            **kwargs,
        )

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """Retrieve related memories based on the given messages.

        Args:
            msg (`Msg | list[Msg] | None`):
                Input message(s) to build search queries.
            limit (`int`, optional):
                Maximum number of results per query.
            **kwargs (`Any`):
                Extra keyword arguments passed to the backend.

        Returns:
            `str`:
                Concatenated memory results.
        """
        if msg is None:
            return ""

        if isinstance(msg, Msg):
            msg = [msg]

        if not isinstance(msg, list) or not all(
            isinstance(_, Msg) for _ in msg
        ):
            raise TypeError(
                "The input message must be a Msg or a list of Msg objects.",
            )

        queries = [self._message_to_query(item) for item in msg]
        queries = [query for query in queries if query]
        if not queries:
            return ""

        await self._ensure_initialized()
        results = await self._search_queries(queries, limit, **kwargs)
        return "\n".join(results)

    async def _ensure_initialized(self) -> None:
        """Ensure the backend is initialized before first use.

        Returns:
            `None`:
                This method returns nothing.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            initialize = getattr(self._memory, "initialize", None)
            if initialize is not None:
                result = initialize()
                if inspect.isawaitable(result):
                    await result
            self._initialized = True

    async def _add_messages(self, payload: str, **kwargs: Any) -> None:
        """Add messages to the memory backend.

        Args:
            payload (`str`):
                Serialized message payload to store.
            **kwargs (`Any`):
                Extra keyword arguments passed to the backend.

        Returns:
            `None`:
                This method returns nothing.
        """
        await self._call_memory(
            self._memory.add,
            messages=payload,
            user_id=self._user_id,
            agent_id=self._agent_id,
            run_id=self._run_id,
            **kwargs,
        )

    async def _search_queries(
        self,
        queries: list[str],
        limit: int,
        **kwargs: Any,
    ) -> list[str]:
        """Search the backend with a list of queries.

        Args:
            queries (`list[str]`):
                Search queries.
            limit (`int`):
                Maximum number of results per query.
            **kwargs (`Any`):
                Extra keyword arguments passed to the backend.

        Returns:
            `list[str]`:
                Flattened memory results.
        """
        results: list[str] = []
        for query in queries:
            response = await self._call_memory(
                self._memory.search,
                query=query,
                user_id=self._user_id,
                agent_id=self._agent_id,
                run_id=self._run_id,
                limit=limit,
                **kwargs,
            )
            results.extend(self._extract_search_results(response))
        return results

    async def _search_keywords(
        self,
        keywords: list[str],
        limit: int,
        **kwargs: Any,
    ) -> list[str]:
        """Search the backend using keyword queries.

        Args:
            keywords (`list[str]`):
                Search keywords.
            limit (`int`):
                Maximum number of results per keyword.
            **kwargs (`Any`):
                Extra keyword arguments passed to the backend.

        Returns:
            `list[str]`:
                Flattened memory results.
        """
        keywords = [keyword for keyword in keywords if keyword]
        if not keywords:
            return []
        return await self._search_queries(keywords, limit, **kwargs)

    async def _call_memory(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke a backend method in async or sync mode.

        Args:
            func (`Any`):
                Callable memory backend method.
            *args (`Any`):
                Positional arguments for the callable.
            **kwargs (`Any`):
                Keyword arguments for the callable.

        Returns:
            `Any`:
                Backend result or awaited coroutine result.
        """
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        result = await asyncio.to_thread(func, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _join_record_content(thinking: str, content: list[str]) -> str:
        """Join thinking and content entries into a payload string.

        Args:
            thinking (`str`):
                Reasoning text.
            content (`list[str]`):
                Content items.

        Returns:
            `str`:
                Joined payload string.
        """
        parts = [item for item in [thinking, *content] if item]
        return "\n".join(parts)

    @staticmethod
    def _message_to_query(msg: Msg) -> str:
        """Convert a message into a search query string.

        Args:
            msg (`Msg`):
                Input message.

        Returns:
            `str`:
                Extracted query string.
        """
        text = msg.get_text_content()
        if text is not None:
            return text
        return str(msg.content)

    @staticmethod
    def _to_powermem_messages(msgs: list[Msg]) -> list[dict[str, str]]:
        """Convert AgentScope messages to powermem message format.

        Args:
            msgs (`list[Msg]`):
                Input messages.

        Returns:
            `list[dict[str, str]]`:
                Converted message payloads.
        """
        messages = []
        for msg in msgs:
            text = msg.get_text_content()
            messages.append(
                {
                    "role": msg.role,
                    "content": text if text is not None else str(msg.content),
                },
            )
        return messages

    @staticmethod
    def _extract_search_results(response: Any) -> list[str]:
        """Extract memory strings from a backend search response.

        Args:
            response (`Any`):
                Backend search response.

        Returns:
            `list[str]`:
                Memory strings and formatted relations.
        """
        if not isinstance(response, dict):
            return []

        results: list[str] = []
        for item in response.get("results", []) or []:
            memory = item.get("memory") or item.get("content")
            if memory:
                results.append(memory)

        relations = response.get("relations")
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                source = relation.get("source")
                relationship = relation.get("relationship")
                destination = relation.get("destination")
                if source and relationship and destination:
                    results.append(
                        f"{source} -- {relationship} -- {destination}",
                    )
        return results

    @staticmethod
    def _resolve_identifier(
        kind: str,
        primary: str | None,
        legacy: str | None,
    ) -> str | None:
        """Resolve primary and legacy identifiers with conflict checks.

        Args:
            kind (`str`):
                Identifier kind label for error messages.
            primary (`str | None`):
                Primary identifier value.
            legacy (`str | None`):
                Legacy identifier value.

        Returns:
            `str | None`:
                Resolved identifier.

        Raises:
            `ValueError`:
                When both identifiers are set and conflict.
        """
        if primary is not None and legacy is not None and primary != legacy:
            raise ValueError(
                f"Conflicting {kind} identifiers: {primary} vs {legacy}",
            )
        return primary if primary is not None else legacy
