# -*- coding: utf-8 -*-
"""RAG middleware that brings knowledge retrieval into the agent loop.

Two usage modes are supported, selected by the ``mode`` constructor
argument:

- ``"agentic"`` — the middleware exposes a ``retrieve_knowledge`` tool
  via :meth:`RAGMiddleware.list_tools`, and the agent decides when to
  call it.  Nothing is injected automatically.
- ``"hint"`` — on the **first** reasoning step of each reply
  (``agent.state.cur_iter == 0``) the middleware retrieves with the
  fresh user turn as the query (text always; multimodal
  :class:`DataBlock` inputs too when the embedding model supports
  them), and injects the results into ``agent.state.context`` as a
  :class:`~agentscope.message.HintBlock` before the model call. The
  hint can be one-shot (removed right after the model call it
  participated in) or persistent, and can optionally be surfaced to
  the front-end via :class:`~agentscope.event.HintBlockEvent`.

Document indexing (parsing, chunking, embedding, insertion) is *not*
this middleware's job — it belongs to the knowledge base management
layer, which also constructs this middleware with already-resolved
``embedding_model`` / ``vector_store`` instances.
"""
import asyncio
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Literal,
)

from ._base import MiddlewareBase
from .._logging import logger
from ..message import (
    AssistantMsg,
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ToolResultState,
)
from ..tool import ToolBase, ToolChunk
from ..permission import PermissionBehavior, PermissionDecision

if TYPE_CHECKING:
    from ..agent import Agent
    from ..embedding import EmbeddingModelBase
    from ..rag import VectorSearchResult, VectorStoreBase

_DEFAULT_HINT_TEMPLATE = (
    "The following content is retrieved from the knowledge base and "
    "may be helpful for the current request:\n{context}"
)
# The default wrapper around the formatted retrieval results.  Must
#  contain a ``{context}`` placeholder.

_HINT_SOURCE = "rag"
# The ``source`` value stamped on injected hint blocks.


