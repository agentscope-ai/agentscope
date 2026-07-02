# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Unit tests for ReMeMiddleware.

The embedded ReMe app is mocked — we only exercise the AgentScope hook
wiring (retrieve-before / write-after, system-prompt injection,
list_tools exposure) and the small adapters that translate between
AgentScope and ReMe. No real ReMe application is built.

``protected-access`` is disabled because tests legitimately reach into
middleware internals (``mw._app``, ``mw._search``, ``mw._session_id_of``)
to inspect what the public API just did.
"""
import sys
import types
import asyncio
from unittest import mock
from unittest.async_case import IsolatedAsyncioTestCase
from typing import Any

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    HintBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.embedding import EmbeddingModelBase
from agentscope.middleware import ReMeMiddleware
from agentscope.middleware._longterm_memory._reme._utils import (
    _extract_memory_texts,
    _extract_query_text,
)
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import Toolkit, ToolBase, ToolChunk


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


class RecordingMockModel(MockModel):
    """MockModel that captures the ``messages`` of every _call_api call."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the recording mock model."""
        super().__init__(*args, **kwargs)
        self.calls: list[list[Msg]] = []

    async def _call_api(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Record messages before delegating to MockModel."""
        messages = kwargs.get("messages")
        if messages is None and len(args) >= 2:
            messages = args[1]
        self.calls.append(list(messages or []))
        return await super()._call_api(*args, **kwargs)

    @property
    def last_call_messages(self) -> list[Msg]:
        """Return the most recent model-call messages."""
        return self.calls[-1]


class _FakeEmbedding(EmbeddingModelBase):
    """Minimal ``EmbeddingModelBase`` instance for tests.

    ``ReMeMiddleware.Parameters.embedding_model`` is validated by pydantic
    (``arbitrary_types_allowed`` still isinstance-checks), so tests need a
    real subclass rather than a bare sentinel. Its API is never called —
    the middleware only checks presence and passes it through to ReMe.
    """

    def __init__(self) -> None:
        """Skip the heavy base init; nothing here is exercised."""

    async def _call_api(  # pragma: no cover - never invoked in tests
        self,
        inputs: list[Any],
        **kwargs: Any,
    ) -> Any:
        """Unused: no test drives an actual embedding call."""
        raise NotImplementedError


class _FakeResponse:
    """Minimal stand-in for ReMe's ``Response`` (answer/success/metadata)."""

    def __init__(
        self,
        *,
        answer: str = "",
        success: bool = True,
        metadata: dict | None = None,
    ) -> None:
        """Initialize the fake response."""
        self.answer = answer
        self.success = success
        self.metadata = metadata or {}


class _FakeReMeApp:
    """Stands in for an embedded :class:`reme.ReMe` application.

    Mirrors the surface :class:`ReMeMiddleware` drives directly (now that
    there is no client wrapper): ``start`` / ``close`` lifecycle,
    ``update_component`` (model injection), ``is_started``, and
    ``run_job(name, **kwargs) -> Response``. Every job call is recorded;
    ``search_calls`` / ``auto_memory_calls`` re-expose them in the
    middleware's own terms for assertions.
    """

    def __init__(self, search_return: Any = None) -> None:
        """Initialize the fake ReMe app."""
        self.search_return = list(search_return) if search_return else []
        self.run_job_calls: list[dict] = []
        self.injected_models: list[Any] = []
        self.injected_components: dict[str, Any] = {}
        self.start_count = 0
        self.closed = False
        self.is_started = False
        # Set to an exception to simulate a failing job.
        self.search_error: Exception | None = None
        self.auto_memory_error: Exception | None = None

    async def start(self) -> None:
        """Record an idempotent start."""
        self.start_count += 1
        self.is_started = True

    async def close(self) -> None:
        """Record a close."""
        self.closed = True
        self.is_started = False

    async def update_component(
        self,
        component: str,
        name: str,
        **kwargs: Any,
    ) -> None:
        """Record a model injection into a ReMe default component."""
        self.injected_models.append(kwargs.get("model"))
        self.injected_components[component] = kwargs.get("model")

    async def run_job(self, name: str, **kwargs: Any) -> _FakeResponse:
        """Dispatch a ReMe job, recording the call."""
        self.run_job_calls.append({"name": name, **kwargs})
        if name == "search":
            if self.search_error is not None:
                raise self.search_error
            results = [{"text": t} for t in self.search_return]
            return _FakeResponse(metadata={"results": results})
        if name == "auto_memory":
            if self.auto_memory_error is not None:
                raise self.auto_memory_error
            return _FakeResponse(answer="recorded", success=True)
        return _FakeResponse()

    @property
    def search_calls(self) -> list[dict]:
        """Recorded search jobs in the middleware's terms."""
        return [
            {
                "query": c.get("query"),
                "limit": c.get("limit"),
            }
            for c in self.run_job_calls
            if c["name"] == "search"
        ]

    @property
    def auto_memory_calls(self) -> list[dict]:
        """Recorded auto_memory jobs in the middleware's terms."""
        return [
            {
                "messages": c.get("messages"),
                "session_id": c.get("session_id"),
            }
            for c in self.run_job_calls
            if c["name"] == "auto_memory"
        ]

    @property
    def last_chat_model(self) -> Any:
        """The model injected into the ``as_llm`` component, if any."""
        return self.injected_components.get("as_llm")

    @property
    def last_embedding_model(self) -> Any:
        """The model injected into the ``as_embedding`` component."""
        return self.injected_components.get("as_embedding")


def _mw(*, app: Any = None, **kwargs: Any) -> ReMeMiddleware:
    """Build a ``ReMeMiddleware`` with a fake embedded app injected.

    ``ReMeMiddleware`` owns and builds its own ``reme.ReMe`` app, so tests
    can't pass one in. Instead we construct the middleware normally and
    seed ``mw._app`` with a :class:`_FakeReMeApp` before it starts — the
    lazy ``_ensure_started`` path sees a non-``None`` app and skips the
    real build, so no ``reme-ai`` install is needed.

    User-tunable kwargs (``chat_model`` / ``embedding_model`` / ``mode`` /
    ``top_k``) are collected into a ``ReMeMiddleware.Parameters`` so tests
    can keep passing them flat; the rest go straight to the constructor.
    """
    param_keys = ("chat_model", "embedding_model", "mode", "top_k")
    param_kwargs = {k: kwargs.pop(k) for k in param_keys if k in kwargs}
    if param_kwargs:
        kwargs["parameters"] = ReMeMiddleware.Parameters(**param_kwargs)
    mw = ReMeMiddleware(**kwargs)
    mw._app = app if app is not None else _FakeReMeApp()
    return mw


def _single_response(text: str) -> ChatResponse:
    """Build a final single-text chat response."""
    return ChatResponse(
        content=[TextBlock(type="text", text=text)],
        is_last=True,
    )


def _echo_then_text(final_text: str, echo: str = "ping") -> list:
    """Two-step response sequence: an ``echo`` tool call, then a text answer.

    Auto-retrieval now runs as a background task started in ``on_reply`` and
    injected in ``on_reasoning`` once it finishes. A single-shot reply may
    complete before retrieval does, so tests that need to observe the
    injected memory drive a tool call first (two reasoning steps): the task
    finishes during step 1, so the hint is present before step 2's model
    call — the same pattern the AgenticMemory middleware tests use.
    """
    return [
        [
            ChatResponse(
                content=[
                    ToolCallBlock(
                        id="call-1",
                        name="echo",
                        input=f'{{"text": "{echo}"}}',
                    ),
                ],
                is_last=True,
            ),
        ],
        [
            ChatResponse(
                content=[TextBlock(text=final_text)],
                is_last=True,
            ),
        ],
    ]


class _EchoTool(ToolBase):
    """A trivial always-allowed tool used to force a multi-step turn."""

    name: str = "echo"
    description: str = "Echo the given text back."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo."},
        },
        "required": ["text"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow — this is a test fixture."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="test tool",
            message="test tool",
        )

    async def __call__(self, text: str, **kwargs: Any) -> ToolChunk:
        """Echo ``text`` back as the tool result."""
        return ToolChunk(content=[TextBlock(text=f"echo: {text}")])


