# -*- coding: utf-8 -*-
"""Exercise ReMe continuation write-back through the public Agent runtime.

Only the model transport and ReMe backend are deterministic local fixtures.
Agent.reply, permissions, context updates, and middleware hooks run normally.
"""
import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable
from unittest import mock
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, AnyValue, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.message import UserMsg
from agentscope.middleware import MiddlewareBase, ReMeMiddleware
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class _LocalModel(MockModel):
    """Yield like an async transport so background retrieval runs reliably."""

    async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
        """Run the deterministic model after allowing retrieval to start."""
        await asyncio.sleep(0)
        return await super()._call_api(*args, **kwargs)


class _MetadataLockMiddleware(MiddlewareBase):
    """Put a noncopyable local object in the public assistant metadata."""

    def __init__(self, enabled: bool) -> None:
        """Enable deterministic metadata injection for selected invocations."""
        self.enabled = enabled

    async def on_reasoning(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject after the real reasoning handler has saved its message."""
        async for item in next_handler(**input_kwargs):
            yield item
        if self.enabled:
            agent.state.context[-1].metadata["local_lock"] = threading.Lock()


class _RecordingApp:
    """Capture immutable backend payloads without constructing a ReMe app."""

    def __init__(self) -> None:
        """Initialize recorded jobs and a bounded write-failure fixture."""
        self.jobs: list[dict] = []
        self.fail_writes = 0
        self.reject_writes = 0
        self.memories: list[str] = []

    async def start(self) -> None:
        """Start the local recording boundary."""

    async def close(self) -> None:
        """Close the local recording boundary."""

    async def run_job(self, name: str, **kwargs: Any) -> SimpleNamespace:
        """Record complete payloads, optionally failing a write attempt."""
        self.jobs.append({"name": name, **deepcopy(kwargs)})
        if name == "auto_memory" and self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("deterministic write failure")
        if name == "auto_memory" and self.reject_writes:
            self.reject_writes -= 1
            return SimpleNamespace(
                success=False,
                answer="deterministic unsuccessful response",
                metadata={},
            )
        return SimpleNamespace(
            success=True,
            answer="recorded",
            metadata={"results": [{"text": text} for text in self.memories]},
        )

    @property
    def writes(self) -> list[dict]:
        """Return complete write attempts, including failed attempts."""
        return [
            {"session_id": job["session_id"], "messages": job["messages"]}
            for job in self.jobs
            if job["name"] == "auto_memory"
        ]

    @property
    def searches(self) -> list[dict]:
        """Return complete search payloads."""
        return [
            {"query": job["query"], "limit": job["limit"]}
            for job in self.jobs
            if job["name"] == "search"
        ]


class _LocalTool(ToolBase):
    """Echo locally after confirmation, or require an external result."""

    name: str = "fixture_echo"
    description: str = "Echo a deterministic local fixture value."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    def __init__(self) -> None:
        """Initialize the execution counter."""
        super().__init__()
        self.calls = 0

    async def check_permissions(
        self,
        _tool_input: dict[str, Any],
        _context: PermissionContext,
    ) -> PermissionDecision:
        """Ask for local confirmation or allow external execution."""
        return PermissionDecision(
            behavior=(
                PermissionBehavior.ALLOW
                if self.is_external_tool
                else PermissionBehavior.ASK
            ),
            decision_reason="local fixture",
            message="local fixture",
        )

    async def __call__(self, text: str, **_kwargs: Any) -> ToolChunk:
        """Return a local result; external fixtures must never run here."""
        assert not self.is_external_tool
        self.calls += 1
        return ToolChunk(content=[TextBlock(text=f"result {text}")])


def _text(text: str, block_id: str | None = None) -> dict:
    """Build the entire expected serialized text block."""
    return {
        "type": "text",
        "text": text,
        "id": block_id or AnyString(),
        "created_at": AnyString(),
        "finished_at": None,
    }


def _call(
    call_id: str,
    state: str,
    external: bool,
    name: str = "fixture_echo",
) -> dict:
    """Build the entire expected serialized tool call."""
    return {
        "type": "tool_call",
        "id": call_id,
        "name": name,
        "input": '{"text":"' + call_id + '"}',
        "state": state,
        "suggested_rules": (
            []
            if external
            else [
                {
                    "tool_name": "fixture_echo",
                    "rule_content": None,
                    "behavior": "allow",
                    "source": "suggested",
                },
            ]
        ),
        "created_at": AnyString(),
        "finished_at": None,
    }


def _result(
    call_id: str,
    output: str | list | None = None,
    state: str = "success",
    name: str = "fixture_echo",
) -> dict:
    """Build the entire expected serialized result block."""
    return {
        "type": "tool_result",
        "id": call_id,
        "name": name,
        "output": output
        if output is not None
        else [_text(f"result {call_id}")],
        "state": state,
        "metadata": {},
        "created_at": AnyString(),
        "finished_at": None,
    }


def _message(
    role: str,
    content: list,
    name: str = "fixture",
    usage: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build the entire expected serialized message, including metadata."""
    return {
        "name": "user" if role == "user" else name,
        "role": role,
        "content": content,
        "id": AnyString(),
        "metadata": metadata or {},
        "created_at": AnyString(),
        "usage": usage,
        "finished_at": AnyString() if role == "user" else None,
        "finished_reason": None,
        "structured_output": None,
        "error": None,
    }


def _response(text: str | None, *call_ids: str) -> ChatResponse:
    """Build a deterministic text/tool response."""
    return ChatResponse(
        content=(
            ([TextBlock(text=text)] if text else [])
            + [
                ToolCallBlock(
                    id=call_id,
                    name="fixture_echo",
                    input='{"text":"' + call_id + '"}',
                )
                for call_id in call_ids
            ]
        ),
        is_last=True,
    )


class _ReMeRuntimeTestCase(IsolatedAsyncioTestCase):
    """Share deterministic runtime and recording-boundary fixtures."""

    async def asyncSetUp(self) -> None:
        """Create a shared recording backend and production middleware."""
        self.app = _RecordingApp()
        self.middleware = ReMeMiddleware(
            parameters=ReMeMiddleware.Parameters(mode="static_control"),
        )
        setattr(self.middleware, "_app", self.app)

    async def asyncTearDown(self) -> None:
        """Close the local middleware boundary."""
        await self.middleware.close()

    def _agent(
        self,
        responses: list,
        external: bool = False,
        session_id: str = "session",
        name: str = "fixture",
        extra_middlewares: list[MiddlewareBase] | None = None,
    ) -> tuple[Agent, _LocalModel, _LocalTool]:
        """Build a real agent using only local model/tool transports."""
        model = _LocalModel(context_size=100000)
        model.set_responses(responses)
        tool = _LocalTool()
        tool.is_external_tool = external
        agent = Agent(
            name=name,
            system_prompt="Deterministic local test.",
            model=model,
            toolkit=Toolkit(tools=[tool]),
            middlewares=[self.middleware, *(extra_middlewares or [])],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        agent.state.session_id = session_id
        return agent, model, tool

    @staticmethod
    def _event(
        agent: Agent,
        external: bool,
        *call_ids: str,
        confirmed: bool = True,
    ) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
        """Build the public input event for selected pending tool calls."""
        if external:
            return ExternalExecutionResultEvent(
                reply_id=agent.state.reply_id,
                execution_results=[
                    ToolResultBlock(
                        id=call_id,
                        name="fixture_echo",
                        output=[TextBlock(text=f"result {call_id}")],
                        state="success",
                    )
                    for call_id in call_ids
                ],
            )
        return UserConfirmResultEvent(
            reply_id=agent.state.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name="fixture_echo",
                        input='{"text":"' + call_id + '"}',
                    ),
                )
                for call_id in call_ids
            ],
        )

    def _assert_complete(self, agent: Agent, final: Msg, calls: int) -> None:
        """Check completion, logical message identity, and model calls."""
        self.assertEqual(
            {
                "finished_reason": final.finished_reason,
                "awaiting": agent.state.has_awaiting_tool_calls(agent.name),
                "same_id": final.id == agent.state.reply_id,
                "model_calls": agent.model.cnt,
            },
            {
                "finished_reason": "completed",
                "awaiting": False,
                "same_id": True,
                "model_calls": calls,
            },
        )


