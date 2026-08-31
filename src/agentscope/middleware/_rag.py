# -*- coding: utf-8 -*-
"""RAG middleware that brings knowledge-base search into the agent loop.

The :class:`RAGMiddleware` wraps one or more
:class:`~agentscope.rag.KnowledgeBase` runtime handles — each carrying
its own embedding model, vector store, and (optional) metadata filter —
so a single agent can search across knowledge bases that were created
with *different* embedding models.

Two modes are supported, selected via :class:`SearchConfig.mode`:

- ``"agentic"`` — exposes a single ``search_knowledge`` tool via
  :meth:`RAGMiddleware.list_tools`.  The agent decides when (and which
  knowledge bases) to query.  Nothing is injected automatically.
- ``"static"`` — on the first reasoning step of each reply
  (``agent.state.cur_iter == 0``) the middleware searches with the
  fresh user turn as the query and injects the merged results into
  ``agent.state.context`` as a :class:`~agentscope.message.HintBlock`.
  Optionally surfaces a
  :class:`~agentscope.event.HintBlockEvent` so the front-end can
  display the matched snippets.

User-tunable parameters are declared on :class:`SearchConfig`; the
JSON Schema served to the front-end is derived from it via
``model_json_schema()``.

Document indexing (parsing, chunking, embedding, insertion) is *not*
this middleware's job — it belongs to the caller (or, in the hosted
service, to the knowledge-base manager) that constructs the
:class:`KnowledgeBase` instances passed in.
"""
import asyncio
import json
from copy import deepcopy
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    Sequence,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from ._base import MiddlewareBase
from .._logging import logger
from ..event import HintBlockEvent
from ..message import (
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ToolResultState,
)
from ..permission import PermissionBehavior, PermissionDecision
from ..tool import ParamsBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from ..agent import Agent
    from ..rag import KnowledgeBase, VectorSearchResult


_DEFAULT_HINT_TEMPLATE = (
    "<system-reminder>The following content is retrieved from the "
    "knowledge base(s) and may be helpful for the current "
    "request:\n<content>{context}</content></system-reminder>"
)
# Wrapper around the formatted search results.  Must contain a single
# ``{context}`` placeholder — :func:`_wrap_hint` splits on it.

_HINT_SOURCE = json.dumps({"label": "KnowledgeBase", "sublabel": ""})
# The ``source`` value stamped on injected hint blocks.  Encoded as a
# JSON string so the front-end can parse a structured label out of it
# while the field stays a plain ``str`` everywhere else.


class _SearchParams(ParamsBase):
    """The parameters accepted by ``_SearchKnowledgeTool``."""

    query: str = Field(
        description=(
            "The query string to search the knowledge base(s) with. "
            "Must be concise, explicit, and self-contained: AVOID "
            "ambiguous references like `he`/`she`/`it`, "
            "`today`/`yesterday`/`tomorrow`, `here`/`there`, etc. — "
            "the search is purely semantic and has no conversational "
            "context. Phrase the query as a complete statement of "
            "what you want to find."
        ),
    )

    knowledge_bases: list[str] | None = Field(
        default=None,
        description=(
            "Optional subset of knowledge bases to query, by name. "
            "When omitted (or `null`) every equipped knowledge base "
            "is searched. Names must exactly match those listed in "
            "the tool description."
        ),
    )


