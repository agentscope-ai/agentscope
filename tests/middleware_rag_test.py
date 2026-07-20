# -*- coding: utf-8 -*-
"""Unit tests for the :class:`RAGMiddleware` class."""
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.embedding import EmbeddingResponse
from agentscope.event import EventType, HintBlockEvent
from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    UserMsg,
)
from agentscope.middleware import RAGMiddleware
from agentscope.rag import Chunk, KnowledgeBase, QdrantStore, VectorRecord


_HINT_SOURCE = '{"label": "KnowledgeBase", "sublabel": ""}'

_EXPECTED_HINT = (
    "<system-reminder>The following content is retrieved from the "
    "knowledge base(s) and may be helpful for the current "
    "request:\n"
    "<content>[1] (source: doc-1.txt)\n"
    "Paris is in France.</content></system-reminder>"
)


class _StubEmbeddingModel:
    """A stub embedding model returning a fixed vector per input."""

    supports_multimodal = False
    dimensions = 3

    def __init__(self, vector: list[float]) -> None:
        """Initialize the stub.

        Args:
            vector (`list[float]`):
                The vector returned for every input.
        """
        self.vector = vector
        self.calls: list[list] = []

    async def __call__(self, inputs: list) -> EmbeddingResponse:
        """Return the fixed vector for each input.

        Args:
            inputs (`list`):
                The input queries.

        Returns:
            `EmbeddingResponse`:
                The response with one fixed vector per input.
        """
        self.calls.append(inputs)
        return EmbeddingResponse(embeddings=[self.vector] * len(inputs))


def _make_record(
    text: str,
    vector: list[float],
    document_id: str,
) -> VectorRecord:
    """Build a VectorRecord for testing.

    Args:
        text (`str`):
            The chunk text content.
        vector (`list[float]`):
            The embedding vector.
        document_id (`str`):
            The ID of the source document the record belongs to.

    Returns:
        `VectorRecord`:
            The constructed record.
    """
    return VectorRecord(
        vector=vector,
        document_id=document_id,
        chunk=Chunk(
            content=TextBlock(text=text),
            source=f"{document_id}.txt",
            chunk_index=0,
            total_chunks=1,
        ),
    )


def _make_agent(
    context: list[Msg] | None = None,
    cur_iter: int = 0,
) -> Any:
    """Build a minimal stand-in for an Agent.

    Args:
        context (`list[Msg] | None`, optional):
            The initial agent context.
        cur_iter (`int`, defaults to ``0``):
            Value for ``state.cur_iter``; the middleware only searches
            on the first reasoning step (``0``).

    Returns:
        `Any`:
            An object with ``name`` and ``state.context`` /
            ``state.reply_id`` / ``state.session_id`` /
            ``state.cur_iter`` / ``state.append_context``.
    """

    msgs: list[Msg] = context if context is not None else []

    def _append_context(name: str, blocks: list) -> None:
        # Always append a new assistant carrier message keyed on the
        # static reply_id used in these tests.  Mirrors the real
        # ``AgentState.append_context`` for the purposes of the
        # middleware's reverse-scan removal logic.
        carrier = Msg(name=name, role="assistant", content=blocks)
        carrier.id = "reply-1"
        msgs.append(carrier)

    state = SimpleNamespace(
        context=msgs,
        reply_id="reply-1",
        session_id="session-1",
        cur_iter=cur_iter,
        append_context=_append_context,
    )
    return SimpleNamespace(name="assistant", state=state)


async def _drain(generator: AsyncGenerator) -> list:
    """Exhaust an async generator into a list.

    Args:
        generator (`AsyncGenerator`):
            The generator to drain.

    Returns:
        `list`:
            All yielded items.
    """
    return [item async for item in generator]