def _all_tool_names(toolkit: Toolkit) -> list[str]:
    """Flatten every registered tool's name across every group."""
    return [t.name for g in toolkit.tool_groups for t in g.tools]


def _find_tool(toolkit: Toolkit, name: str) -> Any:
    """Look a tool up by name across every group on the toolkit."""
    for g in toolkit.tool_groups:
        for t in g.tools:
            if t.name == name:
                return t
    raise LookupError(f"tool {name!r} not found in any group")


def _find_group(toolkit: Toolkit, name: str) -> Any:
    """Look a tool group up by name on the toolkit."""
    for g in toolkit.tool_groups:
        if g.name == name:
            return g
    raise LookupError(f"tool group {name!r} not found")


def _chunk_text(chunk: Any) -> str:
    """Return the first text block from a tool chunk."""
    return chunk.content[0].text


def _msg_text(m: Any) -> str:
    """Text of a message that may be a ``Msg`` or a serialized dict.

    The middleware serializes messages via ``Msg.model_dump(mode="json")``
    before handing them to ReMe's ``auto_memory`` job, so the fake app
    records dicts whose ``content`` is a list of typed blocks.
    """
    if hasattr(m, "get_text_content"):
        return m.get_text_content() or ""
    content = m.get("content") if isinstance(m, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# ----------------------------------------------------------------------
# Unit tests for module-level helpers
# ----------------------------------------------------------------------


class TestExtractQueryText(IsolatedAsyncioTestCase):
    """Tests for extracting the query text from incoming inputs."""

    def test_none_and_empty(self) -> None:
        """None and empty inputs should not produce a query."""
        self.assertIsNone(_extract_query_text(None))
        self.assertIsNone(_extract_query_text([]))

    def test_single_user_msg(self) -> None:
        """A single user message should become its text content."""
        msg = UserMsg("user", "hello world")
        self.assertEqual(_extract_query_text(msg), "hello world")

    def test_list_of_user_msgs_joined(self) -> None:
        """Multiple user messages should be joined by newlines."""
        msgs = [UserMsg("user", "first"), UserMsg("user", "second")]
        self.assertEqual(_extract_query_text(msgs), "first\nsecond")

    def test_resumption_events_return_none(self) -> None:
        """HITL resumption events should not trigger memory IO."""
        self.assertIsNone(
            _extract_query_text(
                UserConfirmResultEvent(
                    reply_id="reply",
                    confirm_results=[],
                ),
            ),
        )
        self.assertIsNone(
            _extract_query_text(
                ExternalExecutionResultEvent(
                    reply_id="reply",
                    execution_results=[],
                ),
            ),
        )


class TestExtractMemoryTexts(IsolatedAsyncioTestCase):
    """Tests for normalizing ReMe search responses into text lists."""

    def test_metadata_results_file_chunks(self) -> None:
        """The standard ReMe envelope nests chunks under metadata."""
        raw = {
            "answer": "",
            "success": True,
            "metadata": {
                "results": [
                    {"text": "a", "path": "daily/1.md", "score": 0.9},
                    {"text": "b", "path": "daily/2.md", "score": 0.8},
                ],
            },
        }
        self.assertEqual(_extract_memory_texts(raw), ["a", "b"])

    def test_unwrapped_results(self) -> None:
        """An already-unwrapped results list should also work."""
        self.assertEqual(
            _extract_memory_texts({"results": [{"text": "only"}]}),
            ["only"],
        )

    def test_plain_list_of_dicts_and_strings(self) -> None:
        """Plain lists of chunks or strings should be supported."""
        self.assertEqual(
            _extract_memory_texts([{"memory": "m"}, "raw"]),
            ["m", "raw"],
        )

    def test_none_and_garbage(self) -> None:
        """Malformed ReMe outputs should normalize to an empty list."""
        self.assertEqual(_extract_memory_texts(None), [])
        self.assertEqual(
            _extract_memory_texts({"metadata": {"results": "nope"}}),
            [],
        )


# ----------------------------------------------------------------------
# Constructor validation
# ----------------------------------------------------------------------


class TestConstructorValidation(IsolatedAsyncioTestCase):
    """Tests for ReMeMiddleware constructor validation."""

    def test_unknown_mode_raises(self) -> None:
        """Unknown control modes should be rejected by the Parameters model."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ReMeMiddleware.Parameters(
                mode="garbage",  # type: ignore[arg-type]
            )

    def test_session_id_not_stored_on_middleware(self) -> None:
        """session_id is read live from the agent, never stored.

        The middleware takes no ``session_id`` argument and keeps no
        ``_session_id`` attribute, so one instance shared across agents
        cannot leak one conversation's session into another's write-back.
        It is read per call from ``agent.state.session_id``.
        """
        mw = ReMeMiddleware()
        self.assertFalse(hasattr(mw, "_session_id"))

        class _State:
            session_id = "sess-xyz"

        class _Agent:
            state = _State()

        self.assertEqual(mw._session_id_of(_Agent()), "sess-xyz")
        self.assertIsNone(mw._session_id_of(object()))

    def test_embedding_model_enables_vector_store(self) -> None:
        """An ``embedding_model`` turns ReMe's vector store on at build."""
        captured: dict = {}

        def _fake_resolve(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"_ok": True}

        fake_reme = types.ModuleType("reme")
        fake_reme.ReMe = lambda **kw: ("app", kw)  # type: ignore[attr-defined]
        fake_cfg = types.ModuleType("reme.config")
        setattr(fake_cfg, "resolve_app_config", _fake_resolve)

        mw = ReMeMiddleware(
            workspace_dir="/real/ws",
            config="mycfg",
            parameters=ReMeMiddleware.Parameters(
                embedding_model=_FakeEmbedding(),
            ),
        )
        with mock.patch.dict(
            sys.modules,
            {"reme": fake_reme, "reme.config": fake_cfg},
        ):
            mw._build_app()

        self.assertEqual(
            captured["components"],
            {"file_store": {"default": {"embedding_store": "default"}}},
        )
        self.assertEqual(captured["workspace_dir"], "/real/ws")
        self.assertEqual(captured["config"], "mycfg")
        self.assertFalse(captured["enable_logo"])

    def test_no_embedding_model_keeps_keyword_only(self) -> None:
        """Without an ``embedding_model`` the vector store stays off."""
        captured: dict = {}

        def _fake_resolve(**kwargs: Any) -> dict:
            captured.update(kwargs)
            return {"_ok": True}

        fake_reme = types.ModuleType("reme")
        fake_reme.ReMe = lambda **kw: ("app", kw)  # type: ignore[attr-defined]
        fake_cfg = types.ModuleType("reme.config")
        setattr(fake_cfg, "resolve_app_config", _fake_resolve)

        mw = ReMeMiddleware(workspace_dir="/real/ws")
        with mock.patch.dict(
            sys.modules,
            {"reme": fake_reme, "reme.config": fake_cfg},
        ):
            mw._build_app()

        self.assertNotIn("components", captured)


# ----------------------------------------------------------------------
# Static-control mode (default)
# ----------------------------------------------------------------------


class TestStaticControlMode(IsolatedAsyncioTestCase):
    """Default mode: retrieve-before / inject / write-after, no tools."""

    async def asyncSetUp(self) -> None:
        """Create a fresh recording model and empty toolkit."""
        self.model = RecordingMockModel(context_size=100_000)
        self.toolkit = Toolkit()

    def _agent(
        self,
        middleware: ReMeMiddleware,
        response_text: str = "ok",
    ) -> Agent:
        """Build a test agent using ``middleware`` and one response."""
        self.model.set_responses([_single_response(response_text)])
        return Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

    async def test_retrieve_inject_write(self) -> None:
        """Static control should search, inject, reply, then write."""
        fake = _FakeReMeApp(search_return=["alice loves coffee"])
        mw = _mw(
            app=fake,
            mode="static_control",
        )

        # A tool call gives two reasoning steps, so the background retrieval
        # finishes and its hint injects before the final model call.
        self.model.set_responses(_echo_then_text("hi alice"))
        toolkit = Toolkit(tools=[_EchoTool()])
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=toolkit,
            middlewares=[mw],
        )
        # session_id is sourced from the agent, not the middleware.
        agent.state.session_id = "alice-001"
        reply = await agent.reply(UserMsg("user", "remind me what I like"))

        # 1. search called with the user query, configured limit/min_score
        self.assertEqual(len(fake.search_calls), 1)
        self.assertEqual(
            fake.search_calls[0]["query"],
            "remind me what I like",
        )
        self.assertEqual(fake.search_calls[0]["limit"], 5)

        # 2. write called post-turn with the user+assistant pair, scoped
        #    by session_id.
        self.assertEqual(len(fake.auto_memory_calls), 1)
        write = fake.auto_memory_calls[0]
        self.assertEqual(write["session_id"], "alice-001")
        written_texts = [_msg_text(m) for m in write["messages"]]
        self.assertIn("remind me what I like", written_texts)
        self.assertIn("hi alice", written_texts)

        # 3. system prompt unchanged — static_control adds no tool nudge.
        sys_msg = self.model.last_call_messages[0]
        self.assertEqual(sys_msg.role, "system")
        self.assertEqual(sys_msg.get_text_content(), "base system prompt")

        # 4. memory appended to the agent's persistent context as an
        #    assistant-role hint note named "memory".
        memory_msgs = [
            m
            for m in agent.state.context
            if getattr(m, "name", None) == "memory"
        ]
        self.assertEqual(len(memory_msgs), 1)
        self.assertEqual(memory_msgs[0].role, "assistant")
        memory_hints = memory_msgs[0].get_content_blocks("hint")
        self.assertEqual(len(memory_hints), 1)
        self.assertIsInstance(memory_hints[0], HintBlock)
        memory_text = memory_hints[0].hint
        self.assertIn("Relevant memories", memory_text)
        self.assertIn("alice loves coffee", memory_text)
        # The model saw it on the final call (injected before step 2).
        delivered = [
            m
            for m in self.model.last_call_messages
            if getattr(m, "name", None) == "memory"
        ]
        self.assertEqual(len(delivered), 1)

        # 5. no agent-control tools registered (in any group)
        all_names = _all_tool_names(toolkit)
        self.assertNotIn("memory_search", all_names)
        self.assertNotIn("add_memory", all_names)

        self.assertEqual(reply.get_text_content(), "hi alice")

    async def test_write_back_includes_full_turn_increment(self) -> None:
        """Write-back persists the whole turn — user input, the tool-call
        step, the tool result, and the final answer — not just the last
        message, and never the injected memory note."""
        fake = _FakeReMeApp(search_return=["remembered fact"])
        mw = _mw(app=fake, mode="static_control")

        # Turn 1: the model asks for a tool call; turn 2: it answers.
        self.model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="call-1",
                                name="echo",
                                input='{"text": "ping"}',
                            ),
                        ],
                        is_last=True,
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="final answer")],
                        is_last=True,
                    ),
                ],
            ],
        )
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=Toolkit(tools=[_EchoTool()]),
            middlewares=[mw],
        )
        agent.state.session_id = "s-multi"

        await agent.reply(UserMsg("user", "use the echo tool then answer"))

        self.assertEqual(len(fake.auto_memory_calls), 1)
        written = fake.auto_memory_calls[0]["messages"]

        # The injected memory hint is NEVER written back.
        self.assertFalse(
            any(m.get("name") == "memory" for m in written),
            "the injected memory note must be excluded from write-back",
        )

        # Every step of the turn is present: user input, the assistant
        # tool call, the tool result, and the final answer.
        block_types = {
            b.get("type")
            for m in written
            for b in (m.get("content") or [])
            if isinstance(b, dict)
        }
        self.assertIn("tool_call", block_types)
        self.assertIn("tool_result", block_types)

        texts = [_msg_text(m) for m in written]
        self.assertIn("use the echo tool then answer", texts)
        self.assertIn("final answer", texts)

    async def test_write_back_is_per_turn_increment(self) -> None:
        """Each write-back carries ONLY that turn's new messages.

        ``auto_memory`` consumes the incremental exchange, while
        ``agent.state.context`` is the full accumulated history — so a
        second turn must not re-send the first turn's messages.
        """
        fake = _FakeReMeApp(search_return=[])
        mw = _mw(app=fake, mode="static_control")

        # Two sequential turns on the SAME agent (shared, growing context).
        self.model.set_responses(
            [
                _single_response("first answer"),
                _single_response("second answer"),
            ],
        )
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=self.toolkit,
            middlewares=[mw],
        )
        agent.state.session_id = "s-seq"

        await agent.reply(UserMsg("user", "first question"))
        await agent.reply(UserMsg("user", "second question"))

        self.assertEqual(len(fake.auto_memory_calls), 2)
        first_texts = [
            _msg_text(m) for m in fake.auto_memory_calls[0]["messages"]
        ]
        second_texts = [
            _msg_text(m) for m in fake.auto_memory_calls[1]["messages"]
        ]

        # Turn 1 wrote only turn-1 content.
        self.assertIn("first question", first_texts)
        self.assertIn("first answer", first_texts)

        # Turn 2 wrote ONLY turn-2 content — none of turn 1 leaked in,
        # even though it is still present in agent.state.context.
        self.assertIn("second question", second_texts)
        self.assertIn("second answer", second_texts)
        self.assertNotIn("first question", second_texts)
        self.assertNotIn("first answer", second_texts)

    async def test_memory_message_lands_after_user_message(self) -> None:
        """The memory note is inserted AFTER the new user input lands in the
        agent context (injected on a later reasoning step, not before)."""
        fake = _FakeReMeApp(search_return=["saved fact"])
        mw = _mw(
            app=fake,
            mode="static_control",
        )
        self.model.set_responses(_echo_then_text("answer"))
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=Toolkit(tools=[_EchoTool()]),
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "tell me what you know"))

        roles_and_names = [
            (m.role, getattr(m, "name", None)) for m in agent.state.context
        ]
        user_idx = next(
            i
            for i, (r, n) in enumerate(roles_and_names)
            if r == "user" and n != "memory"
        )
        memory_idx = next(
            i for i, (_, n) in enumerate(roles_and_names) if n == "memory"
        )
        self.assertGreater(
            memory_idx,
            user_idx,
            f"memory note at {memory_idx} should come after user msg "
            f"at {user_idx}: {roles_and_names}",
        )

    async def test_no_memories_no_injection(self) -> None:
        """Empty search results should not add a memory hint message."""
        fake = _FakeReMeApp(search_return=[])
        mw = _mw(
            app=fake,
            mode="static_control",
        )

        # Tool call → two reasoning steps, so retrieval completes and the
        # injection path is exercised; with empty results it must add nothing.
        self.model.set_responses(_echo_then_text("ok"))
        agent = Agent(
            name="agent_under_test",
            system_prompt="base system prompt",
            model=self.model,
            toolkit=Toolkit(tools=[_EchoTool()]),
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "hi"))

        msgs = self.model.last_call_messages
        self.assertEqual(msgs[0].get_text_content(), "base system prompt")
        for m in msgs:
            self.assertNotEqual(getattr(m, "name", None), "memory")
        for m in agent.state.context:
            self.assertNotEqual(getattr(m, "name", None), "memory")
        self.assertEqual(len(fake.search_calls), 1)

    async def test_search_failure_does_not_break_reply(self) -> None:
        """Search errors should be logged but not block the reply."""
        fake = _FakeReMeApp()
        fake.search_error = RuntimeError("reme down")
        mw = _mw(
            app=fake,
            mode="static_control",
        )

        agent = self._agent(mw, response_text="still works")
        reply = await agent.reply(UserMsg("user", "ping"))

        self.assertEqual(reply.get_text_content(), "still works")
        # Write still happened — a failed search must not block writes.
        self.assertEqual(len(fake.auto_memory_calls), 1)

    async def test_shared_middleware_isolates_sessions(self) -> None:
        """One middleware shared by two agents writes each agent's own
        session_id — no leakage from a stored session."""
        fake = _FakeReMeApp()
        mw = _mw(app=fake, mode="static_control")

        self.model.set_responses(
            [_single_response("r1"), _single_response("r2")],
        )
        agent_a = Agent(
            name="a",
            system_prompt="p",
            model=self.model,
            toolkit=Toolkit(),
            middlewares=[mw],
        )
        agent_a.state.session_id = "sess-a"
        agent_b = Agent(
            name="b",
            system_prompt="p",
            model=self.model,
            toolkit=Toolkit(),
            middlewares=[mw],
        )
        agent_b.state.session_id = "sess-b"

        await agent_a.reply(UserMsg("user", "hi from a"))
        await agent_b.reply(UserMsg("user", "hi from b"))

        sessions = [c["session_id"] for c in fake.auto_memory_calls]
        self.assertEqual(sessions, ["sess-a", "sess-b"])

    async def test_concurrent_sessions_isolate_retrieval_tasks(self) -> None:
        """Retrieval tasks are keyed by session_id, so two replies running
        concurrently on a shared middleware never clobber each other's
        in-flight search — each session injects its own note and no task is
        left behind."""
        fake = _FakeReMeApp(search_return=["shared fact"])
        mw = _mw(app=fake, mode="static_control")

        def _make_agent(sid: str) -> Agent:
            # Distinct model per agent: a shared response queue would be
            # consumed racily by concurrent turns.
            model = RecordingMockModel(context_size=100_000)
            model.set_responses(_echo_then_text(f"done-{sid}"))
            agent = Agent(
                name=sid,
                system_prompt="base",
                model=model,
                toolkit=Toolkit(tools=[_EchoTool()]),
                middlewares=[mw],
            )
            agent.state.session_id = sid
            return agent

        agent_a = _make_agent("sess-a")
        agent_b = _make_agent("sess-b")

        await asyncio.gather(
            agent_a.reply(UserMsg("user", "a asks")),
            agent_b.reply(UserMsg("user", "b asks")),
        )

        # Each session injected its own memory note exactly once.
        for agent in (agent_a, agent_b):
            notes = [
                m
                for m in agent.state.context
                if getattr(m, "name", None) == "memory"
            ]
            self.assertEqual(len(notes), 1, f"{agent.name} missing its note")

        # No task left behind for either session after both turns finish.
        self.assertEqual(mw._retrieval_tasks, {})

    async def test_chat_model_injected_at_start(self) -> None:
        """The configured chat_model is injected into ReMe at start."""
        explicit = RecordingMockModel(context_size=100_000)
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="static_control",
            chat_model=explicit,
        )
        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        self.assertIs(mw._parameters.chat_model, explicit)
        self.assertIs(fake.last_chat_model, explicit)

    async def test_no_chat_model_no_injection(self) -> None:
        """Without a chat_model the middleware injects nothing.

        The model is fixed at construction and never taken from the agent,
        so ReMe falls back to the LLM in its own config/credentials.
        """
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="static_control",
        )
        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        self.assertIsNone(mw._parameters.chat_model)
        self.assertEqual(fake.injected_models, [])

    async def test_embedding_model_injected_at_start(self) -> None:
        """The configured embedding_model is injected into ReMe at start.

        ReMe starts its ``as_embedding`` component eagerly; injecting a
        model bypasses ReMe building its own from credentials, mirroring
        the chat-model injection into ``as_llm``.
        """
        chat = RecordingMockModel(context_size=100_000)
        embedding = _FakeEmbedding()  # only passed through to ReMe
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="static_control",
            chat_model=chat,
            embedding_model=embedding,
        )
        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        self.assertIs(mw._parameters.embedding_model, embedding)
        self.assertIs(fake.last_embedding_model, embedding)
        # Both components are configured independently at start.
        self.assertIs(fake.last_chat_model, chat)

    async def test_no_embedding_model_no_injection(self) -> None:
        """Without an embedding_model, ReMe's embedding stays untouched."""
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="static_control",
            chat_model=RecordingMockModel(context_size=100_000),
        )
        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        self.assertIsNone(mw._parameters.embedding_model)
        self.assertIsNone(fake.last_embedding_model)


