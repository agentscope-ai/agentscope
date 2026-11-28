# -*- coding: utf-8 -*-
"""Unit tests for the :class:`ExternalRetrievalMiddleware` class."""
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.event import HintBlockEvent
from agentscope.message import (
    Msg,
    UserMsg,
)
from agentscope.middleware import ExternalRetrievalMiddleware
from agentscope.middleware._external_retrieval import (
    RetrievalBackend,
    RetrievalResult,
)


_HINT_SOURCE = '{"label": "ExternalRetrieval", "sublabel": ""}'


class _StubBackend(RetrievalBackend):
    """A stub backend returning fixed results."""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self.results = results
        self.calls: list[dict] = []

    async def search(
        self,
        query: str,
        top_k: int = 5,
        **kwargs: Any,
    ) -> list[RetrievalResult]:
        self.calls.append({"query": query, "top_k": top_k, **kwargs})
        return self.results[:top_k]


def _make_agent(
    context: list[Msg] | None = None,
    cur_iter: int = 0,
) -> Any:
    """Build a minimal stand-in for an Agent."""
    msgs: list[Msg] = context if context is not None else []

    def _append_context(name: str, blocks: list) -> None:
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
    """Exhaust an async generator into a list."""
    return [item async for item in generator]


class ExternalRetrievalMiddlewareTest(IsolatedAsyncioTestCase):
    """The test cases for the :class:`ExternalRetrievalMiddleware` class."""

    def _middleware(
        self,
        results: list[RetrievalResult] | None = None,
        **kwargs: Any,
    ) -> ExternalRetrievalMiddleware:
        """Build a middleware with a stub backend."""
        if results is None:
            results = [
                RetrievalResult(
                    content="Use docker-compose up to start services.",
                    source="docker-guide.md",
                    score=0.95,
                ),
            ]
        backend = _StubBackend(results)
        return ExternalRetrievalMiddleware(
            backend=backend,
            top_k=kwargs.get("top_k", 5),
            similarity_threshold=kwargs.get("similarity_threshold"),
            emit_hint_event=kwargs.get("emit_hint_event", True),
            persist_hint=kwargs.get("persist_hint", False),
        )

    async def _run_with_inputs(
        self,
        middleware: ExternalRetrievalMiddleware,
        agent: Any,
        inputs: Msg | list[Msg] | None,
        context_during_reasoning: list[dict] | None = None,
    ) -> list:
        """Drive ``on_reply`` → ``on_reasoning`` end-to-end.

        Mirrors the real agent loop: ``on_reply`` captures the inputs
        in the middleware's scratchpad, then ``on_reasoning`` runs
        (with ``state.cur_iter == 0``) and may inject a hint.  The
        reasoning step yields a sentinel ``"reasoning-event"`` so
        callers can assert event order; if ``context_during_reasoning``
        is provided it is filled with a dump of ``agent.state.context``
        as seen by the innermost reasoning callback.
        """

        async def reasoning_next(**_kwargs: Any) -> AsyncGenerator:
            if context_during_reasoning is not None:
                context_during_reasoning.extend(
                    msg.model_dump() for msg in agent.state.context
                )
            yield "reasoning-event"

        async def reply_next(**_kwargs: Any) -> AsyncGenerator:
            async for evt in middleware.on_reasoning(
                agent=agent,
                input_kwargs={},
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

    async def test_static_mode_injects_hint(self) -> None:
        """Static mode should inject a hint on the first reasoning step."""
        middleware = self._middleware()
        agent = _make_agent(cur_iter=0)
        seen_context: list[dict] = []

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="How to deploy docker?"),
            context_during_reasoning=seen_context,
        )

        # Should emit HintBlockEvent + reasoning event
        self.assertEqual(len(events), 2)
        self.assertIsInstance(events[0], HintBlockEvent)
        self.assertEqual(events[0].source, _HINT_SOURCE)
        self.assertIn("docker-compose up", events[0].hint)
        self.assertEqual(events[1], "reasoning-event")

        # The reasoning callback observed exactly one carrier message
        # with the injected hint block.
        self.assertEqual(len(seen_context), 1)
        carrier = seen_context[0]
        self.assertEqual(carrier["id"], "reply-1")
        self.assertEqual(len(carrier["content"]), 1)

        # Hint should be removed after reasoning (persist_hint=False)
        self.assertEqual(len(agent.state.context), 1)
        self.assertEqual(len(agent.state.context[0].content), 0)

    async def test_static_mode_skips_on_nonzero_iter(self) -> None:
        """Static mode should not inject on subsequent reasoning steps."""
        middleware = self._middleware()
        agent = _make_agent(cur_iter=1)

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="test"),
        )

        # Only reasoning event, no hint
        self.assertEqual(events, ["reasoning-event"])
        self.assertEqual(len(agent.state.context), 0)

    async def test_static_mode_empty_results(self) -> None:
        """No hint injected when backend returns empty results."""
        middleware = self._middleware(results=[])
        agent = _make_agent(cur_iter=0)

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="test"),
        )

        self.assertEqual(events, ["reasoning-event"])
        self.assertEqual(len(agent.state.context), 0)

    async def test_static_mode_backend_error(self) -> None:
        """Backend failure should not break the reasoning flow."""
        backend = _StubBackend([])

        async def _failing_search(*args: Any, **kwargs: Any) -> list:
            raise ConnectionError("service unavailable")

        backend.search = _failing_search  # type: ignore[method-assign]

        middleware = ExternalRetrievalMiddleware(backend=backend)
        agent = _make_agent(cur_iter=0)

        events = await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="test"),
        )

        # Should proceed without hint
        self.assertEqual(events, ["reasoning-event"])

    async def test_static_mode_persist_hint(self) -> None:
        """Hint should remain in context when persist_hint=True."""
        middleware = self._middleware(persist_hint=True)
        agent = _make_agent(cur_iter=0)

        await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="test"),
        )

        # Hint should still be in context
        self.assertEqual(len(agent.state.context), 1)
        carrier = agent.state.context[0]
        self.assertEqual(len(carrier.content), 1)

    async def test_backend_receives_query(self) -> None:
        """Backend should receive the flattened user query."""
        backend = _StubBackend([
            RetrievalResult(content="test", source="doc.md", score=0.9),
        ])
        middleware = ExternalRetrievalMiddleware(backend=backend, top_k=3)
        agent = _make_agent(cur_iter=0)

        await self._run_with_inputs(
            middleware,
            agent,
            UserMsg(name="user", content="docker deployment steps"),
        )

        self.assertEqual(len(backend.calls), 1)
        self.assertIn("docker deployment steps", backend.calls[0]["query"])
        self.assertEqual(backend.calls[0]["top_k"], 3)