class RAGMiddlewareTest(IsolatedAsyncioTestCase):
    """The test cases for the :class:`RAGMiddleware` class."""

    async def asyncSetUp(self) -> None:
        """Create an in-memory store seeded with one collection +
        one :class:`KnowledgeBase` handle wired to it."""
        self._exit_stack = AsyncExitStack()
        self.store = await self._exit_stack.enter_async_context(
            QdrantStore(location=":memory:"),
        )
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert(
            "kb-1",
            [
                _make_record("Paris is in France.", [1.0, 0.0, 0.0], "doc-1"),
                _make_record("Cats are mammals.", [0.0, 1.0, 0.0], "doc-2"),
            ],
        )
        self.embedding_model = _StubEmbeddingModel([1.0, 0.0, 0.0])
        # Build the KnowledgeBase handle once; tests share it.  The
        # collection already exists, so ``ensure_collection`` will
        # short-circuit on first use.
        self.knowledge = KnowledgeBase(
            name="paris-kb",
            description="Trivia about Paris and cats.",
            embedding_model=self.embedding_model,
            vector_store=self.store,
            collection="kb-1",
        )

    async def asyncTearDown(self) -> None:
        """Close the store after each test."""
        await self._exit_stack.aclose()

    def _middleware(
        self,
        knowledges: list[KnowledgeBase] | None = None,
        **kwargs: Any,
    ) -> RAGMiddleware:
        """Build a middleware bound to ``self.knowledge`` with a
        :class:`SearchConfig` assembled from ``kwargs``.

        Args:
            knowledges (`list[KnowledgeBase] | None`, optional):
                Override the bound knowledge bases.  Defaults to
                ``[self.knowledge]``.
            **kwargs (`Any`):
                Forwarded to :class:`SearchConfig` (e.g. ``mode``,
                ``top_k``, ``score_threshold``, ``emit_hint_event``,
                ``persist_hint``).

        Returns:
            `RAGMiddleware`:
                The middleware under test.
        """
        return RAGMiddleware(
            knowledge_bases=knowledges
            if knowledges is not None
            else [
                self.knowledge,
            ],
            parameters=RAGMiddleware.Parameters(**kwargs),
        )

    async def _run_with_inputs(
        self,
        middleware: RAGMiddleware,
        agent: Any,
        inputs: Msg | list[Msg] | None,
        context_during_reasoning: list[dict] | None = None,
    ) -> list:
        """Drive ``on_reply`` → ``on_reasoning`` end-to-end.

        Mirrors the real agent loop: ``on_reply`` captures the inputs
        in the middleware's scratchpad, then ``on_reasoning`` runs
        (with ``state.cur_iter == 0``) and may inject a hint.  The
        reasoning step yields a sentinel ``"reasoning-evt"`` so callers
        can assert event order; if ``context_during_reasoning`` is
        provided it is filled with a dump of ``agent.state.context`` as
        seen by the innermost reasoning callback.

        Args:
            middleware (`RAGMiddleware`):
                The middleware under test.
            agent (`Any`):
                The fake agent.
            inputs (`Msg | list[Msg] | None`):
                The reply inputs to pass through ``on_reply``.
            context_during_reasoning (`list[dict] | None`, optional):
                When provided, receives a dump of the agent context as
                seen by the wrapped (innermost) reasoning call.

        Returns:
            `list`:
                All events yielded by the on_reply → on_reasoning chain.
        """

        async def reasoning_next(**_kwargs: Any) -> AsyncGenerator:
            if context_during_reasoning is not None:
                context_during_reasoning.extend(
                    msg.model_dump() for msg in agent.state.context
                )
            yield "reasoning-evt"

        async def reply_next(**_kwargs: Any) -> AsyncGenerator:
            # The reply branch drives the reasoning branch — same as
            # the real composition.
            async for evt in middleware.on_reasoning(
                agent=agent,
                input_kwargs={"tool_choice": None},
                next_handler=reasoning_next,
            ):
                yield evt

        return await _drain(
            middleware.on_reply(
                agent=agent,
                input_kwargs={"inputs": inputs},
                next_handler=reply_next,
            ),
        )

    # ------------------------------------------------------------------
    # Static mode (auto-injection)
    # ------------------------------------------------------------------

    async def test_static_one_shot_injection(self) -> None:
        """The hint participates in one reasoning step and is removed
        afterwards (``persist_hint=False``, default)."""
        middleware = self._middleware(
            mode="static",
            top_k=1,
            emit_hint_event=False,
        )
        agent = _make_agent()
        seen_context: list[dict] = []

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
            context_during_reasoning=seen_context,
        )

        # No HintBlockEvent (emit_hint_event=False); only downstream
        # events.
        self.assertEqual(events, ["reasoning-evt"])

        # The reasoning callback observed exactly one carrier message
        # holding the injected hint block.
        self.assertEqual(len(seen_context), 1)
        carrier = seen_context[0]
        self.assertEqual(carrier["role"], "assistant")
        self.assertEqual(carrier["id"], "reply-1")
        self.assertEqual(len(carrier["content"]), 1)
        block = carrier["content"][0]
        self.assertEqual(block["type"], "hint")
        self.assertEqual(block["source"], _HINT_SOURCE)
        self.assertEqual(block["hint"], _EXPECTED_HINT)

        # One-shot: after on_reasoning unwinds, the carrier is emptied.
        post = [msg.model_dump() for msg in agent.state.context]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["content"], [])

    async def test_static_persistent_injection(self) -> None:
        """``persist_hint=True`` keeps the hint in the context."""
        middleware = self._middleware(
            mode="static",
            top_k=1,
            persist_hint=True,
            emit_hint_event=False,
        )
        agent = _make_agent()
        seen_context: list[dict] = []

        await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
            context_during_reasoning=seen_context,
        )

        self.assertEqual(
            [msg.model_dump() for msg in agent.state.context],
            seen_context,
        )

    async def test_static_event_emission(self) -> None:
        """``emit_hint_event=True`` yields one :class:`HintBlockEvent`."""
        middleware = self._middleware(
            mode="static",
            top_k=1,
            emit_hint_event=True,
        )
        agent = _make_agent()

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
        )

        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], HintBlockEvent)
        self.assertEqual(
            events[0].model_dump(),
            {
                "type": EventType.HINT_BLOCK,
                "reply_id": "reply-1",
                "block_id": AnyString(),
                "source": _HINT_SOURCE,
                "hint": _EXPECTED_HINT,
                "id": AnyString(),
                "created_at": AnyString(),
                "metadata": {},
            },
        )
        self.assertEqual(events[1], "reasoning-evt")

    async def test_static_skips_event_inputs(self) -> None:
        """Non-message inputs (resumption events / ``None``) skip the
        search entirely."""
        middleware = self._middleware(mode="static")
        agent = _make_agent()

        events = await self._run_with_inputs(middleware, agent, None)

        self.assertEqual(events, ["reasoning-evt"])
        self.assertEqual(self.embedding_model.calls, [])
        self.assertEqual(agent.state.context, [])

    async def test_multimodal_query_extraction(self) -> None:
        """DataBlocks reach the embedding model when it declares
        ``supports_multimodal``."""
        self.embedding_model.supports_multimodal = True
        middleware = self._middleware(
            mode="static",
            top_k=1,
            emit_hint_event=False,
        )
        agent = _make_agent()
        data_block = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )

        await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(
                name="user",
                content=[TextBlock(text="What is this?"), data_block],
            ),
        )

        # The query path prepends ``{name}: `` to the first text
        # block; the data block is passed through verbatim.
        self.assertEqual(len(self.embedding_model.calls), 1)
        query = self.embedding_model.calls[0]
        self.assertEqual(len(query), 2)
        self.assertEqual(query[0].text, "user: What is this?")
        self.assertEqual(query[1], data_block)

    async def test_multimodal_blocks_dropped_for_text_only_model(
        self,
    ) -> None:
        """A text-only embedding model silently drops DataBlock queries
        (no exception, no crash)."""
        middleware = self._middleware(
            mode="static",
            top_k=1,
            emit_hint_event=False,
        )
        agent = _make_agent()
        data_block = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )

        await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(
                name="user",
                content=[TextBlock(text="What is this?"), data_block],
            ),
        )

        # ``KnowledgeBase.search`` strips the DataBlock when the bound
        # embedding model isn't multimodal — the model only saw text.
        self.assertEqual(len(self.embedding_model.calls), 1)
        for item in self.embedding_model.calls[0]:
            self.assertNotIsInstance(item, DataBlock)

    # ------------------------------------------------------------------
    # Agentic mode (tool exposure)
    # ------------------------------------------------------------------

    async def test_agentic_list_tools(self) -> None:
        """Agentic mode exposes the search tool; static mode none."""
        agentic_tools = await self._middleware(mode="agentic").list_tools()
        static_tools = await self._middleware(mode="static").list_tools()

        self.assertEqual(
            [tool.name for tool in agentic_tools],
            ["search_knowledge"],
        )
        self.assertEqual(static_tools, [])

    async def test_agentic_no_auto_injection(self) -> None:
        """Agentic mode never searches or injects automatically."""
        middleware = self._middleware(mode="agentic")
        agent = _make_agent()

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
        )

        self.assertEqual(events, ["reasoning-evt"])
        self.assertEqual(self.embedding_model.calls, [])
        self.assertEqual(agent.state.context, [])

    async def test_search_knowledge_tool_call(self) -> None:
        """The tool returns a formatted ``ToolChunk`` for a query.

        ``_SearchKnowledgeTool.call`` is a regular async function (not
        an async generator), so ``ToolBase.__call__`` awaits it and
        returns the single ``ToolChunk`` directly.
        """
        middleware = self._middleware(mode="agentic", top_k=1)
        tool = (await middleware.list_tools())[0]

        chunk = await tool(query="Where is Paris?")

        self.assertEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "[1] (source: doc-1.txt)\nParis is in France."
                        ),
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_search_knowledge_tool_input_schema_enum(self) -> None:
        """The tool's ``input_schema`` narrows ``knowledge_bases.items``
        to the equipped KB names."""
        middleware = self._middleware(mode="agentic")
        tool = (await middleware.list_tools())[0]

        schema = tool.input_schema
        kb_schema = schema["properties"]["knowledge_bases"]
        # Pydantic emits Optional[list[str]] as anyOf; pick the array
        # branch.
        array_variant = next(
            v for v in kb_schema["anyOf"] if v.get("type") == "array"
        )
        self.assertEqual(array_variant["items"]["enum"], ["paris-kb"])

    async def test_search_knowledge_tool_filters_by_name(self) -> None:
        """Passing ``knowledge_bases=[<unknown>]`` returns the
        ``"No relevant content found."`` notice without touching the
        embedding model."""
        middleware = self._middleware(mode="agentic", top_k=1)
        tool = (await middleware.list_tools())[0]

        chunk = await tool(
            query="Where is Paris?",
            knowledge_bases=["does-not-exist"],
        )

        self.assertEqual(
            [b["text"] for b in chunk.model_dump()["content"]],
            ["No relevant content found."],
        )
        self.assertEqual(self.embedding_model.calls, [])

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    async def test_hint_template_must_have_context_placeholder(self) -> None:
        """:class:`SearchConfig` rejects a template without exactly one
        ``{context}``."""
        with self.assertRaises(ValueError):
            RAGMiddleware.Parameters(hint_template="no placeholder here")
        with self.assertRaises(ValueError):
            RAGMiddleware.Parameters(hint_template="{context} twice {context}")
        # Exactly one placeholder is fine.
        RAGMiddleware.Parameters(hint_template="wrapped: {context}.")