# ----------------------------------------------------------------------
# Agent-control mode
# ----------------------------------------------------------------------


class TestAgentControlMode(IsolatedAsyncioTestCase):
    """Tools are listed, but no automatic memory hook behavior."""

    async def asyncSetUp(self) -> None:
        """Create a fresh recording model and empty toolkit."""
        self.model = RecordingMockModel(context_size=100_000)
        self.toolkit = Toolkit()

    async def _agent(
        self,
        middleware: ReMeMiddleware,
        *,
        name: str = "a",
        system_prompt: str = "p",
        responses: list[str] | None = None,
    ) -> Agent:
        """Build an agent with tools explicitly listed by middleware."""
        self.model.set_responses(
            [_single_response(r) for r in (responses or ["ok"])],
        )
        self.toolkit = Toolkit(tools=await middleware.list_tools())
        return Agent(
            name=name,
            system_prompt=system_prompt,
            model=self.model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

    async def test_tools_listed_and_hint_in_prompt(self) -> None:
        """Agent-control mode should expose the search tool + prompt nudge."""
        fake = _FakeReMeApp(search_return=["found"])
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        agent = await self._agent(mw, system_prompt="base prompt")
        await agent.reply(UserMsg("user", "hello"))

        basic = _find_group(self.toolkit, "basic")
        names = [t.name for t in basic.tools]
        self.assertIn("memory_search", names)
        # ReMe has no manual add tool — writing is automatic.
        self.assertNotIn("add_memory", names)

        # No automatic retrieval ran — agent_control leaves search to the
        # tool...
        self.assertEqual(fake.search_calls, [])
        # ...but write-back is automatic in every mode (ReMe listens to
        # the conversation via the reply hook).
        self.assertEqual(len(fake.auto_memory_calls), 1)

        # System prompt got a short nudge mentioning the tool.
        msgs = self.model.last_call_messages
        prompt_text = msgs[0].get_text_content()
        self.assertIn("base prompt", prompt_text)
        self.assertIn("Long-term memory", prompt_text)
        self.assertIn("memory_search", prompt_text)
        # No synthetic memory message was injected in agent_control mode.
        for m in msgs:
            self.assertNotEqual(getattr(m, "name", None), "memory")

    async def test_middleware_does_not_mutate_toolkit(self) -> None:
        """Middleware should not register tools unless caller does so."""
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        self.model.set_responses([_single_response("ok")])
        toolkit = Toolkit()
        agent = Agent(
            name="a",
            system_prompt="base prompt",
            model=self.model,
            toolkit=toolkit,
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "hello"))

        self.assertNotIn("memory_search", _all_tool_names(toolkit))
        self.assertNotIn("add_memory", _all_tool_names(toolkit))

    async def test_memory_search_tool_invokes_reme(self) -> None:
        """The memory_search tool should query ReMe and return results."""
        fake = _FakeReMeApp(search_return=["first fact", "second fact"])
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        agent = await self._agent(mw, system_prompt="base prompt")
        await agent.reply(UserMsg("user", "hi"))

        search_tool = _find_tool(self.toolkit, "memory_search")
        result = await search_tool(
            query="what does alice like?",
            limit=3,
        )
        result_text = _chunk_text(result)
        self.assertIn("first fact", result_text)
        self.assertIn("second fact", result_text)

        # One ReMe search, using the supplied limit.
        self.assertEqual(len(fake.search_calls), 1)
        self.assertEqual(
            fake.search_calls[0]["query"],
            "what does alice like?",
        )
        self.assertEqual(fake.search_calls[0]["limit"], 3)

    async def test_memory_search_default_limit_is_top_k(self) -> None:
        """Omitting ``limit`` should fall back to the middleware's top_k.

        The tool advertises ``top_k`` as the schema default and resolves an
        omitted ``limit`` through it, so configuring ``top_k`` governs the
        agent-facing tool, not just static-control retrieval.
        """
        fake = _FakeReMeApp(search_return=["fact"])
        mw = _mw(
            app=fake,
            mode="agent_control",
            top_k=11,
        )
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))

        search_tool = _find_tool(self.toolkit, "memory_search")
        # Schema advertises top_k as the default.
        self.assertEqual(
            search_tool.input_schema["properties"]["limit"]["default"],
            11,
        )

        # Calling without a limit uses top_k.
        await search_tool(query="what does alice like?")
        self.assertEqual(fake.search_calls[-1]["limit"], 11)

    async def test_memory_search_no_results(self) -> None:
        """An empty workspace yields a friendly no-results message."""
        fake = _FakeReMeApp(search_return=[])
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        search_tool = _find_tool(self.toolkit, "memory_search")

        result = await search_tool(query="anything", limit=5)
        self.assertIn("no relevant memories", _chunk_text(result).lower())

    async def test_memory_search_failure_returns_error_chunk(self) -> None:
        """ReMe raising during search yields a ToolChunk state=ERROR."""
        from agentscope.message import ToolResultState

        fake = _FakeReMeApp()
        fake.search_error = RuntimeError("reme down")
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        search_tool = _find_tool(self.toolkit, "memory_search")

        result = await search_tool(query="q", limit=5)
        self.assertIsInstance(result, ToolChunk)
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("reme down", result.content[0].text)

    async def test_tools_auto_allow_permission(self) -> None:
        """The memory tool should be auto-allowed by permission checks."""
        mw = _mw(
            app=_FakeReMeApp(),
            mode="agent_control",
        )
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))

        search_tool = _find_tool(self.toolkit, "memory_search")
        decision_search = await search_tool.check_permissions({}, None)
        self.assertEqual(decision_search.behavior, PermissionBehavior.ALLOW)

    async def test_no_add_memory_tool_exposed(self) -> None:
        """ReMe exposes search only — there is no manual add tool."""
        mw = _mw(
            app=_FakeReMeApp(),
            mode="agent_control",
        )
        tools = await mw.list_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["memory_search"])

    async def test_agent_control_writes_back_automatically(self) -> None:
        """Even with no add tool, agent_control writes via the reply hook."""
        fake = _FakeReMeApp()
        mw = _mw(
            app=fake,
            mode="agent_control",
        )
        agent = await self._agent(mw, responses=["sure thing"])
        # session_id is sourced from the agent, not the middleware.
        agent.state.session_id = "alice-001"
        await agent.reply(UserMsg("user", "remember I like tea"))

        # No auto-retrieval in agent_control...
        self.assertEqual(fake.search_calls, [])
        # ...but the exchange was written back automatically.
        self.assertEqual(len(fake.auto_memory_calls), 1)
        write = fake.auto_memory_calls[0]
        self.assertEqual(write["session_id"], "alice-001")
        written_texts = [_msg_text(m) for m in write["messages"]]
        self.assertIn("remember I like tea", written_texts)
        self.assertIn("sure thing", written_texts)