class _SearchKnowledgeTool(ToolBase):
    """The agentic-mode search tool exposed by :class:`RAGMiddleware`.

    A single tool fans out across every bound knowledge base; the
    agent may also pass ``knowledge_bases`` to restrict the search to
    a subset by name.
    """

    name: str = "search_knowledge"
    """The tool name presented to the agent."""

    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False
    is_mcp: bool = False

    def __init__(
        self,
        knowledge_bases: list["KnowledgeBase"],
        top_k: int,
        score_threshold: float | None,
    ) -> None:
        """Initialize the search tool.

        Args:
            knowledge_bases (`list[KnowledgeBase]`):
                The knowledge bases the agent may query.
            top_k (`int`):
                Maximum number of chunks returned per call, after
                merging across knowledge bases.
            score_threshold (`float | None`):
                Minimum similarity score; forwarded unchanged to each
                :meth:`KnowledgeBase.search` call.
        """
        # ``ToolBase`` expects a list of *tool* middlewares; this tool
        # has none of its own (the owning ``RAGMiddleware`` is an
        # *agent* middleware, not a tool one).
        super().__init__()
        self._knowledge_bases = knowledge_bases
        self._top_k = top_k
        self._score_threshold = score_threshold
        self.description = self._build_description()
        self.input_schema = self._build_input_schema()

    def _build_description(self) -> str:
        """Build the tool description from the currently equipped
        knowledge bases so the agent has enough context to decide which
        ones to query."""
        lines = [
            "Search the agent's equipped knowledge bases by semantic "
            "similarity and return the most relevant chunks.",
            "",
            "## When to Use",
            "- The user's question may be answered by content stored "
            "in one of the listed knowledge bases (see *Equipped "
            "Knowledge Bases* below).",
            "- You need supporting facts, definitions, or documents "
            "that are unlikely to be in your parametric knowledge.",
            "",
            "## Guidance",
            "- Knowledge base names and descriptions are "
            "user-supplied and may be terse, vague, or unrelated to "
            "the actual contents. When in doubt, try the search — a "
            "single call is cheap and an empty result is informative.",
            "- Phrase `query` as a self-contained statement of what "
            "you want to find. Avoid pronouns or relative time "
            "references — the search is purely semantic and has no "
            "conversational context.",
            "- Set `knowledge_bases` only when the question clearly "
            "matches one or two specific bases; otherwise leave it "
            "unset to search them all.",
            "",
            "## Equipped Knowledge Bases",
        ]
        if self._knowledge_bases:
            lines.append(
                f"The agent is currently equipped with "
                f"{len(self._knowledge_bases)} knowledge base(s):",
            )
            lines.extend(
                f"- **{kb.name}**: {kb.description}"
                for kb in self._knowledge_bases
            )
        else:
            lines.append(
                "No knowledge bases are currently equipped. **Do "
                "not call this tool** — it will return nothing.",
            )
        return "\n".join(lines)

    def _build_input_schema(self) -> dict:
        """Build the JSON Schema, with ``knowledge_bases.items.enum``
        narrowed to the currently equipped knowledge-base names so
        the LLM cannot invent unknown names."""
        schema: dict[str, Any] = _SearchParams.model_json_schema()
        if self._knowledge_bases:
            names = [kb.name for kb in self._knowledge_bases]
            kb_schema = schema["properties"]["knowledge_bases"]
            # Pydantic emits ``Optional[list[str]]`` as
            # ``anyOf: [{type: array, items: ...}, {type: null}]``; we
            # narrow the array branch's items.  Fall back to a direct
            # ``items`` field for the non-optional shape.
            if "items" in kb_schema:
                kb_schema["items"]["enum"] = names
            else:
                for variant in kb_schema.get("anyOf", []):
                    if variant.get("type") == "array" and "items" in variant:
                        variant["items"]["enum"] = names
                        break
        return schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> Any:
        """Allow the engine to handle this read-only search.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                An allow decision — knowledge-base search is read-only.
        """
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Knowledge-base search is read-only.",
        )

    async def call(  # type: ignore[override]
        self,
        query: str,
        knowledge_bases: list[str] | None = None,
    ) -> ToolChunk:
        """Search the selected knowledge bases and return the results
        as content blocks.

        Args:
            query (`str`):
                The natural-language search query.
            knowledge_bases (`list[str] | None`, optional):
                Optional subset of knowledge bases to query, by name.
                ``None`` searches every equipped knowledge base.

        Returns:
            `ToolChunk`:
                The formatted search results, or a notice when
                nothing relevant is found.
        """
        if knowledge_bases is None:
            targets = list(self._knowledge_bases)
        else:
            wanted = set(knowledge_bases)
            targets = [kb for kb in self._knowledge_bases if kb.name in wanted]

        if not targets:
            return ToolChunk(
                content=[TextBlock(text="No relevant content found.")],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )

        try:
            results = await _search_across(
                targets,
                [query],
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("search_knowledge failed.")
            return ToolChunk(
                content=[TextBlock(text=f"Search failed: {e}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        blocks = _format_results(results)
        if not blocks:
            return ToolChunk(
                content=[TextBlock(text="No relevant content found.")],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        return ToolChunk(
            content=blocks,
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


# ---------------------------------------------------------------------
# Shared helpers — used by both the tool (agentic mode) and the
# middleware's static-mode injection path.
# ---------------------------------------------------------------------


async def _search_across(
    knowledge_bases: Sequence["KnowledgeBase"],
    queries: Sequence[str | TextBlock | DataBlock],
    top_k: int,
    score_threshold: float | None,
) -> list["VectorSearchResult"]:
    """Search every knowledge base concurrently and merge the results.

    Each knowledge base handles its own filtering — it silently drops
    :class:`DataBlock` inputs when its embedding model is not
    multimodal — so callers can pass the same query list to every
    knowledge base without per-KB pre-filtering.  Per-KB hits are
    flattened, sorted by descending score, and truncated to ``top_k``.

    .. note::
        Scores from knowledge bases with different embedding models
        are not strictly comparable; this merge sorts by raw score.
        For mixed-embedding deployments where that matters, switch to
        a rank-based fusion (e.g. RRF) — each per-KB
        :meth:`KnowledgeBase.search` already returns ordered results.

    Args:
        knowledge_bases (`list[KnowledgeBase]`):
            The knowledge bases to query.
        queries (`list[str | TextBlock | DataBlock]`):
            The query inputs.
        top_k (`int`):
            Maximum number of results after merging.
        score_threshold (`float | None`):
            Forwarded to each :meth:`KnowledgeBase.search` call.

    Returns:
        `list[VectorSearchResult]`:
            At most ``top_k`` hits across all knowledge bases.
    """
    if not queries or not knowledge_bases:
        return []

    queries_list = list(queries)
    per_kb = await asyncio.gather(
        *(
            kb.search(
                queries=queries_list,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            for kb in knowledge_bases
        ),
    )

    merged = [r for sub in per_kb for r in sub]
    merged.sort(key=lambda r: r.score, reverse=True)
    return merged[:top_k]


def _format_results(
    results: list["VectorSearchResult"],
) -> list[TextBlock | DataBlock]:
    """Render search results as a numbered, cited list of blocks.

    Every result becomes ``[N] (source: ...)`` followed by its chunk
    content (text inlined, multimodal blocks kept as standalone
    :class:`DataBlock` entries).  Adjacent text fragments are merged
    into a single :class:`TextBlock` so downstream consumers see one
    contiguous text block instead of many small ones.

    Args:
        results (`list[VectorSearchResult]`):
            The search results to format.

    Returns:
        `list[TextBlock | DataBlock]`:
            The formatted blocks; empty list when ``results`` is
            empty.
    """
    entries: list[TextBlock | DataBlock] = []
    last = len(results)
    for index, result in enumerate(results, start=1):
        prefix = f"[{index}] (source: {result.chunk.source})\n"
        block = deepcopy(result.chunk.content)
        if isinstance(block, TextBlock):
            block.text = prefix + block.text
            entries.append(block)
        else:
            entries.append(TextBlock(text=prefix))
            entries.append(block)
        if index != last:
            entries.append(TextBlock(text="\n\n"))

    # Coalesce adjacent TextBlocks into one to keep the consumer-facing
    # block list tight.  We build fresh TextBlocks rather than mutating
    # the deep copied ones to avoid aliasing the originals.
    merged: list[TextBlock | DataBlock] = []
    for entry in entries:
        if (
            isinstance(entry, TextBlock)
            and merged
            and isinstance(merged[-1], TextBlock)
        ):
            merged[-1] = TextBlock(text=merged[-1].text + entry.text)
        else:
            merged.append(entry)
    return merged


def _wrap_hint(
    template: str,
    blocks: list[TextBlock | DataBlock],
) -> str | list[TextBlock | DataBlock]:
    """Substitute ``{context}`` in ``template`` with the rendered blocks.

    When every block is a :class:`TextBlock` the result is a single
    ``str`` (cheap, no block overhead).  When the blocks include
    multimodal :class:`DataBlock` content the template is split on
    ``{context}`` and the surrounding text is prepended / appended as
    :class:`TextBlock` items, producing a ``list[TextBlock |
    DataBlock]`` that preserves the binary payloads end-to-end.

    Args:
        template (`str`):
            Wrapper template with a single ``{context}`` placeholder.
        blocks (`list[TextBlock | DataBlock]`):
            The formatted search blocks to wrap.

    Returns:
        `str | list[TextBlock | DataBlock]`:
            A plain string for text-only payloads, or a list of blocks
            when binary payloads are involved.
    """
    if all(isinstance(b, TextBlock) for b in blocks):
        joined = "\n".join(b.text for b in blocks)  # type: ignore[union-attr]
        return template.format(context=joined)

    prefix, _, end = template.partition("{context}")
    wrapped: list[TextBlock | DataBlock] = list(blocks)
    if prefix:
        if isinstance(wrapped[0], TextBlock):
            wrapped[0] = TextBlock(text=prefix + wrapped[0].text)
        else:
            wrapped.insert(0, TextBlock(text=prefix))
    if end:
        if isinstance(wrapped[-1], TextBlock):
            wrapped[-1] = TextBlock(text=wrapped[-1].text + end)
        else:
            wrapped.append(TextBlock(text=end))
    return wrapped


class RAGMiddleware(MiddlewareBase):
    """Middleware that integrates knowledge-base search into the agent.

    Constructed from a list of :class:`~agentscope.rag.KnowledgeBase`
    handles — each carrying its own embedding model, vector store, and
    metadata filter.  The middleware does not own these resources; it
    only orchestrates search against them.

    .. code-block:: python

        # Automatic injection (static mode)
        middleware = RAGMiddleware(
            knowledge_bases=[kb1, kb2],
            search_config=SearchConfig(mode="static"),
        )

        # Agent-driven search (agentic mode, the default)
        middleware = RAGMiddleware(knowledge_bases=[kb1, kb2])

        agent = Agent(..., middlewares=[middleware], ...)
    """

    class Parameters(BaseModel):
        """User-tunable knowledge-base search parameters of
        :class:`RAGMiddleware`.

        The fields here are exactly the keys the hosted service persists
        into ``SessionKnowledgeConfig.parameters`` and the keys
        :class:`RAGMiddleware` accepts as ``search_config``.  Every field
        is annotated with a ``title`` and ``description`` so the front-end
        can render them as labels and tooltips via
        ``model_json_schema()``.
        """

        model_config = ConfigDict(frozen=True)

        mode: Literal["static", "agentic"] = Field(
            default="agentic",
            title="Mode",
            description=(
                "Retrieval is either agentic, letting the Agent decide when "
                "to retrieve, or static, triggering on every user input."
            ),
        )

        top_k: int = Field(
            default=5,
            ge=1,
            le=50,
            title="Top K",
            description=(
                "Maximum number of chunks returned per search, across all "
                "configured knowledge bases."
            ),
        )

        score_threshold: float | None = Field(
            default=None,
            title="Score Threshold",
            description=(
                "Minimum similarity score for a hit to be kept. Leave "
                "empty to disable filtering."
            ),
        )

        emit_hint_event: bool = Field(
            default=True,
            title="Show matched chunks in chat",
            description=(
                "Emit a `HintBlockEvent` in static mode so the front-end "
                "can display the matched snippets to the user."
            ),
        )

        persist_hint: bool = Field(
            default=False,
            title="Persist Hint",
            description=(
                "In `static` mode, keep the injected hint block in the "
                "agent context instead of removing it right after the "
                "model call."
            ),
        )

        hint_template: SkipJsonSchema[str] = Field(
            default=_DEFAULT_HINT_TEMPLATE,
            title="Hint template",
            description=(
                "Template wrapping the formatted search results in static "
                "mode, with a `{context}` placeholder."
            ),
        )

        # ``hint_template`` is intentionally hidden from the JSON Schema
        # exposed to the dock UI: the wrapper text is part of the
        # middleware's prompt contract and exposing it through the dock
        # invites session-by-session prompt drift.  It is still accepted
        # for programmatic use.

        @field_validator("hint_template")
        @classmethod
        def _validate_hint_template(cls, value: str) -> str:
            """Reject templates with anything other than exactly one
            ``{context}`` placeholder — :func:`_wrap_hint` substitutes on
            the first occurrence, so zero placeholders silently drop the
            matched content and multiple placeholders duplicate it."""
            count = value.count("{context}")
            if count != 1:
                raise ValueError(
                    "hint_template must contain exactly one '{context}' "
                    f"placeholder; found {count}.",
                )
            return value

    def __init__(
        self,
        knowledge_bases: list["KnowledgeBase"],
        parameters: "RAGMiddleware.Parameters | None" = None,
    ) -> None:
        """Initialize the RAG middleware.

        Args:
            knowledge_bases (`list[KnowledgeBase]`):
                The knowledge bases this agent searches.
            parameters (`RAGMiddleware.Parameters | None`, optional):
                Search-time knobs (mode, top_k, score threshold, hint
                behaviour).  ``None`` uses the defaults of
                :class:`SearchConfig`.
        """
        self._knowledge_bases = knowledge_bases
        self._parameters = parameters or RAGMiddleware.Parameters()
        # Static-mode reply scratchpad: populated by ``on_reply`` and
        # consumed by ``on_reasoning`` so the auto-search can use the
        # original reply inputs (which are no longer in
        # ``agent.state.context`` by the time ``on_reasoning`` runs in
        # a tool-call loop).  Cleared in ``on_reply``'s finally.
        self._cached_inputs: list[TextBlock | DataBlock] | None = None

    # ------------------------------------------------------------------
    # Agentic mode — expose the search tool
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[ToolBase]:
        """Expose the search tool in ``"agentic"`` mode.

        Returns:
            `list[ToolBase]`:
                A single ``search_knowledge`` tool in ``"agentic"``
                mode; an empty list in ``"static"`` mode.
        """
        if self._parameters.mode == "agentic":
            return [
                _SearchKnowledgeTool(
                    self._knowledge_bases,
                    top_k=self._parameters.top_k,
                    score_threshold=self._parameters.score_threshold,
                ),
            ]
        return []

    # ------------------------------------------------------------------
    # Static mode — capture inputs in on_reply, search in on_reasoning
    # ------------------------------------------------------------------

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Cache reply inputs for the static-mode search.

        ``on_reasoning`` runs *after* the agent has potentially
        consumed the inputs and started its own reply, so it cannot
        recover them from ``agent.state.context`` reliably.  This
        method captures them on entry and clears the cache when the
        reply finishes.

        Args:
            agent (`Agent`):
                The executing agent.  Unused, but part of the
                middleware contract.
            input_kwargs (`dict`):
                Reply input kwargs (``inputs``, plus any extras);
                forwarded unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reply logic.

        Yields:
            `Any`:
                Whatever ``next_handler`` yields.
        """
        inputs = input_kwargs.get("inputs")

        msgs: list[Msg] | None = None
        if isinstance(inputs, Msg):
            msgs = [inputs]
        elif isinstance(inputs, list) and all(
            isinstance(m, Msg) for m in inputs
        ):
            msgs = inputs

        if msgs:
            # Deepcopy because we are about to mutate the first text block of
            # each message to prepend the speaker name — never touch the
            # caller's message objects.
            msgs = deepcopy(msgs)
            blocks: list[TextBlock | DataBlock] = []
            for msg in msgs:
                if not msg.content:
                    continue
                speaker = f"{msg.name}: "
                if isinstance(msg.content[0], TextBlock):
                    msg.content[0].text = speaker + msg.content[0].text
                else:
                    blocks.append(TextBlock(text=speaker))
                blocks.extend(msg.content)

            self._cached_inputs = blocks

        try:
            async for evt in next_handler(**input_kwargs):
                yield evt
        finally:
            self._cached_inputs = None

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject a one-shot RAG hint on the first reasoning step.

        Only active in ``"static"`` mode and only when
        ``agent.state.cur_iter == 0`` — i.e. the first reasoning cycle
        of a reply.  Subsequent reasoning iterations (tool-call
        rounds) skip the search so the agent does not re-embed and
        re-inject for every iteration.

        When ``persist_hint`` is ``False`` (the default) the injected
        block is removed from the context right after the reasoning
        step it participated in — keyed on the block's id so other
        middlewares can append their own blocks to the same carrier
        message without interfering.

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
                An optional :class:`HintBlockEvent` followed by events
                from downstream.
        """
        hint: HintBlock | None = None

        if (
            self._parameters.mode == "static"
            and agent.state.cur_iter == 0
            and self._cached_inputs
        ):
            try:
                results = await _search_across(
                    self._knowledge_bases,
                    self._cached_inputs,
                    top_k=self._parameters.top_k,
                    score_threshold=self._parameters.score_threshold,
                )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Knowledge-base search failed; proceeding without "
                    "matched context.",
                )
                results = []

            blocks = _format_results(results)
            if blocks:
                hint = HintBlock(
                    hint=_wrap_hint(self._parameters.hint_template, blocks),
                    source=_HINT_SOURCE,
                )
                agent.state.append_context(agent.name, [hint])
                if self._parameters.emit_hint_event:
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
            if hint is not None and not self._parameters.persist_hint:
                # Remove the injected block from whichever message
                # ``append_context`` placed it on.  Reverse-scan
                # because that carrier is always the latest message
                # with our ``reply_id``.
                for msg in reversed(agent.state.context):
                    if msg.id != agent.state.reply_id:
                        continue
                    msg.content = [b for b in msg.content if b.id != hint.id]
                    break
