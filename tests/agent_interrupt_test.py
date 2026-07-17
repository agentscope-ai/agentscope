# -*- coding: utf-8 -*-
# pylint: disable=redefined-builtin
"""Tests for agent interruption:

- :class:`AgentInterruptCancelTest`: ``task.cancel()`` lands during tool
  execution and the agent must close every pending tool call with an
  ``INTERRUPTED`` result and end the reply with
  ``ReplyEndReason.INTERRUPTED``.
- :class:`AgentInterruptEventTest`: a :class:`UserInterruptEvent` is
  delivered to a parked reply and the agent must close every pending
  HITL tool call the same way, without entering the reasoning-acting
  loop.
"""
import asyncio
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent
from agentscope.event import (
    ReplyEndEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import (
    ToolBase,
    ToolChunk,
    Toolkit,
)

# Sleep the too long enough that a ``task.cancel()`` scheduled a few
# milliseconds later is guaranteed to land while the tool is still inside
# its ``asyncio.sleep``.
_SLOW = 5.0

_INTERRUPT_MSG = (
    "<system-reminder>The tool call has been interrupted by "
    "the user.</system-reminder>"
)

_CONFIRM_SUGGESTED_RULES = [
    {
        "tool_name": "user_confirm_concurrent",
        "rule_content": None,
        "behavior": "allow",
        "source": "suggested",
    },
]


def _tool_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "input": {"type": "string"},
            "timeout": {"type": "number"},
        },
        "required": [],
    }


class _TimeoutConcurrentTool(ToolBase):
    """Concurrent tool that sleeps ``timeout`` seconds before finishing."""

    name: str = "timeout_concurrent"
    description: str = "Sleeps and returns."
    input_schema: dict[str, Any] = _tool_input_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="ok",
            message="ok",
        )

    async def __call__(
        self,
        timeout: float = _SLOW,
        input: str = "",
        **kwargs: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        await asyncio.sleep(timeout)
        yield ToolChunk(
            content=[TextBlock(text=f"done:{input}")],
            is_last=True,
        )


class _TimeoutSequentialTool(_TimeoutConcurrentTool):
    """Sequential (not concurrency-safe) variant."""

    name: str = "timeout_sequential"
    is_concurrency_safe: bool = False


class _UserConfirmConcurrentTool(_TimeoutConcurrentTool):
    """Concurrent tool that always asks for user confirmation."""

    name: str = "user_confirm_concurrent"
    # A tool that requires confirmation is not read-only: the read-only
    # fast path auto-allows read-only invocations in every mode (ahead of
    # ``check_permissions``), which would otherwise bypass the ASK below.
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="needs confirm",
            message="needs confirm",
        )


class _ExternalConcurrentTool(_TimeoutConcurrentTool):
    """Concurrent tool that requires external execution."""

    name: str = "external_concurrent"
    is_external_tool: bool = True


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _msg_base(name: str = "Friday") -> dict[str, Any]:
    """Assistant message dict skeleton — ``finished_at`` stays ``None``
    on assistant replies (only user inputs get a finish timestamp)."""
    return {
        "id": AnyString(),
        "created_at": AnyString(),
        "finished_at": None,
        "metadata": {},
        "name": name,
        "role": "assistant",
        "usage": None,
    }


def _user_msg_dict(content: str) -> dict[str, Any]:
    return {
        "id": AnyString(),
        "created_at": AnyString(),
        "finished_at": AnyString(),
        "metadata": {},
        "name": "user",
        "role": "user",
        "content": [
            {"type": "text", "id": AnyString(), "text": content},
        ],
        "usage": None,
    }


def _tool_call_dict(
    tc_id: str,
    name: str,
    input: str,
    suggested_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "id": tc_id,
        "name": name,
        "input": input,
        "state": "finished",
        "suggested_rules": suggested_rules or [],
    }


def _interrupted_tool_result_dict(
    tc_id: str,
    name: str,
    output_is_blocks: bool,
) -> dict[str, Any]:
    """Interrupted tool_result dict.

    ``output_is_blocks`` toggles between the two output shapes actually
    produced by the framework:

    - ``True`` — ``list[TextBlock]``, emitted by the toolkit's
      ``CancelledError`` path (in-flight tool cancelled mid-run).
    - ``False`` — raw ``str``, emitted by
      :meth:`Agent._close_unfinished_tool_calls` for HITL tool calls
      that never actually started (ASKING / SUBMITTED).
    """
    output: Any
    if output_is_blocks:
        output = [
            {"type": "text", "id": AnyString(), "text": _INTERRUPT_MSG},
        ]
    else:
        output = _INTERRUPT_MSG
    return {
        "type": "tool_result",
        "id": tc_id,
        "name": name,
        "output": output,
        "state": "interrupted",
        "metadata": {},
    }