class ReMeHITLWritebackTest(_ReMeRuntimeTestCase):
    """Verify acknowledged increments across real pause/resume lifecycles."""

    async def test_confirmation_and_external_completion_increment(
        self,
    ) -> None:
        """Persist complete new content without refeeding acknowledged text."""
        for external in (False, True):
            for prefix in (False, True):
                with self.subTest(external=external, prefix=prefix):
                    self.app.jobs.clear()
                    agent, _, _ = self._agent(
                        [
                            _response("prefix" if prefix else None, "one"),
                            _response("final"),
                        ],
                        external=external,
                    )
                    parked = await agent.reply(UserMsg("user", "question"))
                    self.assertTrue(
                        agent.state.has_awaiting_tool_calls(agent.name),
                    )
                    state = "submitted" if external else "asking"
                    initial = (
                        [
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("user", [_text("question")]),
                                    _message(
                                        "assistant",
                                        [
                                            _text("prefix"),
                                            _call("one", state, external),
                                        ],
                                    ),
                                ],
                            },
                        ]
                        if prefix
                        else []
                    )
                    self.assertEqual(self.app.writes, initial)
                    final = await agent.reply(
                        self._event(agent, external, "one"),
                    )
                    self._assert_complete(agent, final, 2)
                    self.assertEqual(parked.id, final.id)
                    increment = [_result("one"), _text("final")]
                    messages = (
                        [_message("assistant", increment)]
                        if prefix
                        else [
                            _message("user", [_text("question")]),
                            _message(
                                "assistant",
                                [
                                    _call("one", "finished", external),
                                    *increment,
                                ],
                            ),
                        ]
                    )
                    self.assertEqual(
                        self.app.writes,
                        [
                            *initial,
                            {"session_id": "session", "messages": messages},
                        ],
                    )
                    self.assertEqual(
                        self.app.searches,
                        [{"query": "question", "limit": 5}],
                    )

    async def test_repeated_pauses_accumulate_until_completion(self) -> None:
        """Write no new prefix when a resumed invocation pauses again."""
        for external in (False, True):
            with self.subTest(external=external):
                self.app.jobs.clear()
                agent, _, _ = self._agent(
                    [
                        _response("prefix", "one"),
                        _response("middle", "two"),
                        _response("final"),
                    ],
                    external=external,
                )
                await agent.reply(UserMsg("user", "question"))
                initial = deepcopy(self.app.writes)
                await agent.reply(self._event(agent, external, "one"))
                self.assertTrue(
                    agent.state.has_awaiting_tool_calls(agent.name),
                )
                self.assertEqual(self.app.writes, initial)
                final = await agent.reply(self._event(agent, external, "two"))
                self._assert_complete(agent, final, 3)
                self.assertEqual(
                    self.app.writes,
                    [
                        *initial,
                        {
                            "session_id": "session",
                            "messages": [
                                _message(
                                    "assistant",
                                    [
                                        _result("one"),
                                        _text("middle"),
                                        _call("two", "finished", external),
                                        _result("two"),
                                        _text("final"),
                                    ],
                                ),
                            ],
                        },
                    ],
                )
                self.assertEqual(
                    self.app.searches,
                    [{"query": "question", "limit": 5}],
                )

    async def test_partial_external_results_do_not_write_tool_only_delta(
        self,
    ) -> None:
        """Keep partial external results pending until the reply completes."""
        agent, _, _ = self._agent(
            [_response("prefix", "one", "two"), _response("final")],
            external=True,
        )
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        await agent.reply(self._event(agent, True, "one"))
        self.assertTrue(agent.state.has_awaiting_tool_calls(agent.name))
        self.assertEqual(self.app.writes, initial)
        final = await agent.reply(self._event(agent, True, "two"))
        self._assert_complete(agent, final, 2)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message(
                            "assistant",
                            [_result("one"), _result("two"), _text("final")],
                        ),
                    ],
                },
            ],
        )

    async def test_invalid_and_duplicate_resumes_reject_without_backend_io(
        self,
    ) -> None:
        """Invalid inputs cannot write even with an unacknowledged prefix."""
        for external in (False, True):
            with self.subTest(external=external):
                self.app.jobs.clear()
                self.app.fail_writes = 1
                agent, _, _ = self._agent(
                    [_response("prefix", "one"), _response("final")],
                    external=external,
                )
                await agent.reply(UserMsg("user", "question"))
                jobs = deepcopy(self.app.jobs)
                context = deepcopy(
                    [msg.model_dump() for msg in agent.state.context],
                )
                with self.assertRaises(ValueError):
                    await agent.reply(self._event(agent, external, "wrong"))
                self.assertEqual(self.app.jobs, jobs)
                self.assertEqual(
                    [msg.model_dump() for msg in agent.state.context],
                    context,
                )
                event = self._event(agent, external, "one")
                final = await agent.reply(event)
                self._assert_complete(agent, final, 2)
                jobs = deepcopy(self.app.jobs)
                with self.assertRaises(ValueError):
                    await agent.reply(event)
                self.assertEqual(self.app.jobs, jobs)

    async def test_denied_confirmation_keeps_permissions_and_result(
        self,
    ) -> None:
        """Do not run a denied tool; retain its result in completed memory."""
        agent, _, tool = self._agent(
            [_response("prefix", "one"), _response("denial explained")],
        )
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        final = await agent.reply(
            self._event(agent, False, "one", confirmed=False),
        )
        self._assert_complete(agent, final, 2)
        self.assertEqual(tool.calls, 0)
        denied = (
            '<system-reminder>The execution of tool "fixture_echo" '
            "is denied by user!</system-reminder>"
        )
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message(
                            "assistant",
                            [
                                _result("one", denied, "denied"),
                                _text("denial explained"),
                            ],
                        ),
                    ],
                },
            ],
        )

    async def test_acknowledged_text_block_growth_writes_only_suffix(
        self,
    ) -> None:
        """Retain appended text on an existing block ID in public context."""
        agent, _, _ = self._agent(
            [_response("prefix", "one"), _response("final")],
        )
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        block = agent.state.context[-1].get_content_blocks("text")[0]
        block.text += " extended"
        final = await agent.reply(self._event(agent, False, "one"))
        self._assert_complete(agent, final, 2)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message(
                            "assistant",
                            [
                                _text(" extended", block.id),
                                _result("one"),
                                _text("final"),
                            ],
                        ),
                    ],
                },
            ],
        )

    async def test_failed_prefix_is_not_acknowledged_or_retried_in_place(
        self,
    ) -> None:
        """Submit the unacknowledged increment on a later completion."""
        for unsuccessful_response in (False, True):
            with self.subTest(unsuccessful_response=unsuccessful_response):
                self.app.jobs.clear()
                self.app.fail_writes = int(not unsuccessful_response)
                self.app.reject_writes = int(unsuccessful_response)
                agent, _, _ = self._agent(
                    [_response("prefix", "one"), _response("final")],
                )
                await agent.reply(UserMsg("user", "question"))
                initial = deepcopy(self.app.writes)
                self.assertEqual(len(initial), 1)
                final = await agent.reply(self._event(agent, False, "one"))
                self._assert_complete(agent, final, 2)
                self.assertEqual(
                    self.app.writes,
                    [
                        *initial,
                        {
                            "session_id": "session",
                            "messages": [
                                _message("user", [_text("question")]),
                                _message(
                                    "assistant",
                                    [
                                        _text("prefix"),
                                        _call("one", "finished", False),
                                        _result("one"),
                                        _text("final"),
                                    ],
                                ),
                            ],
                        },
                    ],
                )

    async def test_repeated_pauses_without_initial_text_defer_entire_exchange(
        self,
    ) -> None:
        """Retain later paused text until completion without an early write."""
        for external in (False, True):
            with self.subTest(external=external):
                self.app.jobs.clear()
                agent, _, _ = self._agent(
                    [
                        _response(None, "one"),
                        _response("middle", "two"),
                        _response("final"),
                    ],
                    external=external,
                )
                await agent.reply(UserMsg("user", "question"))
                self.assertEqual(self.app.writes, [])
                await agent.reply(self._event(agent, external, "one"))
                self.assertTrue(
                    agent.state.has_awaiting_tool_calls(agent.name),
                )
                self.assertEqual(self.app.writes, [])
                final = await agent.reply(self._event(agent, external, "two"))
                self._assert_complete(agent, final, 3)
                self.assertEqual(
                    self.app.writes,
                    [
                        {
                            "session_id": "session",
                            "messages": [
                                _message("user", [_text("question")]),
                                _message(
                                    "assistant",
                                    [
                                        _call("one", "finished", external),
                                        _result("one"),
                                        _text("middle"),
                                        _call("two", "finished", external),
                                        _result("two"),
                                        _text("final"),
                                    ],
                                ),
                            ],
                        },
                    ],
                )
                self.assertEqual(
                    self.app.searches,
                    [{"query": "question", "limit": 5}],
                )

    async def test_failed_completion_does_not_retry_or_leak_into_next_turn(
        self,
    ) -> None:
        """A failed terminal write remains fail-open without a retry loop."""
        agent, _, _ = self._agent(
            [
                _response("prefix", "one"),
                _response("final"),
                _response("next answer"),
            ],
        )
        await agent.reply(UserMsg("user", "question"))
        self.app.fail_writes = 1
        final = await agent.reply(self._event(agent, False, "one"))
        self._assert_complete(agent, final, 2)
        attempts = deepcopy(self.app.writes)
        self.assertEqual(len(attempts), 2)
        final = await agent.reply(UserMsg("user", "next question"))
        self._assert_complete(agent, final, 3)
        self.assertEqual(
            self.app.writes,
            [
                *attempts,
                {
                    "session_id": "session",
                    "messages": [
                        _message("user", [_text("next question")]),
                        _message("assistant", [_text("next answer")]),
                    ],
                },
            ],
        )

    async def test_resume_interrupt_and_cancellation_preserve_partial_policy(
        self,
    ) -> None:
        """Write nothing on resume interruption; isolate the next turn."""
        for external in (False, True):
            for cancel in (False, True):
                with self.subTest(external=external, cancel=cancel):
                    self.app.jobs.clear()
                    agent, model, _ = self._agent(
                        [
                            _response("prefix", "one"),
                            asyncio.CancelledError(),
                            _response("next answer"),
                        ],
                        external=external,
                    )
                    await agent.reply(UserMsg("user", "question"))
                    initial = deepcopy(self.app.writes)
                    event = (
                        self._event(agent, external, "one")
                        if cancel
                        else UserInterruptEvent(reply_id=agent.state.reply_id)
                    )
                    final = await agent.reply(event)
                    self.assertEqual(final.finished_reason, "interrupted")
                    self.assertEqual(self.app.writes, initial)
                    model.set_responses([_response("next answer")])
                    final = await agent.reply(UserMsg("user", "next question"))
                    self._assert_complete(agent, final, 1)
                    self.assertEqual(
                        self.app.writes,
                        [
                            *initial,
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("user", [_text("next question")]),
                                    _message(
                                        "assistant",
                                        [_text("next answer")],
                                    ),
                                ],
                            },
                        ],
                    )

    async def test_original_generation_cancellation_preserves_partial_write(
        self,
    ) -> None:
        """Keep partial writes on cancellation of original user input."""
        agent, _, _ = self._agent(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="partial")],
                        is_last=False,
                    ),
                    asyncio.CancelledError(),
                ],
            ],
        )
        final = await agent.reply(UserMsg("user", "question"))
        self.assertEqual(final.finished_reason, "interrupted")
        self.assertEqual(
            self.app.writes,
            [
                {
                    "session_id": "session",
                    "messages": [
                        _message("user", [_text("question")]),
                        _message("assistant", [_text("partial")]),
                    ],
                },
            ],
        )

    async def test_resumed_partial_generation_cancellation_does_not_write(
        self,
    ) -> None:
        """Do not write an interrupted partial answer during resume."""
        agent, _, _ = self._agent(
            [
                [_response("prefix", "one")],
                [
                    ChatResponse(
                        content=[TextBlock(text="partial resumed answer")],
                        is_last=False,
                    ),
                    asyncio.CancelledError(),
                ],
            ],
        )
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        final = await agent.reply(self._event(agent, False, "one"))
        self.assertEqual(final.finished_reason, "interrupted")
        self.assertEqual(self.app.writes, initial)

    async def test_resume_exception_does_not_write_or_leak_into_next_turn(
        self,
    ) -> None:
        """Do not acknowledge a continuation if its handler raises."""
        agent, model, _ = self._agent(
            [
                _response("prefix", "one"),
                RuntimeError("deterministic model failure"),
            ],
        )
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        with self.assertRaisesRegex(
            RuntimeError,
            "deterministic model failure",
        ):
            await agent.reply(self._event(agent, False, "one"))
        self.assertEqual(self.app.writes, initial)
        model.set_responses([_response("next answer")])
        final = await agent.reply(UserMsg("user", "next question"))
        self._assert_complete(agent, final, 1)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message("user", [_text("next question")]),
                        _message("assistant", [_text("next answer")]),
                    ],
                },
            ],
        )

    async def test_pending_checkpoint_cannot_cross_changed_session(
        self,
    ) -> None:
        """A changed session cannot receive an earlier pending exchange."""
        agent, _, _ = self._agent(
            [
                _response("prefix", "one"),
                _response("final"),
                _response("new session answer"),
            ],
        )
        await agent.reply(UserMsg("user", "old session question"))
        initial = deepcopy(self.app.writes)
        agent.state.session_id = "new-session"
        final = await agent.reply(self._event(agent, False, "one"))
        self._assert_complete(agent, final, 2)
        self.assertEqual(self.app.writes, initial)
        final = await agent.reply(UserMsg("user", "new session question"))
        self._assert_complete(agent, final, 3)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "new-session",
                    "messages": [
                        _message("user", [_text("new session question")]),
                        _message("assistant", [_text("new session answer")]),
                    ],
                },
            ],
        )

    async def test_shared_agents_isolate_retrieval_and_reply_checkpoints(
        self,
    ) -> None:
        """Concurrent agents stay isolated, including identical session IDs."""
        for same_session in (False, True):
            with self.subTest(same_session=same_session):
                self.app.jobs.clear()
                agents = [
                    self._agent(
                        [_response(None, "one"), _response(f"answer {name}")],
                        external=True,
                        session_id="shared" if same_session else name,
                        name=name,
                    )[0]
                    for name in ("alpha", "beta")
                ]
                await asyncio.gather(
                    *(
                        agent.reply(UserMsg("user", f"question {agent.name}"))
                        for agent in agents
                    ),
                )
                self.assertEqual(self.app.writes, [])
                finals = await asyncio.gather(
                    *(
                        agent.reply(self._event(agent, True, "one"))
                        for agent in agents
                    ),
                )
                for agent, final in zip(agents, finals):
                    self._assert_complete(agent, final, 2)
                self.assertNotEqual(finals[0].id, finals[1].id)
                self.assertEqual(
                    sorted(self.app.searches, key=lambda item: item["query"]),
                    [
                        {"query": "question alpha", "limit": 5},
                        {"query": "question beta", "limit": 5},
                    ],
                )
                self.assertEqual(
                    sorted(
                        self.app.writes,
                        key=lambda item: item["messages"][0]["content"][0][
                            "text"
                        ],
                    ),
                    [
                        {
                            "session_id": "shared" if same_session else name,
                            "messages": [
                                _message("user", [_text(f"question {name}")]),
                                _message(
                                    "assistant",
                                    [
                                        _call("one", "finished", True),
                                        _result("one"),
                                        _text(f"answer {name}"),
                                    ],
                                    name,
                                ),
                            ],
                        }
                        for name in ("alpha", "beta")
                    ],
                )
                self.assertEqual(
                    getattr(self.middleware, "_retrieval_tasks"),
                    {},
                )

    async def test_memory_hint_preserves_same_id_message_boundaries_on_resume(
        self,
    ) -> None:
        """Preserve earlier slices of the same reply across memory hints."""

        class AllowedTool(_LocalTool):
            """Finish a local tool before the later confirmation pause."""

            name: str = "allowed_echo"

            async def check_permissions(
                self,
                _tool_input: dict[str, Any],
                _context: PermissionContext,
            ) -> PermissionDecision:
                """Allow the deterministic first tool without human input."""
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    decision_reason="local fixture",
                    message="local fixture",
                )

        self.app.memories = ["retrieved memory"]
        first = ChatResponse(
            content=[
                TextBlock(text="early prefix"),
                ToolCallBlock(
                    id="early",
                    name="allowed_echo",
                    input='{"text":"early"}',
                ),
            ],
            is_last=True,
        )
        agent, _, _ = self._agent(
            [
                first,
                _response("parked prefix", "one"),
                _response("final"),
            ],
        )
        await agent.toolkit.add_tool(AllowedTool())
        await agent.reply(UserMsg("user", "question"))
        self.assertEqual(
            self.app.writes,
            [
                {
                    "session_id": "session",
                    "messages": [
                        _message("user", [_text("question")]),
                        _message(
                            "assistant",
                            [
                                _text("early prefix"),
                                _call(
                                    "early",
                                    "finished",
                                    True,
                                    "allowed_echo",
                                ),
                                _result("early", name="allowed_echo"),
                            ],
                        ),
                        _message(
                            "assistant",
                            [
                                _text("parked prefix"),
                                _call("one", "asking", False),
                            ],
                        ),
                    ],
                },
            ],
        )
        self.assertEqual(
            [msg.name for msg in agent.state.context],
            ["user", "fixture", "memory", "fixture"],
        )
        self.assertEqual(agent.state.context[1].id, agent.state.context[3].id)
        initial = deepcopy(self.app.writes)
        final = await agent.reply(self._event(agent, False, "one"))
        self._assert_complete(agent, final, 3)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message(
                            "assistant",
                            [_result("one"), _text("final")],
                        ),
                    ],
                },
            ],
        )

    async def test_completion_delta_preserves_current_usage_and_metadata(
        self,
    ) -> None:
        """Keep current message metadata and usage when filtering content."""
        first = _response("prefix", "one")
        first.usage = ChatUsage(
            time=0,
            input_tokens=5,
            output_tokens=3,
            cache_input_tokens=1,
        )
        second = _response("final")
        second.usage = ChatUsage(
            time=0,
            input_tokens=7,
            output_tokens=4,
            cache_input_tokens=2,
            cache_creation_input_tokens=1,
        )
        agent, _, _ = self._agent([first, second])
        await agent.reply(UserMsg("user", "question"))
        initial = deepcopy(self.app.writes)
        agent.state.context[-1].metadata["source"] = "resume state"
        final = await agent.reply(self._event(agent, False, "one"))
        self._assert_complete(agent, final, 2)
        self.assertEqual(
            self.app.writes,
            [
                *initial,
                {
                    "session_id": "session",
                    "messages": [
                        _message(
                            "assistant",
                            [_result("one"), _text("final")],
                            usage={
                                "input_tokens": 12,
                                "output_tokens": 7,
                                "cache_input_tokens": 3,
                                "cache_creation_input_tokens": 1,
                            },
                            metadata={"source": "resume state"},
                        ),
                    ],
                },
            ],
        )

    async def test_noncopyable_metadata_keeps_write_preparation_fail_open(
        self,
    ) -> None:
        """Keep replies running without acknowledging preparation failures."""
        warning_path = (
            "agentscope.middleware._longterm_memory._reme._middleware."
            "logger.warning"
        )
        expected_warning = mock.call(
            "ReMe write preparation failed for session_id=%s: %s",
            "session",
            AnyValue(),
        )
        poison = _MetadataLockMiddleware(enabled=True)
        agent, _, _ = self._agent(
            [_response("final"), _response("next answer")],
            extra_middlewares=[poison],
        )
        with mock.patch(warning_path) as warning:
            final = await agent.reply(UserMsg("user", "question"))
        self._assert_complete(agent, final, 1)
        self.assertEqual(warning.call_args_list, [expected_warning])
        self.assertEqual(self.app.writes, [])
        poison.enabled = False
        agent.state.context[-1].metadata.pop("local_lock")
        await agent.reply(UserMsg("user", "next question"))
        self.assertEqual(
            self.app.writes,
            [
                {
                    "session_id": "session",
                    "messages": [
                        _message("user", [_text("next question")]),
                        _message("assistant", [_text("next answer")]),
                    ],
                },
            ],
        )

        for external in (False, True):
            for failed_prefix, prefix in (
                (True, True),
                (False, True),
                (False, False),
            ):
                with self.subTest(
                    external=external,
                    failed_prefix=failed_prefix,
                    prefix=prefix,
                ):
                    self.app.jobs.clear()
                    poison = _MetadataLockMiddleware(enabled=failed_prefix)
                    agent, _, _ = self._agent(
                        [
                            _response("prefix" if prefix else None, "one"),
                            _response("final"),
                            _response("next answer"),
                        ],
                        external=external,
                        extra_middlewares=[poison],
                    )
                    with mock.patch(warning_path) as warning:
                        await agent.reply(UserMsg("user", "question"))
                    self.assertEqual(
                        warning.call_args_list,
                        [expected_warning] if failed_prefix else [],
                    )
                    initial = deepcopy(self.app.writes)
                    if failed_prefix:
                        self.assertEqual(initial, [])
                        agent.state.context[-1].metadata.pop("local_lock")
                    poison.enabled = not failed_prefix
                    with mock.patch(warning_path) as warning:
                        final = await agent.reply(
                            self._event(agent, external, "one"),
                        )
                    self._assert_complete(agent, final, 2)
                    self.assertEqual(
                        warning.call_args_list,
                        [] if failed_prefix else [expected_warning],
                    )
                    expected = initial
                    if failed_prefix:
                        expected = [
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("user", [_text("question")]),
                                    _message(
                                        "assistant",
                                        [
                                            _text("prefix"),
                                            _call("one", "finished", external),
                                            _result("one"),
                                            _text("final"),
                                        ],
                                    ),
                                ],
                            },
                        ]
                    self.assertEqual(self.app.writes, expected)
                    self.assertEqual(
                        self.app.searches,
                        [{"query": "question", "limit": 5}],
                    )
                    poison.enabled = False
                    agent.state.context[-1].metadata.pop("local_lock", None)
                    final = await agent.reply(UserMsg("user", "next question"))
                    self._assert_complete(agent, final, 3)
                    self.assertEqual(
                        self.app.writes,
                        [
                            *expected,
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("user", [_text("next question")]),
                                    _message(
                                        "assistant",
                                        [_text("next answer")],
                                    ),
                                ],
                            },
                        ],
                    )


