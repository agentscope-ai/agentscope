# -*- coding: utf-8 -*-
"""Unit tests for the RAGMiddleware class."""
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
from agentscope.rag import Chunk, QdrantStore, VectorRecord


class _StubEmbeddingModel:
    """A stub embedding model returning a fixed vector per input."""

    supports_multimodal = False

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


def _make_agent(context: list[Msg] | None = None) -> Any:
    """Build a minimal stand-in for an Agent.

    Args:
        context (`list[Msg] | None`, optional):
            The initial agent context.

    Returns:
        `Any`:
            An object with ``name`` and ``state.context`` /
            ``state.reply_id`` / ``state.session_id``.
    """
    return SimpleNamespace(
        name="assistant",
        state=SimpleNamespace(
            context=context if context is not None else [],
            reply_id="reply-1",
            session_id="session-1",
        ),
    )


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
    """The test cases for the RAGMiddleware class."""

    async def asyncSetUp(self) -> None:
        """Create an in-memory store seeded with one collection."""
        self.store = QdrantStore(location=":memory:")
        await self.store.__aenter__()
        await self.store.create_collection("kb-1", dimensions=3)
        await self.store.insert(
            "kb-1",
            [
                _make_record("Paris is in France.", [1.0, 0.0, 0.0], "doc-1"),
                _make_record("Cats are mammals.", [0.0, 1.0, 0.0], "doc-2"),
            ],
        )
        self.embedding_model = _StubEmbeddingModel([1.0, 0.0, 0.0])

    async def asyncTearDown(self) -> None:
        """Close the store after each test."""
        await self.store.__aexit__(None, None, None)

    def _middleware(self, **kwargs: Any) -> RAGMiddleware:
        """Build a middleware bound to the test store.

        Args:
            **kwargs (`Any`):
                Extra constructor arguments.

        Returns:
            `RAGMiddleware`:
                The middleware under test.
        """
        return RAGMiddleware(
            embedding_model=self.embedding_model,
            vector_store=self.store,
            collections=["kb-1"],
            **kwargs,
        )

    async def _run_reply_then_reasoning(
        self,
        middleware: RAGMiddleware,
        agent: Any,
        inputs: Any,
        context_during_reasoning: list[dict] | None = None,
    ) -> list:
        """Drive the on_reply → on_reasoning sequence like the agent.

        Mimics the real call order: ``on_reply`` runs first (staging
        the hint), its ``next_handler`` appends the input message to
        the context (as ``_reply_impl`` does) and then runs
        ``on_reasoning``, whose ``next_handler`` optionally snapshots
        the context for assertions.

        Args:
            middleware (`RAGMiddleware`):
                The middleware under test.
            agent (`Any`):
                The fake agent.
            inputs (`Any`):
                The reply inputs.
            context_during_reasoning (`list[dict] | None`, optional):
                When provided, receives a dump of the agent context
                as seen by the model call.

        Returns:
            `list`:
                All events yielded by the on_reply chain.
        """

        async def reasoning_next(**_kwargs: Any) -> AsyncGenerator:
            if context_during_reasoning is not None:
                context_during_reasoning.extend(
                    msg.model_dump() for msg in agent.state.context
                )
            yield "reasoning-evt"

        async def reply_next(**kwargs: Any) -> AsyncGenerator:
            msgs = kwargs.get("inputs")
            if isinstance(msgs, Msg):
                agent.state.context.append(msgs)
            elif isinstance(msgs, list):
                agent.state.context.extend(msgs)
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
    # Hint mode
    # ------------------------------------------------------------------

    async def test_hint_one_shot_injection(self) -> None:
        """The hint participates in one reasoning step and is removed
        afterwards (persist_hint=False, default)."""
        middleware = self._middleware(mode="hint", top_k=1)
        agent = _make_agent()
        seen_context: list[dict] = []

        events = await self._run_reply_then_reasoning(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
            context_during_reasoning=seen_context,
        )

        # No HintBlockEvent by default; only downstream events.
        self.assertEqual(events, ["reasoning-evt"])
        # During reasoning the context contains user msg + hint carrier
        self.assertEqual(
            seen_context,
            [
                {
                    "name": "user",
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Where is Paris?",
                            "id": AnyString(),
                        },
                    ],
                    "id": AnyString(),
                    "metadata": {},
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                    "usage": None,
                },
                {
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "hint",
                            "hint": (
                                "The following content is retrieved from "
                                "the knowledge base and may be helpful for "
                                "the current request:\n"
                                "[1] (source: doc-1.txt)\n"
                                "Paris is in France."
                            ),
                            "id": AnyString(),
                            "source": "rag",
                        },
                    ],
                    "id": "reply-1",
                    "metadata": {},
                    "created_at": AnyString(),
                    "finished_at": None,
                    "usage": None,
                },
            ],
        )
        # One-shot: after reasoning the hint carrier is gone, only the
        # user message remains.
        self.assertEqual(
            [msg.model_dump() for msg in agent.state.context],
            [seen_context[0]],
        )

    async def test_hint_persistent_injection(self) -> None:
        """persist_hint=True keeps the hint in the context."""
        middleware = self._middleware(
            mode="hint",
            top_k=1,
            persist_hint=True,
        )
        agent = _make_agent()
        seen_context: list[dict] = []

        await self._run_reply_then_reasoning(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
            context_during_reasoning=seen_context,
        )

        self.assertEqual(
            [msg.model_dump() for msg in agent.state.context],
            seen_context,
        )

    async def test_hint_event_emission(self) -> None:
        """emit_hint_event=True yields one HintBlockEvent."""
        middleware = self._middleware(
            mode="hint",
            top_k=1,
            emit_hint_event=True,
        )
        agent = _make_agent()

        events = await self._run_reply_then_reasoning(
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
                "source": "rag",
                "hint": (
                    "The following content is retrieved from "
                    "the knowledge base and may be helpful for "
                    "the current request:\n"
                    "[1] (source: doc-1.txt)\n"
                    "Paris is in France."
                ),
                "id": AnyString(),
                "created_at": AnyString(),
                "metadata": {},
            },
        )
        self.assertEqual(events[1], "reasoning-evt")

    async def test_hint_skips_event_inputs(self) -> None:
        """Non-message inputs (resumption events / None) skip
        retrieval entirely."""
        middleware = self._middleware(mode="hint")
        agent = _make_agent()

        events = await self._run_reply_then_reasoning(
            middleware,
            agent,
            None,
        )

        self.assertEqual(events, ["reasoning-evt"])
        self.assertEqual(self.embedding_model.calls, [])
        self.assertEqual(agent.state.context, [])

    async def test_multimodal_query_extraction(self) -> None:
        """DataBlocks become extra queries when the embedding model
        supports multimodal inputs."""
        self.embedding_model.supports_multimodal = True
        middleware = self._middleware(mode="hint", top_k=1)
        agent = _make_agent()
        data_block = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )

        await self._run_reply_then_reasoning(
            middleware,
            agent,
            UserMsg(
                name="user",
                content=[TextBlock(text="What is this?"), data_block],
            ),
        )

        self.assertEqual(
            self.embedding_model.calls,
            [["What is this?", data_block]],
        )

    # ------------------------------------------------------------------
    # Agentic mode
    # ------------------------------------------------------------------

    async def test_agentic_list_tools(self) -> None:
        """Agentic mode exposes the retrieval tool; hint mode none."""
        agentic_tools = await self._middleware(mode="agentic").list_tools()
        hint_tools = await self._middleware(mode="hint").list_tools()

        self.assertEqual(
            [tool.name for tool in agentic_tools],
            ["retrieve_knowledge"],
        )
        self.assertEqual(hint_tools, [])

    async def test_agentic_no_auto_injection(self) -> None:
        """Agentic mode never retrieves or injects automatically."""
        middleware = self._middleware(mode="agentic")
        agent = _make_agent()

        events = await self._run_reply_then_reasoning(
            middleware,
            agent,
            UserMsg(name="user", content="Where is Paris?"),
        )

        self.assertEqual(events, ["reasoning-evt"])
        self.assertEqual(self.embedding_model.calls, [])
        self.assertEqual(len(agent.state.context), 1)

    async def test_retrieve_knowledge_tool_call(self) -> None:
        """The tool returns formatted results for a query."""
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
                            "[1] (source: doc-1.txt)\n" "Paris is in France."
                        ),
                        "id": AnyString(),
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )
