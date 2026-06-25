# -*- coding: utf-8 -*-
"""Service-layer RAG middleware.

The :class:`KnowledgeBaseMiddleware` is the service-layer counterpart
of :class:`agentscope.middleware.RAGMiddleware`: it wraps one or more
:class:`~agentscope.app.rag.Knowledge` runtime handles instead of a
single ``(embedding_model, vector_store, collections)`` triple.  Each
knowledge base keeps its own embedding model, vector store, and
metadata filter, so a single agent can retrieve across knowledge bases
that were created with *different* embedding models — something the
building-block middleware cannot express.

The user-tunable parameters are declared on the nested
:class:`KnowledgeBaseMiddlewareParameters` Pydantic model; the JSON
Schema served to the front-end is derived from it via
``model_json_schema()``.
"""
import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Literal,
)

from pydantic import BaseModel, Field

from ..._logging import logger
from ...message import (
    AssistantMsg,
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ToolResultState,
)
from ...middleware import MiddlewareBase
from ...permission import PermissionBehavior, PermissionDecision
from ...tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from ...agent import Agent
    from ...rag import VectorSearchResult
    from .knowledge_base_manager import Knowledge


_DEFAULT_HINT_TEMPLATE = (
    "The following content is retrieved from the knowledge bases and "
    "may be helpful for the current request:\n{context}"
)
# The default wrapper around the formatted retrieval results.  Must
# contain a ``{context}`` placeholder.

_HINT_SOURCE = "rag"
# The ``source`` value stamped on injected hint blocks.


def _format_results(results: list["VectorSearchResult"]) -> str | None:
    """Format text retrieval results into a numbered, cited list.

    Non-text chunks (multimodal :class:`DataBlock` content) are skipped
    — only text chunks can be inlined into a prompt.

    Args:
        results (`list[VectorSearchResult]`):
            The retrieval results to format.

    Returns:
        `str | None`:
            The formatted text, or ``None`` when no text chunk exists.
    """
    entries = [
        f"[{index}] (source: {result.chunk.source})\n"
        f"{result.chunk.content.text}"
        for index, result in enumerate(results, start=1)
        if result.chunk.content.type == "text"
    ]
    if not entries:
        return None
    return "\n\n".join(entries)


class KnowledgeBaseMiddlewareParameters(BaseModel):
    """User-tunable parameters of :class:`KnowledgeBaseMiddleware`.

    The fields here are exactly the keys the front-end persists into
    ``SessionKnowledgeConfig.parameters`` and the keys
    :class:`KnowledgeBaseMiddleware` accepts as ``**parameters``.  Keep
    every field annotated with a ``title`` and ``description`` — the
    front-end renders them as labels and tooltips via
    ``model_json_schema()``.
    """

    mode: Literal["hint", "agentic"] = Field(
        default="hint",
        title="Mode",
        description=(
            "`hint`: automatically retrieve at the first reasoning "
            "step of each reply and inject the results into the "
            "context.  `agentic`: expose a `retrieve_knowledge` tool "
            "for the model to call on its own."
        ),
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        title="Top K",
        description=(
            "Maximum number of chunks returned per retrieval, across "
            "all configured knowledge bases."
        ),
    )

    score_threshold: float | None = Field(
        default=None,
        title="Score threshold",
        description=(
            "Minimum similarity score for a hit to be kept.  Leave "
            "empty to disable filtering.  Only meaningful for "
            "similarity metrics where higher is better (cosine / "
            "dot-product)."
        ),
    )

    emit_hint_event: bool = Field(
        default=True,
        title="Show retrieved chunks in chat",
        description=(
            "In `hint` mode, also emit a `HintBlockEvent` so the "
            "front-end can display the retrieved snippets to the user."
        ),
    )

    persist_hint: bool = Field(
        default=False,
        title="Persist hint across turns",
        description=(
            "In `hint` mode, keep the injected hint block in the "
            "agent context for subsequent reasoning steps instead of "
            "removing it right after the model call."
        ),
    )

    # ``hint_template`` is intentionally not exposed as a user-tunable
    # parameter: the wrapper text is part of the middleware's prompt
    # contract and exposing it through the dock UI invites
    # session-by-session prompt drift.  The constructor still accepts
    # the kwarg for programmatic use; ``SessionKnowledgeConfig`` simply
    # never sets it.


