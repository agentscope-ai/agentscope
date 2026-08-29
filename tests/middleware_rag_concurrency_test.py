# -*- coding: utf-8 -*-
"""Concurrency regression tests for :class:`RAGMiddleware`."""
import asyncio
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.message import Msg, TextBlock
from agentscope.middleware import RAGMiddleware
from agentscope.middleware import _rag as rag_module


class RAGMiddlewareConcurrencyTest(IsolatedAsyncioTestCase):
    """Verify static RAG inputs are isolated per concurrent reply."""

    async def test_shared_middleware_keeps_queries_task_local(self) -> None:
        """One reply's cleanup cannot overwrite another reply's query."""
        middleware = RAGMiddleware(
            knowledge_bases=[],
            parameters=RAGMiddleware.Parameters(
                mode="static",
                emit_hint_event=False,
            ),
        )
        entered_a = asyncio.Event()
        entered_b = asyncio.Event()
        resume_a = asyncio.Event()
        resume_b = asyncio.Event()
        searched_queries: list[list[str]] = []

        async def fake_search_across(
            knowledge_bases: list[Any],
            queries: list[TextBlock],
            top_k: int,
            score_threshold: float | None,
        ) -> list[Any]:
            del knowledge_bases, top_k, score_threshold
            searched_queries.append([block.text for block in queries])
            return []

        async def run_reply(
            query: str,
            entered: asyncio.Event,
            resume: asyncio.Event,
        ) -> None:
            agent = SimpleNamespace(
                name="assistant",
                state=SimpleNamespace(cur_iter=0),
            )

            async def reasoning_next(**_kwargs: Any) -> AsyncGenerator:
                yield "reasoning-evt"

            async def reply_next(**_kwargs: Any) -> AsyncGenerator:
                entered.set()
                await resume.wait()
                async for event in middleware.on_reasoning(
                    agent=agent,
                    input_kwargs={},
                    next_handler=reasoning_next,
                ):
                    yield event

            async for _ in middleware.on_reply(
                agent=agent,
                input_kwargs={
                    "inputs": Msg(name="user", content=query, role="user"),
                },
                next_handler=reply_next,
            ):
                pass

        with patch.object(
            rag_module,
            "_search_across",
            side_effect=fake_search_across,
        ):
            task_a = asyncio.create_task(
                run_reply("query-a", entered_a, resume_a),
            )
            await entered_a.wait()
            task_b = asyncio.create_task(
                run_reply("query-b", entered_b, resume_b),
            )
            await entered_b.wait()

            # A resumes after B enters.  The original instance field would
            # make A search query-b, then clear B's pending query on exit.
            resume_a.set()
            await task_a
            resume_b.set()
            await task_b

        self.assertEqual(
            searched_queries,
            [["user: query-a"], ["user: query-b"]],
        )
