# -*- coding: utf-8 -*-
"""mem0-backed long-term memory middleware for AgentScope agents.

Works with either ``mem0.AsyncMemory`` (open-source) or
``mem0.AsyncMemoryClient`` (hosted Platform). Both clients converge on
the same call shape:

- ``search(query, filters={"user_id": ..., "agent_id": ...}, top_k=...)``
- ``add(messages, user_id=..., agent_id=...)``

so one middleware class handles both.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    TYPE_CHECKING,
)
from ..._base import MiddlewareBase
from ...._logging import logger
from ....event import ReplyStartEvent
from ....message import AssistantMsg, HintBlock, Msg
from ._tools import _build_memory_tools
from ._utils import (
    _extract_memory_texts,
    _extract_query_text,
    _mem0_extracted_anything,
)

if TYPE_CHECKING:
    from typing import TypeAlias

    from mem0 import AsyncMemory, AsyncMemoryClient

    from ....agent import Agent
    from ....embedding import EmbeddingModelBase
    from ....model import ChatModelBase
    from ....tool import ToolBase

    # Explicit ``TypeAlias`` annotation tells mypy this is a type
    # alias rather than a plain variable assignment — without it
    # mypy 1.7+ refuses to use ``Mem0AsyncClient`` in annotation
    # positions ("Variable is not valid as a type").
    Mem0AsyncClient: TypeAlias = AsyncMemory | AsyncMemoryClient


def _looks_async(method: Any) -> bool:
    """Detect "async-callable" methods, including ones wrapped by a
    sync ``@functools.wraps`` decorator that returns the underlying
    coroutine.

    Plain ``inspect.iscoroutinefunction`` is too strict for mem0's
    Platform client: ``AsyncMemoryClient.search`` is ``async def`` but
    decorated by mem0's ``@api_error_handler``, which is a sync wrapper
    that returns the coroutine produced by calling the underlying async
    function. The wrapper itself isn't a coroutine function, so the
    naive check rejects an otherwise-valid client. ``inspect.unwrap``
    walks ``__wrapped__`` (set by ``functools.wraps``) until it finds
    the original async ``func``, which the strict check then accepts.
    """
    if method is None:
        return False
    return inspect.iscoroutinefunction(inspect.unwrap(method))


DEFAULT_MEMORY_SECTION_HEADER = "## Relevant memories from past conversations"
DEFAULT_MEMORY_SECTION_INTRO = (
    "The following memories about the user may be relevant. "
    "Use them only if they are pertinent to the current request."
)

DEFAULT_TOOL_INSTRUCTIONS = (
    "## Long-term memory\n\n"
    "You have `search_memory` and `add_memory` tools available. Use "
    "them whenever the conversation depends on (search) or contributes "
    "(add) a durable fact about the user — see each tool's own "
    "description for the exact input shape and usage guidance."
)


class Mem0Middleware(MiddlewareBase):
    """AgentScope middleware that adds long-term memory backed by
    `mem0 <https://github.com/mem0ai/mem0>`_.

    Two construction paths:

    1. **Models** — pass an AgentScope ``chat_model`` and
       ``embedding_model`` (optionally with a custom ``mem0_config``
       as base for vector store / history DB / etc.). The middleware
       builds an OSS ``AsyncMemory`` internally, wired so mem0's
       memory extraction and embedding both go through your AgentScope
       models.
    2. **Client** — pass a pre-built ``mem0.AsyncMemory`` /
       ``mem0.AsyncMemoryClient`` when you want full control (e.g.
       hosted Platform, sharing one mem0 across agents). When
       ``client`` is given it takes precedence and the other backend
       kwargs are ignored.

    Three control patterns are available via the ``mode`` parameter
    (``"static_control"`` / ``"agent_control"`` / ``"both"``); see
    the constructor's ``mode`` arg for what each does.

    Example (build OSS internally)::

        from agentscope.middleware import Mem0Middleware
        from agentscope.tool import Toolkit

        mw = Mem0Middleware(
            user_id="alice",
            chat_model=my_chat_model,
            embedding_model=my_embedding_model,
            mode="both",
        )
        agent = Agent(
            ...,
            toolkit=Toolkit(tools=await mw.list_tools()),
            middlewares=[mw],
        )

    Example (hosted Platform with pre-built client)::

        from agentscope.middleware import Mem0Middleware
        from agentscope.tool import Toolkit
        from mem0 import AsyncMemoryClient

        mw = Mem0Middleware(
            user_id="alice",
            client=AsyncMemoryClient(api_key="m0-..."),
            mode="both",
        )
        agent = Agent(
            ...,
            toolkit=Toolkit(tools=await mw.list_tools()),
            middlewares=[mw],
        )
    """

    def __init__(
        self,
        *,
        user_id: str,
        client: Mem0AsyncClient | None = None,
        chat_model: "ChatModelBase | None" = None,
        embedding_model: "EmbeddingModelBase | None" = None,
        mem0_config: Any | None = None,
        mode: Literal["static_control", "agent_control", "both"] = "both",
        agent_id: str | None = None,
        top_k: int = 5,
        threshold: float | None = None,
        scope_search_by_agent: bool = True,
        await_write: bool = True,
        memory_section_header: str = DEFAULT_MEMORY_SECTION_HEADER,
        memory_section_intro: str = DEFAULT_MEMORY_SECTION_INTRO,
        tool_instructions: str = DEFAULT_TOOL_INSTRUCTIONS,
    ) -> None:
        """Initialize the mem0 middleware.

        Three ways to wire up the mem0 backend:

        - **Models only** — pass ``chat_model`` + ``embedding_model``
          and the middleware builds a local OSS ``AsyncMemory`` wired
          to them (mem0's default Qdrant for storage).
        - **Models + ``mem0_config``** — same but starts from your
          customized ``MemoryConfig``; only ``.llm`` / ``.embedder``
          slots are overridden with the AgentScope adapters, every
          other field (vector store, history DB, reranker, ...) is
          preserved.
        - **Client** — pass a pre-built mem0 client (OSS / Platform /
          custom). When ``client`` is given it takes absolute
          precedence; ``chat_model`` / ``embedding_model`` /
          ``mem0_config`` are ignored, with a warning listing any
          ignored kwargs.

        Args:
            user_id:
                The mem0 ``user_id`` for memory namespacing. Required.
            client:
                A pre-built mem0 async client —
                ``mem0.AsyncMemory`` (OSS) or
                ``mem0.AsyncMemoryClient`` (Platform). Use this when
                you want full control over the mem0 setup.
            chat_model:
                The AgentScope chat model mem0 should use for memory
                extraction. Required if ``client`` is not given and
                ``mem0_config`` does not already supply an LLM.
            embedding_model:
                The AgentScope embedding model mem0 should use to
                embed memories. Required if ``client`` is not given
                and ``mem0_config`` does not already supply an embedder.
                Its ``dimensions`` must match mem0's vector store
                (the default Qdrant expects 1536).
            mem0_config:
                Optional ``mem0.configs.base.MemoryConfig`` to use as
                the base — lets you customize vector store / history
                DB / reranker / etc. while still routing LLM and
                embedding through AgentScope. Mutually exclusive with
                ``client``.
            mode:
                How the agent interacts with mem0:

                - ``"static_control"``: middleware searches mem0
                  before each reply, appends the retrieved memories
                  to ``agent.state.context`` as an
                  ``AssistantMsg(name="memory")``, and writes the new
                  exchange back after the reply. The agent never sees
                  mem0 as a tool.
                - ``"agent_control"``: middleware exposes
                  ``search_memory`` / ``add_memory`` tools for the
                  agent to invoke on demand, plus a short nudge in
                  the system prompt. No automatic retrieval or
                  write-back.
                - ``"both"``: both patterns at once — auto retrieval
                  AND on-demand tools.

                Defaults to ``"both"`` (matching AgentScope 1.x's
                ``ReActAgent.long_term_memory_mode`` default).
            agent_id:
                Optional mem0 ``agent_id`` for finer-grained
                namespacing. When ``None`` (default) mem0 receives no
                ``agent_id`` filter and memories are scoped by
                ``user_id`` only.
            top_k:
                Max number of memories retrieved per static-control
                search. Also serves as the default ``top_k`` for the
                ``search_memory`` tool (the agent can override).
            threshold:
                Minimum similarity score. ``None`` lets mem0 decide.
            scope_search_by_agent:
                When ``True`` (default) search filters include both
                ``user_id`` and ``agent_id`` — memories are scoped to
                the agent that created them. When ``False`` search uses
                ``user_id`` only, so a user's memories are shared across
                agents.
            await_write:
                When ``True`` (default) the post-turn ``add`` call is
                awaited inline. When ``False`` it's fire-and-forget —
                faster response but exceptions only surface in logs.
            memory_section_header, memory_section_intro:
                Strings used when injecting retrieved memories into
                the model's messages list (``static_control`` /
                ``both`` modes).
            tool_instructions:
                Markdown block appended to the agent's system prompt
                in ``agent_control`` / ``both`` modes, advertising the
                ``search_memory`` / ``add_memory`` tools to the LLM.
        """
        is_empty_user_id = isinstance(user_id, str) and not user_id.strip()
        if user_id is None or is_empty_user_id:
            raise ValueError(
                "Mem0Middleware requires a non-empty `user_id`.",
            )
        if mode not in ("static_control", "agent_control", "both"):
            raise ValueError(
                f"Unknown mode {mode!r}; expected one of "
                f"'static_control', 'agent_control', 'both'.",
            )

        client = self._resolve_client(
            client=client,
            chat_model=chat_model,
            embedding_model=embedding_model,
            mem0_config=mem0_config,
        )
        self._client = client

        self._user_id = user_id
        self._agent_id = agent_id
        self._mode = mode
        self._top_k = top_k
        self._threshold = threshold
        self._scope_search_by_agent = scope_search_by_agent
        self._await_write = await_write
        self._memory_section_header = memory_section_header
        self._memory_section_intro = memory_section_intro
        self._tool_instructions = tool_instructions

    # ------------------------------------------------------------------
    # Hook: on_reply
    # ------------------------------------------------------------------
    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        user_id = self._user_id
        agent_id = self._agent_id

        # In pure agent_control mode the middleware is a no-op on the
        # reply path — the agent decides when to invoke the memory
        # tools — so just pass through.
        if self._mode == "agent_control":
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # static_control / both, mirroring AgentScope 1.x's ReActAgent:
        # 1. Pre-fetch memories from mem0 using the user's new query.
        # 2. Once the agent has actually ingested the new user input
        #    into state.context (signaled by ReplyStartEvent — fires
        #    right after _handle_incoming_messages and before the
        #    reasoning loop), append the memory note. This places the
        #    note IMMEDIATELY AFTER the user message in context, same
        #    slot as v1's `_retrieve_from_long_term_memory` (which ran
        #    right after `self.memory.add(msg)`).
        # 3. After the reply finishes, write the new exchange back.
        #
        # The memory note persists in state.context across turns. Long
        # sessions will accumulate one per turn that retrieved
        # anything; rely on ``compress_context`` or pop them yourself
        # if that becomes a token concern.
        # user_id / agent_id already resolved + cached above.

        inputs = input_kwargs.get("inputs")
        query_text = _extract_query_text(inputs)

        memories: list[str] = []
        if query_text:
            search_agent_id = agent_id if self._scope_search_by_agent else None
            logger.info(
                "mem0 search started: user_id=%s agent_id=%s chars=%d",
                user_id,
                search_agent_id,
                len(query_text),
            )
            try:
                memories = await self._async_search(
                    query_text,
                    user_id=user_id,
                    agent_id=search_agent_id,
                )
                logger.info(
                    "mem0 search finished: user_id=%s agent_id=%s memories=%d",
                    user_id,
                    search_agent_id,
                    len(memories),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "mem0 search failed for user_id=%s: %s",
                    user_id,
                    e,
                )

        final_msg: Msg | None = None
        injected = False
        try:
            async for item in next_handler(**input_kwargs):
                is_reply_start = isinstance(item, ReplyStartEvent)
                if not injected and memories and is_reply_start:
                    agent.state.context.append(
                        self._build_memory_message(memories),
                    )
                    injected = True
                if isinstance(item, Msg) and item.role == "assistant":
                    final_msg = item
                yield item
        finally:
            if query_text and final_msg is not None:
                assistant_text = final_msg.get_text_content()
                if assistant_text:
                    await self._dispatch_write(
                        [
                            {"role": "user", "content": query_text},
                            {"role": "assistant", "content": assistant_text},
                        ],
                        user_id=user_id,
                        agent_id=agent_id,
                    )

    # ------------------------------------------------------------------
    # Hook: on_system_prompt (advertise memory tools to the LLM)
    # ------------------------------------------------------------------
    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append memory-tool instructions to the system prompt.

        Args:
            agent (`Agent`):
                The agent whose system prompt is being transformed.
            current_prompt (`str`):
                The system prompt produced by previous middleware.

        Returns:
            `str`:
                The unchanged prompt in static-control mode, otherwise the
                prompt with memory-tool instructions appended.
        """
        if self._mode == "static_control":
            return current_prompt
        return f"{current_prompt}\n\n{self._tool_instructions}"

    async def list_tools(self) -> list["ToolBase"]:
        """List memory tools provided by this middleware.

        Returns:
            `list[ToolBase]`:
                The ``search_memory`` and ``add_memory`` tools in
                agent-control modes, otherwise an empty list.
        """
        if self._mode == "static_control":
            return []
        return _build_memory_tools(self)

    # ==================================================================
    # mem0 client construction
    # ==================================================================
    @staticmethod
    def _resolve_client(
        *,
        client: "Mem0AsyncClient | None",
        chat_model: "ChatModelBase | None",
        embedding_model: "EmbeddingModelBase | None",
        mem0_config: Any | None,
    ) -> "Mem0AsyncClient":
        """Resolve the constructor's mem0-backend kwargs into a single
        async client.

        ``client=`` takes absolute precedence — if given, the other
        three (``chat_model`` / ``embedding_model`` / ``mem0_config``)
        are ignored (a warning is logged so the mismatch is not
        invisible). Otherwise, the rest are combined by
        :func:`build_mem0_config` into an ``AsyncMemory``.
        """
        if client is not None:
            ignored = [
                name
                for name, value in (
                    ("chat_model", chat_model),
                    ("embedding_model", embedding_model),
                    ("mem0_config", mem0_config),
                )
                if value is not None
            ]
            if ignored:
                logger.warning(
                    "Mem0Middleware: `client` was provided, so %s "
                    "%s ignored. Pass them via the mem0 client itself "
                    "(or omit `client` to let the middleware build one "
                    "for you).",
                    ", ".join(ignored),
                    "is" if len(ignored) == 1 else "are",
                )
        if client is None:
            no_models = chat_model is None and embedding_model is None
            if mem0_config is None and no_models:
                raise ValueError(
                    "Mem0Middleware needs one of: a pre-built `client`, "
                    "a `mem0_config`, or both `chat_model` and "
                    "`embedding_model`.",
                )
            # When no mem0_config is given, models must come as a pair.
            if mem0_config is None and (
                (chat_model is None) ^ (embedding_model is None)
            ):
                raise ValueError(
                    "Mem0Middleware: `chat_model` and "
                    "`embedding_model` must be passed together when "
                    "`mem0_config` is not given.",
                )

            from mem0 import AsyncMemory

            from ._agentscope_adapter import build_mem0_config

            client = AsyncMemory(
                config=build_mem0_config(
                    chat_model=chat_model,
                    embedding_model=embedding_model,
                    mem0_config=mem0_config,
                ),
            )

        if not _looks_async(
            getattr(client, "search", None),
        ) or not _looks_async(getattr(client, "add", None)):
            raise TypeError(
                "Mem0Middleware requires an async mem0 client "
                "(`mem0.AsyncMemory` or `mem0.AsyncMemoryClient`). "
                "The synchronous `Memory` / `MemoryClient` are not "
                "supported.",
            )
        return client

    # ==================================================================
    # mem0 client adapters (OSS + Platform share this call shape)
    # ==================================================================
    async def _async_search(
        self,
        query: str,
        *,
        user_id: str,
        agent_id: str | None,
        top_k: int | None = None,
    ) -> list[str]:
        """Search mem0 and normalize the result into memory strings.

        Args:
            query (`str`):
                Search query text.
            user_id (`str`):
                mem0 ``user_id`` namespace.
            agent_id (`str | None`):
                Optional mem0 ``agent_id`` namespace. ``None`` searches by
                user only.
            top_k (`int | None`, optional):
                Optional per-call result limit. Defaults to the middleware's
                configured ``top_k``.

        Returns:
            `list[str]`:
                Retrieved memory texts extracted from mem0's response.
        """
        filters: dict[str, Any] = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id

        kwargs: dict[str, Any] = {
            "filters": filters,
            "top_k": self._top_k if top_k is None else top_k,
        }
        if self._threshold is not None:
            kwargs["threshold"] = self._threshold

        raw = await self._client.search(query, **kwargs)
        return _extract_memory_texts(raw)

    async def _async_add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str,
        agent_id: str | None,
        infer: bool = True,
    ) -> dict | None:
        """Add messages to mem0 using the shared async client call shape.

        Args:
            messages (`list[dict[str, str]]`):
                mem0-compatible message dictionaries.
            user_id (`str`):
                mem0 ``user_id`` namespace.
            agent_id (`str | None`):
                Optional mem0 ``agent_id`` namespace.
            infer (`bool`, optional):
                Whether mem0 should run memory extraction. ``False`` asks
                mem0 to store the text directly.

        Returns:
            `dict | None`:
                The raw result returned by mem0.
        """
        kwargs: dict[str, Any] = {"user_id": user_id}
        if agent_id:
            kwargs["agent_id"] = agent_id
        if not infer:
            # mem0 docstring: ``infer=False`` skips the LLM extraction
            # step and stores the message text directly.
            kwargs["infer"] = False

        return await self._client.add(messages, **kwargs)

    async def _async_add_with_fallback(
        self,
        text: str,
        *,
        user_id: str,
        agent_id: str | None,
    ) -> dict | None:
        """Two-tier add strategy: try extraction first; if mem0's
        extraction LLM returns no memories, fall back to ``infer=False``
        and save the raw text. Guarantees that ``add_memory`` always
        persists *something* — matching AgentScope 1.x's
        ``record_to_memory`` "always save" contract.

        Historical note — why this is 2 tiers, not 3
        ---------------------------------------------
        AgentScope 1.x's ``record_to_memory`` had a 3-tier fallback:
        (a) user role → (b) assistant role → (c) assistant + infer=False.
        Tier (b) was meaningful against **old** mem0, which routed
        user-role messages through ``USER_MEMORY_EXTRACTION_PROMPT``
        and assistant-role messages through
        ``AGENT_MEMORY_EXTRACTION_PROMPT`` — two genuinely different
        prompts, so switching role had a real chance of rescuing an
        empty extraction.

        Current mem0 (v2.x) restructured this in
        ``_add_to_vector_store``:

            parsed_messages = parse_messages(messages)
            ...
            is_agent_scoped = bool(filters.get("agent_id")) \\
                              and not filters.get("user_id")
            system_prompt = ADDITIVE_EXTRACTION_PROMPT
            if is_agent_scoped:
                system_prompt += AGENT_CONTEXT_SUFFIX

        Prompt selection now depends on the **filters dict**, not on
        the message role. ``ADDITIVE_EXTRACTION_PROMPT`` itself says
        explicitly "You extract from BOTH user and assistant messages"
        (it just changes attribution framing). Since this middleware
        always passes both ``user_id`` and ``agent_id`` (when scoping
        is on), ``is_agent_scoped`` is always False and the same
        ``ADDITIVE_EXTRACTION_PROMPT`` runs regardless of message role.
        Retrying with role="assistant" is a no-op LLM call.

        So tier (b) is dropped. Tier (c) ``infer=False`` is still
        valuable — it bypasses extraction entirely and saves raw
        text, useful when mem0 decides nothing in the input is
        memory-worthy but the caller wants the bytes persisted anyway.
        """
        # 1. Normal path: let mem0's extraction LLM do its job.
        result = await self._async_add(
            [{"role": "user", "content": text, "name": "user"}],
            user_id=user_id,
            agent_id=agent_id,
        )
        if _mem0_extracted_anything(result):
            return result

        # 2. Raw save: extraction returned empty, persist the raw text
        # so the caller's ``add_memory`` invocation isn't silently
        # discarded.
        return await self._async_add(
            [{"role": "user", "content": text, "name": "user"}],
            user_id=user_id,
            agent_id=agent_id,
            infer=False,
        )

    # ==================================================================
    # Helpers
    # ==================================================================
    def _build_memory_message(self, memories: list[str]) -> Msg:
        """Format retrieved ``memories`` as a synthetic hint message.

        The context entry uses an assistant-role ``Msg`` container because
        user messages cannot carry ``HintBlock`` content. Formatters convert
        the ``HintBlock`` itself into a user message before the model call.

        Args:
            memories (`list[str]`):
                Retrieved memory texts to expose to the model.

        Returns:
            `Msg`:
                An assistant-role message containing one ``HintBlock``.
        """
        bullets = "\n".join(f"- {m}" for m in memories)
        content = (
            f"{self._memory_section_header}\n"
            f"{self._memory_section_intro}\n"
            f"{bullets}"
        )
        return AssistantMsg(
            name="memory",
            content=[HintBlock(hint=content)],
        )

    async def _dispatch_write(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str,
        agent_id: str | None,
    ) -> None:
        """Persist a completed user/assistant exchange to mem0.

        When ``await_write`` is enabled, the add call is awaited inline so
        errors can be logged before reply cleanup completes. Otherwise, to
        write is scheduled as a background task and failures are logged there.

        Args:
            messages (`list[dict[str, str]]`):
                User/assistant message pair to persist.
            user_id (`str`):
                mem0 ``user_id`` namespace.
            agent_id (`str | None`):
                Optional mem0 ``agent_id`` namespace.
        """
        if self._await_write:
            try:
                logger.info(
                    "mem0 write started: user_id=%s agent_id=%s messages=%d",
                    user_id,
                    agent_id,
                    len(messages),
                )
                await self._async_add(
                    messages,
                    user_id=user_id,
                    agent_id=agent_id,
                )
                logger.info(
                    "mem0 write finished for user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "mem0 add failed for user_id=%s: %s",
                    user_id,
                    e,
                )
        else:

            async def _bg() -> None:
                try:
                    logger.info(
                        "mem0 background write started for user_id=%s "
                        "agent_id=%s messages=%d",
                        user_id,
                        agent_id,
                        len(messages),
                    )
                    await self._async_add(
                        messages,
                        user_id=user_id,
                        agent_id=agent_id,
                    )
                    logger.info(
                        "mem0 bg write finished: user_id=%s agent_id=%s",
                        user_id,
                        agent_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "mem0 background add failed for user_id=%s: %s",
                        user_id,
                        e,
                    )

            asyncio.create_task(_bg())
