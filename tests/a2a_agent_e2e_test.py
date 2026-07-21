# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position
"""Local HTTP/SSE end-to-end test for A2AAgent."""
from __future__ import annotations

import asyncio
import socket
from unittest import IsolatedAsyncioTestCase

import pytest

pytest.importorskip("a2a", reason="A2A E2E test requires the a2a extra")

import httpx
import uvicorn
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import TransportProtocol
from starlette.applications import Starlette

from agentscope.agent import A2AAgent
from agentscope.event import CustomEvent, TextBlockDeltaEvent
from agentscope.message import UserMsg


class _StatefulExecutor(AgentExecutor):
    """Deterministic remote agent that records user turns by context ID."""

    def __init__(self) -> None:
        self._history: dict[str, list[str]] = {}

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Stream a two-chunk answer through the official server SDK."""
        if context.task_id is None or context.context_id is None:
            raise RuntimeError("A2A server did not assign Task/context IDs.")

        updater = TaskUpdater(
            event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            ),
        )
        await updater.start_work()

        turns = self._history.setdefault(context.context_id, [])
        turns.append(context.get_user_input())
        answer = f"turn={len(turns)}; users={' | '.join(turns)}"
        split_at = max(1, len(answer) // 2)
        artifact_id = f"{context.task_id}-answer"
        await updater.add_artifact(
            [Part(text=answer[:split_at])],
            artifact_id=artifact_id,
            append=False,
            last_chunk=False,
        )
        await updater.add_artifact(
            [Part(text=answer[split_at:])],
            artifact_id=artifact_id,
            append=True,
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Mark the test Task as canceled."""
        if context.task_id is None or context.context_id is None:
            raise RuntimeError("Cannot cancel a Task without IDs.")
        await TaskUpdater(
            event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        ).cancel()


def _create_app(base_url: str) -> Starlette:
    """Create the deterministic local A2A 1.0 harness."""
    card = AgentCard(
        name="Local E2E Harness",
        description="Deterministic A2AAgent end-to-end test server.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="chat",
                name="Chat",
                description="Return deterministic multi-turn responses.",
                tags=["test"],
            ),
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=_StatefulExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, rpc_url="/"),
        ],
    )


class A2AAgentE2ETest(IsolatedAsyncioTestCase):
    """Exercise Agent Card resolution, JSON-RPC/SSE, and context reuse."""

    async def test_streaming_and_multi_turn_context(self) -> None:
        """Use a real localhost transport with no model or external API."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(_create_app(base_url), log_level="warning"),
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))

        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(server.started, "local A2A harness did not start")

            async with httpx.AsyncClient(trust_env=False) as resolver_client:
                card = await A2ACardResolver(
                    httpx_client=resolver_client,
                    base_url=base_url,
                ).get_agent_card()

            transport_client = httpx.AsyncClient(trust_env=False)
            client = ClientFactory(
                ClientConfig(
                    streaming=True,
                    polling=False,
                    httpx_client=transport_client,
                    supported_protocol_bindings=[TransportProtocol.JSONRPC],
                ),
            ).create(card)
            async with A2AAgent(card, client=client) as agent:
                deltas: list[str] = []
                states: list[str] = []
                async for event in agent.reply_stream(
                    UserMsg(name="user", content="FIRST"),
                ):
                    if isinstance(event, TextBlockDeltaEvent):
                        deltas.append(event.delta)
                    elif isinstance(event, CustomEvent):
                        states.append(event.value["task_state"])

                first_context_id = agent.context_id
                self.assertTrue(first_context_id)
                self.assertEqual("".join(deltas), "turn=1; users=FIRST")
                self.assertIn("TASK_STATE_WORKING", states)

                reply = await agent.reply(
                    UserMsg(name="user", content="SECOND"),
                )
                self.assertEqual(agent.context_id, first_context_id)
                self.assertEqual(
                    reply.get_text_content(),
                    "turn=2; users=FIRST | SECOND",
                )
                self.assertEqual(agent.task_state, "TASK_STATE_COMPLETED")
        finally:
            server.should_exit = True
            await server_task


if __name__ == "__main__":
    import unittest

    unittest.main()