class ReMeToolOnlyWritebackTest(_ReMeRuntimeTestCase):
    """Verify the logical text gate when completion adds only tool results."""

    async def test_completed_tool_only_increment_uses_logical_exchange_gate(
        self,
    ) -> None:
        """Keep new results when an acknowledged logical exchange completes."""
        for external in (False, True):
            for prefix in (False, True):
                with self.subTest(external=external, prefix=prefix):
                    self.app.jobs.clear()
                    agent, _, _ = self._agent(
                        [
                            _response("prefix" if prefix else None, "one"),
                            _response(None),
                        ],
                        external=external,
                    )
                    await agent.reply(UserMsg("user", "question"))
                    initial = (
                        [
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("user", [_text("question")]),
                                    _message(
                                        "assistant",
                                        [
                                            _text("prefix"),
                                            _call(
                                                "one",
                                                "submitted"
                                                if external
                                                else "asking",
                                                external,
                                            ),
                                        ],
                                    ),
                                ],
                            },
                        ]
                        if prefix
                        else []
                    )
                    self.assertEqual(self.app.writes, initial)
                    event = self._event(agent, external, "one")
                    final = await agent.reply(event)
                    self._assert_complete(agent, final, 2)
                    self.assertEqual(final.content, [])
                    expected = (
                        [
                            *initial,
                            {
                                "session_id": "session",
                                "messages": [
                                    _message("assistant", [_result("one")]),
                                ],
                            },
                        ]
                        if prefix
                        else []
                    )
                    self.assertEqual(self.app.writes, expected)
                    self.assertEqual(
                        self.app.searches,
                        [{"query": "question", "limit": 5}],
                    )
                    jobs = deepcopy(self.app.jobs)
                    with self.assertRaises(ValueError):
                        await agent.reply(event)
                    self.assertEqual(self.app.jobs, jobs)