async def _run_and_cancel(
    agent: Agent,
    inputs: Any,
    cancel_after: float = 0.05,
) -> list[Any]:
    """Drive ``agent.reply_stream(inputs)`` in a background task, cancel
    after ``cancel_after`` seconds, and return the collected events."""
    events: list[Any] = []

    async def _drive() -> None:
        async for evt in agent.reply_stream(inputs):
            events.append(evt)

    task = asyncio.create_task(_drive())
    await asyncio.sleep(cancel_after)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return events


def _assert_interrupted_end(
    testcase: IsolatedAsyncioTestCase,
    events: list[Any],
    reply_id: str,
    session_id: str,
) -> None:
    """The final event must be a ``ReplyEndEvent`` with
    ``finished_reason='interrupted'`` for the given reply/session."""
    testcase.assertIsInstance(events[-1], ReplyEndEvent)
    testcase.assertDictEqual(
        events[-1].model_dump(mode="json"),
        {
            "id": AnyString(),
            "created_at": AnyString(),
            "metadata": {},
            "type": "REPLY_END",
            "session_id": session_id,
            "reply_id": reply_id,
            "finished_reason": "interrupted",
        },
    )


class AgentInterruptCancelTest(IsolatedAsyncioTestCase):
    """``task.cancel()`` lands during tool execution."""

    def _make_agent(self, tools: list[ToolBase]) -> tuple[Agent, MockModel]:
        model = MockModel(model="mock-model", stream=True)
        agent = Agent(
            name="Friday",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(tools=tools),
        )
        return agent, model

    async def test_sequential_tool_cancelled_mid_execution(self) -> None:
        """Sequential batch: model emits one slow sequential tool call,
        ``task.cancel()`` fires while it sleeps, the tool call is patched
        to FINISHED with an INTERRUPTED result (produced via the toolkit
        cancel path, so ``output`` is a ``list[TextBlock]``) and the
        reply ends with INTERRUPTED."""
        agent, model = self._make_agent([_TimeoutSequentialTool()])
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Calling."),
                            ToolCallBlock(
                                id="tc-seq",
                                name="timeout_sequential",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = await _run_and_cancel(
            agent,
            UserMsg(name="user", content="Hi"),
        )

        _assert_interrupted_end(
            self,
            events,
            reply_id=agent.state.reply_id,
            session_id=agent.state.session_id,
        )

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Calling.",
                        },
                        _tool_call_dict(
                            "tc-seq",
                            "timeout_sequential",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-seq",
                            "timeout_sequential",
                            output_is_blocks=True,
                        ),
                    ],
                },
            ],
        )

    async def test_concurrent_tool_cancelled_mid_execution(self) -> None:
        """Concurrent batch: two slow concurrent tool calls running in
        parallel, cancel patches both to INTERRUPTED via the toolkit
        cancel path."""
        agent, model = self._make_agent([_TimeoutConcurrentTool()])
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Calling both."),
                            ToolCallBlock(
                                id="tc-a",
                                name="timeout_concurrent",
                                input='{"input": "a"}',
                            ),
                            ToolCallBlock(
                                id="tc-b",
                                name="timeout_concurrent",
                                input='{"input": "b"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = await _run_and_cancel(
            agent,
            UserMsg(name="user", content="Hi"),
        )

        _assert_interrupted_end(
            self,
            events,
            reply_id=agent.state.reply_id,
            session_id=agent.state.session_id,
        )

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Calling both.",
                        },
                        _tool_call_dict(
                            "tc-a",
                            "timeout_concurrent",
                            '{"input": "a"}',
                        ),
                        _tool_call_dict(
                            "tc-b",
                            "timeout_concurrent",
                            '{"input": "b"}',
                        ),
                        _interrupted_tool_result_dict(
                            "tc-a",
                            "timeout_concurrent",
                            output_is_blocks=True,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-b",
                            "timeout_concurrent",
                            output_is_blocks=True,
                        ),
                    ],
                },
            ],
        )

    async def test_cancel_during_user_confirm_and_running_tool(
        self,
    ) -> None:
        """Concurrent batch mixes a user-confirmation tool (parks in
        ASKING state without ever running, so its result comes from the
        agent ``_close_unfinished_tool_calls`` path with a ``str``
        output) and a slow running tool (whose result comes from the
        toolkit cancel path with a ``list[TextBlock]`` output).

        The running tool's result is appended first (flushed from the
        concurrent queue), the ASKING tool's result is appended last
        by the agent cleanup."""
        agent, model = self._make_agent(
            [_UserConfirmConcurrentTool(), _TimeoutConcurrentTool()],
        )
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Mixed batch."),
                            ToolCallBlock(
                                id="tc-confirm",
                                name="user_confirm_concurrent",
                                input="{}",
                            ),
                            ToolCallBlock(
                                id="tc-run",
                                name="timeout_concurrent",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = await _run_and_cancel(
            agent,
            UserMsg(name="user", content="Hi"),
        )

        _assert_interrupted_end(
            self,
            events,
            reply_id=agent.state.reply_id,
            session_id=agent.state.session_id,
        )

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Mixed batch.",
                        },
                        _tool_call_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            "{}",
                            suggested_rules=_CONFIRM_SUGGESTED_RULES,
                        ),
                        _tool_call_dict(
                            "tc-run",
                            "timeout_concurrent",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-run",
                            "timeout_concurrent",
                            output_is_blocks=True,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )

    async def test_cancel_during_external_and_running_tool(self) -> None:
        """Concurrent batch mixes an external-execution tool (parks in
        SUBMITTED without running → ``str`` output on cleanup) and a
        slow running tool (toolkit cancel → ``list[TextBlock]``)."""
        agent, model = self._make_agent(
            [_ExternalConcurrentTool(), _TimeoutConcurrentTool()],
        )
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Mixed batch."),
                            ToolCallBlock(
                                id="tc-ext",
                                name="external_concurrent",
                                input="{}",
                            ),
                            ToolCallBlock(
                                id="tc-run",
                                name="timeout_concurrent",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = await _run_and_cancel(
            agent,
            UserMsg(name="user", content="Hi"),
        )

        _assert_interrupted_end(
            self,
            events,
            reply_id=agent.state.reply_id,
            session_id=agent.state.session_id,
        )

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Mixed batch.",
                        },
                        _tool_call_dict(
                            "tc-ext",
                            "external_concurrent",
                            "{}",
                        ),
                        _tool_call_dict(
                            "tc-run",
                            "timeout_concurrent",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-run",
                            "timeout_concurrent",
                            output_is_blocks=True,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-ext",
                            "external_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )

    async def test_cancel_with_confirm_external_and_running_tool(
        self,
    ) -> None:
        """Confirm + external + slow running, all three cancelled."""
        agent, model = self._make_agent(
            [
                _UserConfirmConcurrentTool(),
                _ExternalConcurrentTool(),
                _TimeoutConcurrentTool(),
            ],
        )
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Mixed batch."),
                            ToolCallBlock(
                                id="tc-confirm",
                                name="user_confirm_concurrent",
                                input="{}",
                            ),
                            ToolCallBlock(
                                id="tc-ext",
                                name="external_concurrent",
                                input="{}",
                            ),
                            ToolCallBlock(
                                id="tc-run",
                                name="timeout_concurrent",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )

        events = await _run_and_cancel(
            agent,
            UserMsg(name="user", content="Hi"),
        )

        _assert_interrupted_end(
            self,
            events,
            reply_id=agent.state.reply_id,
            session_id=agent.state.session_id,
        )

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Mixed batch.",
                        },
                        _tool_call_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            "{}",
                            suggested_rules=_CONFIRM_SUGGESTED_RULES,
                        ),
                        _tool_call_dict(
                            "tc-ext",
                            "external_concurrent",
                            "{}",
                        ),
                        _tool_call_dict(
                            "tc-run",
                            "timeout_concurrent",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-run",
                            "timeout_concurrent",
                            output_is_blocks=True,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            output_is_blocks=False,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-ext",
                            "external_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )


class AgentInterruptEventTest(IsolatedAsyncioTestCase):
    """A :class:`UserInterruptEvent` is delivered to a parked reply.

    HITL tool calls that never actually ran are patched by
    :meth:`Agent._close_unfinished_tool_calls`, whose ``tool_result``
    output is always the raw interrupt ``str`` (never ``list[TextBlock]``)."""

    def _make_agent(self, tools: list[ToolBase]) -> tuple[Agent, MockModel]:
        model = MockModel(model="mock-model", stream=True)
        agent = Agent(
            name="Friday",
            system_prompt="You are a test agent.",
            model=model,
            toolkit=Toolkit(tools=tools),
        )
        return agent, model

    async def _park_with(
        self,
        agent: Agent,
        model: MockModel,
        pending_tool_calls: list[ToolCallBlock],
    ) -> None:
        """Drive one reply that yields the given tool calls so the reply
        parks in either ASKING or SUBMITTED state."""
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            TextBlock(text="Need HITL."),
                            *pending_tool_calls,
                        ],
                        is_last=True,
                    ),
                ],
            ],
        )
        async for _ in agent.reply_stream(
            UserMsg(name="user", content="Hi"),
        ):
            pass

    async def test_interrupt_event_on_idle_agent_is_noop(self) -> None:
        """No parked HITL → ``UserInterruptEvent`` yields nothing and
        leaves state untouched."""
        agent, _ = self._make_agent([])

        events = []
        async for evt in agent.reply_stream(
            UserInterruptEvent(reply_id="noop"),
        ):
            events.append(evt)

        self.assertListEqual(events, [])
        self.assertListEqual(agent.state.context, [])

    async def test_interrupt_event_after_user_confirm(self) -> None:
        """Parked in ASKING state: ``UserInterruptEvent`` patches the
        tool call to FINISHED and appends an INTERRUPTED result (``str``
        output) and ends the reply with INTERRUPTED."""
        agent, model = self._make_agent([_UserConfirmConcurrentTool()])
        await self._park_with(
            agent,
            model,
            [
                ToolCallBlock(
                    id="tc-confirm",
                    name="user_confirm_concurrent",
                    input="{}",
                ),
            ],
        )
        reply_id = agent.state.reply_id
        session_id = agent.state.session_id

        events = []
        async for evt in agent.reply_stream(
            UserInterruptEvent(reply_id=reply_id),
        ):
            events.append(evt)

        _assert_interrupted_end(self, events, reply_id, session_id)

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Need HITL.",
                        },
                        _tool_call_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            "{}",
                            suggested_rules=_CONFIRM_SUGGESTED_RULES,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )

    async def test_interrupt_event_after_external_execution(self) -> None:
        """Parked in SUBMITTED: ``UserInterruptEvent`` patches the same
        way (``str`` output)."""
        agent, model = self._make_agent([_ExternalConcurrentTool()])
        await self._park_with(
            agent,
            model,
            [
                ToolCallBlock(
                    id="tc-ext",
                    name="external_concurrent",
                    input="{}",
                ),
            ],
        )
        reply_id = agent.state.reply_id
        session_id = agent.state.session_id

        events = []
        async for evt in agent.reply_stream(
            UserInterruptEvent(reply_id=reply_id),
        ):
            events.append(evt)

        _assert_interrupted_end(self, events, reply_id, session_id)

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Need HITL.",
                        },
                        _tool_call_dict(
                            "tc-ext",
                            "external_concurrent",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-ext",
                            "external_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )

    async def test_interrupt_event_after_both_confirm_and_external(
        self,
    ) -> None:
        """Parked with both ASKING and SUBMITTED tool calls: a single
        ``UserInterruptEvent`` closes both."""
        agent, model = self._make_agent(
            [_UserConfirmConcurrentTool(), _ExternalConcurrentTool()],
        )
        await self._park_with(
            agent,
            model,
            [
                ToolCallBlock(
                    id="tc-confirm",
                    name="user_confirm_concurrent",
                    input="{}",
                ),
                ToolCallBlock(
                    id="tc-ext",
                    name="external_concurrent",
                    input="{}",
                ),
            ],
        )
        reply_id = agent.state.reply_id
        session_id = agent.state.session_id

        events = []
        async for evt in agent.reply_stream(
            UserInterruptEvent(reply_id=reply_id),
        ):
            events.append(evt)

        _assert_interrupted_end(self, events, reply_id, session_id)

        context_dicts = [
            msg.model_dump(mode="json") for msg in agent.state.context
        ]
        self.assertListEqual(
            context_dicts,
            [
                _user_msg_dict("Hi"),
                {
                    **_msg_base(),
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": "Need HITL.",
                        },
                        _tool_call_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            "{}",
                            suggested_rules=_CONFIRM_SUGGESTED_RULES,
                        ),
                        _tool_call_dict(
                            "tc-ext",
                            "external_concurrent",
                            "{}",
                        ),
                        _interrupted_tool_result_dict(
                            "tc-confirm",
                            "user_confirm_concurrent",
                            output_is_blocks=False,
                        ),
                        _interrupted_tool_result_dict(
                            "tc-ext",
                            "external_concurrent",
                            output_is_blocks=False,
                        ),
                    ],
                },
            ],
        )