class _RetrieveKnowledgeTool(ToolBase):
    """A read-only tool that searches every bound knowledge base.

    Exposed by :class:`KnowledgeBaseMiddleware` in ``"agentic"`` mode
    so the agent can decide when (and what) to retrieve.  A single
    tool fans out across all configured knowledge bases — the model is
    not asked to choose which knowledge base to query.
    """

    name: str = "retrieve_knowledge"
    """The tool name presented to the agent."""

    description: str = (
        "Search the configured knowledge bases for content relevant "
        "to a query. Use this tool when you need background "
        "knowledge, facts, or documents to answer the user's "
        "question. Results are aggregated across every knowledge "
        "base attached to this agent. Returns the most relevant text "
        "chunks with their sources."
    )
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The natural-language search query.",
            },
        },
        "required": ["query"],
    }

    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False
    is_mcp: bool = False

    def __init__(self, middleware: "KnowledgeBaseMiddleware") -> None:
        """Initialize the retrieval tool.

        Args:
            middleware (`KnowledgeBaseMiddleware`):
                The owning middleware, whose retrieval pipeline is
                reused for the actual search.
        """
        # ``ToolBase.__init__`` expects a list of tool middlewares;
        # this tool has none of its own, so pass ``None`` (the owning
        # ``KnowledgeBaseMiddleware`` is an agent middleware, not a
        # tool one).
        super().__init__()
        self._middleware = middleware

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> Any:
        """Allow the engine to handle this read-only retrieval.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                An allow decision — knowledge retrieval is read-only.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Knowledge retrieval is read-only.",
        )

    async def call(  # type: ignore[override]
        self,
        query: str,
    ) -> ToolChunk:
        """Search every bound knowledge base and return formatted results.

        Args:
            query (`str`):
                The natural-language search query.

        Returns:
            `ToolChunk`:
                The formatted retrieval results, or a notice when
                nothing relevant is found.
        """
        try:
            results = await self._middleware.retrieve([query])
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("retrieve_knowledge failed.")
            return ToolChunk(
                content=[TextBlock(text=f"Retrieval failed: {e}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        formatted = _format_results(results)
        if formatted is None:
            return ToolChunk(
                content=[TextBlock(text="No relevant content found.")],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        return ToolChunk(
            content=[TextBlock(text=formatted)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )


class KnowledgeBaseMiddleware(MiddlewareBase):
    """Service-layer middleware that retrieves across multiple knowledge bases.

    Unlike the building-block :class:`agentscope.middleware.RAGMiddleware`,
    this middleware is constructed from a list of
    :class:`~agentscope.app.rag.Knowledge` handles — each carrying its
    own embedding model and vector store.  This makes it possible to
    attach knowledge bases with *different* embedding models (or even
    different vector store backends in the future) to a single agent.

    Two modes are supported, selected via the ``mode`` parameter:

    - ``"agentic"`` — exposes a single ``retrieve_knowledge`` tool via
      :meth:`list_tools`.  The tool fans out across every bound
      knowledge base; the model is **not** asked to pick a knowledge
      base.
    - ``"hint"`` — on the first reasoning step of each reply
      (``agent.state.cur_iter == 0``) the middleware retrieves with
      the fresh user turn as the query and injects the merged results
      into ``agent.state.context`` as a
      :class:`~agentscope.message.HintBlock`.  Optionally surfaces a
      :class:`~agentscope.event.HintBlockEvent` so the front-end can
      display the retrieved snippets.

    .. note::
        Scores from knowledge bases with different embedding models
        are not strictly comparable; the merge below sorts by raw
        score.  For mixed-embedding deployments where this matters,
        switch to a rank-based fusion (e.g. RRF) — the per-KB
        :meth:`Knowledge.search` already returns ordered results.
    """

    Parameters = KnowledgeBaseMiddlewareParameters
    """The Pydantic model describing user-tunable parameters.

    Exposed as a class attribute so the HTTP layer can derive the JSON
    Schema without importing the type separately:
    ``KnowledgeBaseMiddleware.Parameters.model_json_schema()``.
    """

    def __init__(
        self,
        knowledges: list["Knowledge"],
        mode: Literal["hint", "agentic"] = "hint",
        top_k: int = 5,
        score_threshold: float | None = None,
        emit_hint_event: bool = True,
        persist_hint: bool = False,
        hint_template: str | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            knowledges (`list[Knowledge]`):
                The runtime handles of the knowledge bases this agent
                should retrieve from.  Each handle was resolved by the
                :class:`~agentscope.app.rag.KnowledgeBaseManagerBase`
                and carries its own embedding model, vector store, and
                metadata filter.
            mode (`Literal["hint", "agentic"]`, defaults to ``"hint"``):
                Integration mode — see class docstring.
            top_k (`int`, defaults to ``5``):
                Maximum number of chunks returned per retrieval,
                across all knowledge bases.
            score_threshold (`float | None`, optional):
                Minimum similarity score for a hit to be kept.
                Forwarded to each :meth:`Knowledge.search` call.
            emit_hint_event (`bool`, defaults to ``True``):
                In ``"hint"`` mode, whether to yield a
                :class:`~agentscope.event.HintBlockEvent` so the
                front-end can render the retrieved snippets.
            persist_hint (`bool`, defaults to ``False``):
                In ``"hint"`` mode, whether the injected hint block
                stays in the context after the reasoning step.  When
                ``False`` the hint is one-shot.
            hint_template (`str | None`, optional):
                A template wrapping the formatted retrieval results,
                with a ``{context}`` placeholder.  ``None`` uses the
                built-in default.  Only used in ``"hint"`` mode.
        """
        self._knowledges = knowledges
        self._mode = mode
        self._top_k = top_k
        self._score_threshold = score_threshold
        self._emit_hint_event = emit_hint_event
        self._persist_hint = persist_hint
        self._hint_template = hint_template or _DEFAULT_HINT_TEMPLATE

    # ------------------------------------------------------------------
    # Agentic mode — expose the retrieval tool
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[ToolBase]:
        """Expose the retrieval tool in ``"agentic"`` mode.

        Returns:
            `list[ToolBase]`:
                A single ``retrieve_knowledge`` tool in ``"agentic"``
                mode; an empty list in ``"hint"`` mode.
        """
        if self._mode == "agentic":
            return [_RetrieveKnowledgeTool(self)]
        return []

    # ------------------------------------------------------------------
    # Hint mode — retrieve on the first reasoning step of each reply
    # ------------------------------------------------------------------

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject a one-shot RAG hint on the first reasoning step.

        Only active in ``"hint"`` mode and only when
        ``agent.state.cur_iter == 0`` — i.e. the first reasoning
        cycle right after the agent appended the new user turn to
        ``agent.state.context``.  Mirrors the injection / removal
        pattern used by :class:`agentscope.middleware.RAGMiddleware`
        so other hint-producing middlewares can append to the same
        carrier without interfering.

        Args:
            agent (`Agent`):
                The executing agent whose ``state.context`` receives
                the hint.
            input_kwargs (`dict`):
                Reasoning input kwargs; forwarded unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reasoning logic.

        Yields:
            `Any`:
                An optional ``HintBlockEvent`` followed by events from
                downstream.
        """
        hint: HintBlock | None = None

        if self._mode == "hint" and agent.state.cur_iter == 0:
            queries = self._extract_queries_from_context(agent)
            if queries:
                try:
                    results = await self.retrieve(queries)
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "Knowledge base retrieval failed, proceeding "
                        "without retrieved context.",
                    )
                    results = []
                formatted = _format_results(results)
                if formatted is not None:
                    hint = HintBlock(
                        hint=self._hint_template.format(context=formatted),
                        source=_HINT_SOURCE,
                    )

        carrier: Msg | None = None
        created_new = False
        if hint is not None:
            context = agent.state.context
            if (
                context
                and context[-1].role == "assistant"
                and context[-1].name == agent.name
            ):
                carrier = context[-1]
                carrier.content.append(hint)
            else:
                carrier = AssistantMsg(
                    id=agent.state.reply_id,
                    name=agent.name,
                    content=[hint],
                )
                context.append(carrier)
                created_new = True

            if self._emit_hint_event:
                from ...event import HintBlockEvent

                yield HintBlockEvent(
                    reply_id=agent.state.reply_id,
                    block_id=hint.id,
                    source=hint.source,
                    hint=hint.hint,
                )

        try:
            async for evt in next_handler(**input_kwargs):
                yield evt
        finally:
            if (
                hint is not None
                and not self._persist_hint
                and carrier is not None
            ):
                carrier.content = [
                    b for b in carrier.content if b.id != hint.id
                ]
                if created_new and not carrier.content:
                    try:
                        agent.state.context.remove(carrier)
                    except ValueError:
                        pass

    # ------------------------------------------------------------------
    # Retrieval pipeline (shared by both modes)
    # ------------------------------------------------------------------

    def _extract_queries_from_context(
        self,
        agent: "Agent",
    ) -> list[str | DataBlock]:
        """Build the query list from the current user turn in context.

        The agent appends the new user ``Msg`` objects to
        ``agent.state.context`` before the first reasoning step, so
        the "fresh user turn" is the trailing run of ``role=='user'``
        messages.  All their text content is joined into one text
        query, and every :class:`DataBlock` in those messages is
        collected as an additional query.  Per-knowledge-base
        embedding-model capability filtering happens inside
        :meth:`retrieve`.

        Args:
            agent (`Agent`):
                The executing agent; its ``state.context`` is read
                to locate the fresh user turn.

        Returns:
            `list[str | DataBlock]`:
                The query inputs for the embedding model.  Empty list
                when the reply was triggered by a resumption / event
                (no fresh user message at the tail of the context).
        """
        context = agent.state.context
        msgs: list[Msg] = []
        for msg in reversed(context):
            if msg.role == "user":
                msgs.append(msg)
            else:
                break
        msgs.reverse()
        if not msgs:
            return []

        queries: list[str | DataBlock] = []
        texts = [
            text
            for msg in msgs
            if (text := msg.get_text_content()) is not None
        ]
        if texts:
            queries.append("\n".join(texts))

        for msg in msgs:
            queries.extend(msg.get_content_blocks("data"))

        return queries

    async def retrieve(
        self,
        queries: list[str | DataBlock],
    ) -> list["VectorSearchResult"]:
        """Search every bound knowledge base concurrently and merge.

        Each knowledge base receives only the query inputs its
        embedding model can handle: text-only models see strings, and
        multimodal-capable models additionally see
        :class:`DataBlock`s.  The per-KB results — already deduplicated
        and ``top_k``-truncated by :meth:`Knowledge.search` — are
        flattened, sorted by descending score, and truncated to the
        middleware's ``top_k``.

        Args:
            queries (`list[str | DataBlock]`):
                The query inputs (text and/or multimodal blocks).

        Returns:
            `list[VectorSearchResult]`:
                At most ``top_k`` hits across all knowledge bases.
        """
        if not queries or not self._knowledges:
            return []

        text_queries: list[str | DataBlock] = [
            q for q in queries if isinstance(q, str)
        ]

        async def _search_one(
            kb: "Knowledge",
        ) -> list["VectorSearchResult"]:
            inputs = (
                queries
                if kb.embedding_model.supports_multimodal
                else text_queries
            )
            if not inputs:
                return []
            return await kb.search(
                queries=inputs,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )

        results_per_kb = await asyncio.gather(
            *(_search_one(kb) for kb in self._knowledges),
        )

        merged = [result for sub in results_per_kb for result in sub]
        merged.sort(key=lambda result: result.score, reverse=True)
        return merged[: self._top_k]