def _format_results(results: list["VectorSearchResult"]) -> str | None:
    """Format text retrieval results into a numbered, cited list.

    Non-text chunks (multimodal :class:`DataBlock` content) are
    skipped — only text chunks can be inlined into a prompt.

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


class _RetrieveKnowledgeTool(ToolBase):
    """A read-only tool that searches the bound knowledge bases.

    Exposed by :class:`RAGMiddleware` in ``"agentic"`` mode so the
    agent can decide when (and what) to retrieve.
    """

    name: str = "retrieve_knowledge"
    """The tool name presented to the agent."""

    description: str = (
        "Search the knowledge base for content relevant to a query. "
        "Use this tool when you need background knowledge, facts, or "
        "documents to answer the user's question. Returns the most "
        "relevant text chunks with their sources."
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

    def __init__(self, middleware: "RAGMiddleware") -> None:
        """Initialize the retrieval tool.

        Args:
            middleware (`RAGMiddleware`):
                The owning middleware, whose retrieval pipeline is
                reused for the actual search.
        """
        super().__init__(middleware)
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
                An allow decision — knowledge retrieval is
                read-only.
        """

        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Knowledge retrieval is read-only.",
        )

    async def call(  # type: ignore[override]
        self,
        query: str,
    ) -> ToolChunk:
        """Search the knowledge bases and return formatted results.

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
        except Exception as e:
            logger.exception("retrieve_knowledge failed.")
            return ToolChunk(
                content=[TextBlock(text=f"Retrieval failed: {e}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        formatted = _format_results(results)
        if formatted is None:
            return ToolChunk(
                content=[
                    TextBlock(text="No relevant content found."),
                ],
                state=ToolResultState.RUNNING,
                is_last=True,
            )
        return ToolChunk(
            content=[TextBlock(text=formatted)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )


class RAGMiddleware(MiddlewareBase):
    """Middleware that integrates knowledge retrieval into the agent.

    Constructed with already-resolved instances — the caller (e.g. the
    app service layer) is responsible for loading the knowledge base
    configuration, resolving credentials, and instantiating the
    embedding model.  This keeps the middleware free of any
    application-layer dependency.

    In ``"hint"`` mode the retrieval query is taken from the **reply
    inputs** (the new ``Msg`` / ``list[Msg]`` that triggered the
    reply): all text content is used as one text query, and each
    :class:`DataBlock` becomes an additional multimodal query when the
    embedding model declares ``supports_multimodal``.  Resumption
    events and ``None`` inputs skip retrieval.

    .. note:: Retrieval scores are assumed to be
        "higher = more similar" (cosine / dot-product).  When using a
        distance metric where lower is better, leave
        ``score_threshold`` unset.

    .. code-block:: python

        # Automatic injection (hint mode)
        middleware = RAGMiddleware(
            embedding_model=embedding_model,
            vector_store=vector_store,
            collections=["kb-1"],
            mode="hint",
        )

        # Agent-driven retrieval (agentic mode)
        middleware = RAGMiddleware(
            embedding_model=embedding_model,
            vector_store=vector_store,
            collections=["kb-1"],
            mode="agentic",
        )

        agent = Agent(..., middlewares=[middleware], ...)
    """

    def __init__(
        self,
        embedding_model: "EmbeddingModelBase",
        vector_store: "VectorStoreBase",
        collections: list[str],
        mode: Literal["agentic", "hint"] = "hint",
        top_k: int = 5,
        score_threshold: float | None = None,
        emit_hint_event: bool = False,
        persist_hint: bool = False,
        hint_template: str | None = None,
    ) -> None:
        """Initialize the RAG middleware.

        Args:
            embedding_model (`EmbeddingModelBase`):
                The embedding model used to embed queries.  Must be
                the same model used when indexing the bound
                collections.  Multimodal retrieval is enabled when the
                model declares ``supports_multimodal``.
            vector_store (`VectorStoreBase`):
                The application-wide vector store instance.
            collections (`list[str]`):
                The vector store collections (knowledge base IDs) to
                retrieve from.
            mode (`Literal["agentic", "hint"]`, defaults to \
             ``"hint"``):
                ``"agentic"`` exposes a ``retrieve_knowledge`` tool
                for the agent to call; ``"hint"`` automatically
                retrieves on each reply and injects a
                :class:`HintBlock` into the context.
            top_k (`int`, defaults to ``5``):
                Maximum number of chunks returned per retrieval,
                across all collections and query inputs.
            score_threshold (`float | None`, optional):
                Minimum similarity score for a hit to be used.
                ``None`` disables filtering.
            emit_hint_event (`bool`, defaults to ``False``):
                Whether to yield a
                :class:`~agentscope.event.HintBlockEvent` when a hint
                is injected, so the front-end can display the
                retrieved content.  Only used in ``"hint"`` mode.
            persist_hint (`bool`, defaults to ``False``):
                Whether the injected hint block stays in the agent
                context.  When ``False`` the hint is one-shot: it is
                removed from the context right after the reasoning
                step (model inference) it participated in.  Only used
                in ``"hint"`` mode.
            hint_template (`str | None`, optional):
                A template wrapping the formatted retrieval results,
                with a ``{context}`` placeholder.  ``None`` uses the
                built-in default.  Only used in ``"hint"`` mode.
        """
        self._embedding_model = embedding_model
        self._vector_store = vector_store
        self._collections = collections
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
    # Hint mode — retrieve on the first reasoning step of each reply,
    # inject before the model call
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
        ``agent.state.context``. Subsequent reasoning iterations of
        the same reply (cur_iter > 0) skip retrieval so the agent
        does not re-embed and re-inject for every tool-call round.

        Follows the ``InboxMiddleware`` injection pattern: the hint
        block is appended to the last assistant message, or a new
        assistant message is created. When ``persist_hint`` is
        ``False`` the block is removed right after the wrapped
        reasoning step completes — it participates in exactly one
        model inference. Removal keys on ``hint.id`` rather than block
        type, so other middlewares (Inbox, …) can append their own
        HintBlocks to the same carrier without interfering.

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

        # Manually retrieval knowledge base at the first reasoning step
        if self._mode == "hint" and agent.state.cur_iter == 0:
            queries = self._extract_queries_from_context(agent)
            if queries:
                try:
                    results = await self.retrieve(queries)
                except Exception:
                    logger.exception(
                        "RAG retrieval failed, proceeding without "
                        "retrieved context.",
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
                from ..event import HintBlockEvent

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
            if hint is not None and not self._persist_hint:
                if carrier is None:
                    return
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
        query; when the embedding model supports multimodal inputs,
        each :class:`DataBlock` in those messages becomes an
        additional query.

        Args:
            agent (`Agent`):
                The executing agent; its ``state.context`` is read
                to locate the fresh user turn.

        Returns:
            `list[str | DataBlock]`:
                The query inputs for the embedding model. Empty list
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

        if self._embedding_model.supports_multimodal:
            for msg in msgs:
                queries.extend(msg.get_content_blocks("data"))

        return queries

    async def retrieve(
        self,
        queries: list[str | DataBlock],
    ) -> list["VectorSearchResult"]:
        """Embed the queries and search all bound collections.

        All queries are embedded in one batch; each resulting vector
        is searched against every collection concurrently.  Hits are
        deduplicated by chunk content ID (keeping the best score),
        filtered by ``score_threshold`` (when set), sorted by
        descending score, and truncated to ``top_k``.

        Args:
            queries (`list[str | DataBlock]`):
                The query inputs (text and/or multimodal blocks).

        Returns:
            `list[VectorSearchResult]`:
                At most ``top_k`` deduplicated hits.
        """
        response = await self._embedding_model(queries)

        results_per_search = await asyncio.gather(
            *(
                self._vector_store.search(
                    collection=collection,
                    query_vector=vector,
                    top_k=self._top_k,
                )
                for vector in response.embeddings
                for collection in self._collections
            ),
        )

        best: dict[tuple[str, int], "VectorSearchResult"] = {}
        for results in results_per_search:
            for result in results:
                if (
                    self._score_threshold is not None
                    and result.score < self._score_threshold
                ):
                    continue
                # ``(document_id, chunk_index)`` is the stable identity
                # of a chunk: it survives reindex (block UUIDs do not),
                # and uniquely names "this slice of that document"
                # regardless of which query / collection surfaced it.
                key = (result.document_id, result.chunk.chunk_index)
                if key not in best or result.score > best[key].score:
                    best[key] = result

        merged = sorted(
            best.values(),
            key=lambda result: result.score,
            reverse=True,
        )
        return merged[: self._top_k]
