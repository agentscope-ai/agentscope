# -*- coding: utf-8 -*-
"""External retrieval middleware for automatic knowledge-base injection.

This module provides :class:`ExternalRetrievalMiddleware`, which mirrors
:class:`~agentscope.middleware.RAGMiddleware`'s ``"static"`` mode but
backed by external retrieval services (RAGFlow, Dify, etc.) instead of
local :class:`~agentscope.rag.KnowledgeBase` instances.

The middleware intercepts the first reasoning step of each reply, calls
the configured backend with the user's input, and injects matched chunks
as a one-shot :class:`~agentscope.message.HintBlock`.  The LLM never
decides "should I search" — retrieval is automatic and configuration is
locked at construction time.
"""
from typing import AsyncGenerator, Callable, Sequence, TYPE_CHECKING

from ._backend import RetrievalBackend, RetrievalResult
from ..._logging import logger
from ...event import HintBlockEvent
from ...message import (
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
)
from .._base import MiddlewareBase

if TYPE_CHECKING:
    from ...agent import Agent


_DEFAULT_HINT_TEMPLATE = (
    "<system-reminder>The following content is retrieved from the "
    "knowledge base and may be helpful for the current request:\n"
    "<content>{context}</content></system-reminder>"
)


def _format_results(
    results: Sequence[RetrievalResult],
) -> list[TextBlock | DataBlock]:
    """Render retrieval results as a numbered, cited list of blocks.

    Args:
        results (`Sequence[RetrievalResult]`):
            The retrieval results to format.

    Returns:
        `list[TextBlock | DataBlock]`:
            Formatted blocks; empty list when ``results`` is empty.
    """
    if not results:
        return []

    lines: list[str] = []
    for index, result in enumerate(results, start=1):
        source = result.source or "unknown"
        lines.append(f"[{index}] (source: {source})\n{result.content}")

    # Coalesce into a single TextBlock for static-mode injection
    return [TextBlock(text="\n\n".join(lines))]


def _wrap_hint(
    template: str,
    blocks: list[TextBlock | DataBlock],
) -> str | list[TextBlock | DataBlock]:
    """Substitute ``{context}`` in ``template`` with rendered blocks.

    Args:
        template (`str`):
            Wrapper template with a single ``{context}`` placeholder.
        blocks (`list[TextBlock | DataBlock]`):
            The formatted blocks to wrap.

    Returns:
        `str | list[TextBlock | DataBlock]`:
            Wrapped hint content.
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


class ExternalRetrievalMiddleware(MiddlewareBase):
    """Middleware that auto-injects external retrieval results.

    Configuration (backend, dataset ids, top_k, thresholds) is locked
    at construction time — the LLM never sees or controls these.

    .. code-block:: python

        backend = RAGFlowRetrievalBackend(
            base_url="...",
            api_key="...",
            dataset_ids=["..."],
        )
        middleware = ExternalRetrievalMiddleware(
            backend=backend,
            top_k=5,
            similarity_threshold=0.2,
        )
        agent = Agent(..., middlewares=[middleware], ...)
    """

    def __init__(
        self,
        backend: RetrievalBackend,
        top_k: int = 5,
        similarity_threshold: float | None = None,
        hint_template: str = _DEFAULT_HINT_TEMPLATE,
        emit_hint_event: bool = True,
        persist_hint: bool = False,
    ) -> None:
        """Initialize the external retrieval middleware.

        Args:
            backend (`RetrievalBackend`):
                The external retrieval backend to query.
            top_k (`int`, defaults to ``5``):
                Maximum number of chunks returned per search.
            similarity_threshold (`float | None`, optional):
                Minimum similarity score; forwarded to the backend.
            hint_template (`str`, optional):
                Template wrapping the formatted results, with a
                ``{context}`` placeholder.
            emit_hint_event (`bool`, defaults to ``True``):
                Emit a :class:`HintBlockEvent` so the front-end can
                display matched snippets.
            persist_hint (`bool`, defaults to ``False``):
                Keep the injected hint in context after the model call.
        """
        self._backend = backend
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold
        self._hint_template = hint_template
        self._emit_hint_event = emit_hint_event
        self._persist_hint = persist_hint
        # Scratchpad for static-mode injection (same pattern as RAGMiddleware)
        self._cached_inputs: list[TextBlock | DataBlock] | None = None

    async def get_middleware_key(self) -> str:
        """Return a unique key for this middleware instance."""
        return f"ExternalRetrievalMiddleware:{type(self._backend).__name__}"

    # ------------------------------------------------------------------
    # Capture user inputs on reply entry
    # ------------------------------------------------------------------

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Cache reply inputs for the static-mode search.

        Args:
            agent (`Agent`):
                The executing agent.
            input_kwargs (`dict`):
                Reply input kwargs.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reply logic.

        Yields:
            `Any`:
                Events from downstream.
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
            blocks: list[TextBlock | DataBlock] = []
            for msg in msgs:
                if not msg.content:
                    continue
                speaker = f"{msg.name}: "
                first = msg.content[0]
                if isinstance(first, TextBlock):
                    blocks.append(TextBlock(text=speaker + first.text))
                else:
                    blocks.append(TextBlock(text=speaker))
                blocks.extend(msg.content[1:] if len(msg.content) > 1 else [])
            self._cached_inputs = blocks

        try:
            async for evt in next_handler(**input_kwargs):
                yield evt
        finally:
            self._cached_inputs = None

    # ------------------------------------------------------------------
    # Inject retrieval results on first reasoning step
    # ------------------------------------------------------------------

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject a one-shot retrieval hint on the first reasoning step.

        Args:
            agent (`Agent`):
                The executing agent.
            input_kwargs (`dict`):
                Reasoning input kwargs.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reasoning logic.

        Yields:
            `Any`:
                Optional :class:`HintBlockEvent` followed by downstream events.
        """
        hint: HintBlock | None = None

        if agent.state.cur_iter == 0 and self._cached_inputs:
            # Flatten text blocks into a single query string
            query_parts: list[str] = []
            for block in self._cached_inputs:
                if isinstance(block, TextBlock):
                    query_parts.append(block.text)
            query = "\n".join(query_parts).strip()

            if query:
                try:
                    results = await self._backend.search(
                        query=query,
                        top_k=self._top_k,
                        similarity_threshold=self._similarity_threshold,
                    )
                except Exception:
                    logger.exception(
                        "External retrieval search failed; proceeding "
                        "without matched context.",
                    )
                    results = []

                blocks = _format_results(results)
                if blocks:
                    hint = HintBlock(
                        hint=_wrap_hint(self._hint_template, blocks),
                        source='{"label": "ExternalRetrieval", "sublabel": ""}',
                    )
                    agent.state.append_context(agent.name, [hint])
                    if self._emit_hint_event:
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
                # Remove the injected block from the carrier message
                for msg in reversed(agent.state.context):
                    if msg.id != agent.state.reply_id:
                        continue
                    msg.content = [
                        b for b in msg.content if b.id != hint.id
                    ]
                    break
