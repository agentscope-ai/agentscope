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
        default_memory_type: str | None = None,
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
            default_memory_type (`str | None`, optional):
                Default memory type passed to the backend when the caller
                does not provide ``memory_type``.
            infer (`bool`, optional):
                Whether to enable intelligent inference when recording.
            auto_config (`bool`, optional):
                Whether to auto-load configuration from environment when
                config is not provided.

        .. note::
            Parameter precedence is explicit:
            1) If ``memory`` is provided, ``config`` and ``auto_config`` are
               ignored.
            2) Otherwise, ``config`` is used as-is, or ``powermem.auto_config()``
               is used when ``config`` is None and ``auto_config`` is True.

        .. note::
            Scope identifiers (``agent_id``, ``user_id``, ``run_id``) are
            always passed to backend calls when supported by the backend
            signature. This keeps the scoping path consistent across record
            and retrieve operations.

        .. note::
            ``memory_type`` precedence is explicit:
            constructor ``default_memory_type`` < kwargs ``memory_type`` <
            method argument ``memory_type``.
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

        # When a prebuilt memory is provided, prefer its agent_id if the
        # caller does not override it. This keeps the scoping path consistent.
        if memory is not None and resolved_agent_id is None:
            resolved_agent_id = getattr(memory, "agent_id", None)

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
        self._default_memory_type = default_memory_type
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
            return self._tool_response(
                "Successfully recorded content to memory.",
            )
        except Exception as e:
            return self._tool_error_response("Error recording memory", e)

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
            return self._tool_response("\n".join(results))
        except Exception as e:
            return self._tool_error_response("Error retrieving memory", e)

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
        msg_list = self._normalize_msg_list(msgs)
        if not msg_list:
            return

        await self._ensure_initialized()
        messages = self._to_powermem_messages(msg_list)
        resolved_memory_type = self._resolve_memory_type(
            memory_type,
            kwargs,
        )
        await self._call_memory(
            self._memory.add,
            messages=messages,
            memory_type=resolved_memory_type,
            infer=self._infer if infer is None else infer,
            **self._scoped_kwargs(),
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
        msg_list = self._normalize_msg_input(msg)
        if not msg_list:
            return ""

        queries = [self._message_to_query(item) for item in msg_list]
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
            if callable(initialize):
                await self._call_memory(initialize)
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
        resolved_memory_type = self._resolve_memory_type(None, kwargs)
        await self._call_memory(
            self._memory.add,
            messages=payload,
            memory_type=resolved_memory_type,
            **self._scoped_kwargs(),
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
                limit=limit,
                **self._scoped_kwargs(),
                **kwargs,
            )
            results.extend(self._extract_search_results(response))
        return results

    def _scoped_kwargs(self) -> dict[str, str | None]:
        """Build scoped identifiers for backend calls.

        Returns:
            `dict[str, str | None]`:
                Scoped identifiers for user, agent, and run.
        """
        return {
            "user_id": self._user_id,
            "agent_id": self._agent_id,
            "run_id": self._run_id,
        }

    def _resolve_memory_type(
        self,
        memory_type: str | None,
        kwargs: dict[str, Any],
    ) -> str | None:
        """Resolve memory type with explicit precedence.

        Precedence:
        1) Explicit ``memory_type`` argument
        2) ``memory_type`` in ``kwargs``
        3) ``default_memory_type`` from constructor

        Args:
            memory_type (`str | None`):
                The explicit memory type argument.
            kwargs (`dict[str, Any]`):
                Keyword arguments that may contain ``memory_type``.

        Returns:
            `str | None`:
                The resolved memory type.
        """
        if memory_type is not None:
            return memory_type
        if "memory_type" in kwargs:
            return kwargs["memory_type"]
        return self._default_memory_type

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
        call_kwargs = self._filter_supported_kwargs(func, kwargs)
        if inspect.iscoroutinefunction(func):
            return await func(*args, **call_kwargs)
        result = await asyncio.to_thread(func, *args, **call_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _filter_supported_kwargs(func: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Filter kwargs based on the callable signature when possible.

        This avoids passing unsupported parameters to custom backends while
        keeping full kwargs for functions that accept ``**kwargs``.

        Args:
            func (`Any`):
                The target callable.
            kwargs (`dict[str, Any]`):
                The keyword arguments to filter.

        Returns:
            `dict[str, Any]`:
                Filtered keyword arguments.
        """
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return kwargs

        params = signature.parameters.values()
        if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params):
            return kwargs

        supported = set(signature.parameters.keys())
        return {key: value for key, value in kwargs.items() if key in supported}

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
    def _tool_response(text: str) -> ToolResponse:
        """Create a standard tool response with text content.

        Args:
            text (`str`):
                The text content to return.

        Returns:
            `ToolResponse`:
                A tool response containing a single text block.
        """
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=text,
                ),
            ],
        )

    @staticmethod
    def _tool_error_response(prefix: str, error: Exception) -> ToolResponse:
        """Create a standard tool error response.

        Args:
            prefix (`str`):
                The error message prefix.
            error (`Exception`):
                The captured exception.

        Returns:
            `ToolResponse`:
                A tool response containing the formatted error.
        """
        return PowerMemLongTermMemory._tool_response(f"{prefix}: {error}")

    @staticmethod
    def _normalize_msg_list(msgs: list[Msg | None] | Msg) -> list[Msg]:
        """Normalize record input into a validated message list.

        Args:
            msgs (`list[Msg | None] | Msg`):
                The input messages to normalize.

        Returns:
            `list[Msg]`:
                A validated list of messages.

        Raises:
            `TypeError`:
                If the input is not a message or list of messages.
        """
        if isinstance(msgs, Msg):
            msgs = [msgs]
        if not isinstance(msgs, list):
            raise TypeError(
                "The input messages must be a Msg or a list of Msg objects.",
            )
        msg_list = [msg for msg in msgs if msg is not None]
        if not all(isinstance(msg, Msg) for msg in msg_list):
            raise TypeError(
                "The input messages must be a list of Msg objects.",
            )
        return msg_list

    @staticmethod
    def _normalize_msg_input(msg: Msg | list[Msg] | None) -> list[Msg]:
        """Normalize retrieve input into a validated message list.

        Args:
            msg (`Msg | list[Msg] | None`):
                The input message(s).

        Returns:
            `list[Msg]`:
                A validated list of messages, or an empty list when input
                is None.

        Raises:
            `TypeError`:
                If the input is not a message or list of messages.
        """
        if msg is None:
            return []
        if isinstance(msg, Msg):
            msg = [msg]
        if not isinstance(msg, list) or not all(
            isinstance(item, Msg) for item in msg
        ):
            raise TypeError(
                "The input message must be a Msg or a list of Msg objects.",
            )
        return msg

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