# ----------------------------------------------------------------------
# Both mode — hooks + tools together
# ----------------------------------------------------------------------


class TestBothMode(IsolatedAsyncioTestCase):
    """Tests for combined static-control and agent-control behavior."""

    async def test_memory_msg_and_tool_hint_both_present(self) -> None:
        """Both mode should inject memory and expose memory tools."""
        model = RecordingMockModel(context_size=100_000)
        fake = _FakeReMeApp(search_return=["auto-injected"])
        mw = _mw(
            app=fake,
            mode="both",
        )
        # Echo tool forces a second reasoning step so the background
        # auto-retrieval injects before the final model call.
        toolkit = Toolkit(tools=[*await mw.list_tools(), _EchoTool()])
        model.set_responses(_echo_then_text("ok"))
        agent = Agent(
            name="a",
            system_prompt="base",
            model=model,
            toolkit=toolkit,
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "hi"))

        # Static hooks ran — one background search (the echo tool call does
        # not search), one write-back.
        self.assertEqual(len(fake.search_calls), 1)
        self.assertEqual(len(fake.auto_memory_calls), 1)

        msgs = model.last_call_messages

        # System prompt has the tool nudge (from on_system_prompt).
        prompt_text = msgs[0].get_text_content()
        self.assertIn("Long-term memory", prompt_text)
        self.assertIn("memory_search", prompt_text)

        # Memory injected as a synthetic HintBlock Msg.
        memory_msgs = [m for m in msgs if getattr(m, "name", None) == "memory"]
        self.assertEqual(len(memory_msgs), 1)
        memory_hints = memory_msgs[0].get_content_blocks("hint")
        self.assertEqual(len(memory_hints), 1)
        self.assertIn("auto-injected", memory_hints[0].hint)

        # Search tool exposed (and no add tool — writing is automatic).
        names = _all_tool_names(toolkit)
        self.assertIn("memory_search", names)
        self.assertNotIn("add_memory", names)
