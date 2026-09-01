# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Unit tests for Mem0Middleware.

The mem0 client itself is mocked — we only exercise the AgentScope hook
wiring (retrieve-before / write-after, system-prompt injection,
list_tools exposure) and the small adapter that translates between
AgentScope and mem0. The OSS ``Memory`` and Platform ``MemoryClient``
share the same call shape (``search(query, filters=..., top_k=...)``
and ``add(messages, user_id=..., agent_id=...)``) so one mock covers
both.

``protected-access`` is disabled because tests legitimately reach into
middleware internals (``mw._client``, ``mw._async_search``, ``mw._top_k``) to
inspect what the public API just did.
"""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from typing import Any

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import HintBlock, Msg, TextBlock, UserMsg
from agentscope.middleware import Mem0Middleware
from agentscope.middleware._longterm_memory._mem0._utils import (
    _extract_memory_texts,
    _extract_query_text,
)
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit


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


class _FakeAsyncMem0Client:
    """Stands in for any of mem0's four client variants.

    All four expose compatible ``search(query, filters=..., top_k=...)``
    and ``add(messages, user_id=..., agent_id=...)`` shapes, so a single
    fake suffices.
    """

    def __init__(self, search_return: Any = None) -> None:
        """Initialize the fake mem0 client."""
        self.search_return = search_return or {"results": []}
        self.search_calls: list[dict] = []
        self.add_calls: list[dict] = []

    async def search(self, query: str, **kwargs: Any) -> Any:
        """Record a mem0 search call and return the configured result."""
        self.search_calls.append({"query": query, **kwargs})
        return self.search_return

    async def add(self, messages: list[dict], **kwargs: Any) -> None:
        """Record a mem0 add call."""
        self.add_calls.append({"messages": messages, **kwargs})


def _single_response(text: str) -> ChatResponse:
    """Build a final single-text chat response."""
    return ChatResponse(
        content=[TextBlock(type="text", text=text)],
        is_last=True,
    )


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
    """Tests for normalizing mem0 search responses into text lists."""

    def test_dict_with_results(self) -> None:
        """Dictionary responses with result dicts should be flattened."""
        raw = {"results": [{"memory": "a"}, {"memory": "b"}]}
        self.assertEqual(_extract_memory_texts(raw), ["a", "b"])

    def test_plain_list_of_dicts(self) -> None:
        """Plain list responses should also be supported."""
        self.assertEqual(
            _extract_memory_texts([{"memory": "only"}]),
            ["only"],
        )

    def test_plain_list_of_strings(self) -> None:
        """String results should pass through unchanged."""
        self.assertEqual(
            _extract_memory_texts({"results": ["x", "y"]}),
            ["x", "y"],
        )

    def test_none_and_garbage(self) -> None:
        """Malformed mem0 outputs should normalize to an empty list."""
        self.assertEqual(_extract_memory_texts(None), [])
        self.assertEqual(_extract_memory_texts({"results": "nope"}), [])


# ----------------------------------------------------------------------
# Constructor validation
# ----------------------------------------------------------------------


class TestConstructorValidation(IsolatedAsyncioTestCase):
    """Tests for Mem0Middleware constructor validation."""

    def test_missing_user_id_raises(self) -> None:
        """Empty user IDs should be rejected."""
        with self.assertRaises(ValueError):
            Mem0Middleware(client=MagicMock(), user_id="")
        with self.assertRaises(ValueError):
            Mem0Middleware(client=MagicMock(), user_id="   ")

    def test_unknown_mode_raises(self) -> None:
        """Unknown control modes should be rejected."""
        with self.assertRaises(ValueError):
            Mem0Middleware(
                client=MagicMock(),
                user_id="alice",
                mode="garbage",  # type: ignore[arg-type]
            )

    def test_neither_client_nor_models_nor_config_raises(self) -> None:
        """At least one backend construction path should be provided."""
        with self.assertRaises(ValueError) as ctx:
            Mem0Middleware(user_id="alice")
        self.assertIn("client", str(ctx.exception))
        self.assertIn("mem0_config", str(ctx.exception))
        self.assertIn("chat_model", str(ctx.exception))

    def test_client_wins_over_other_backend_kwargs_with_warning(
        self,
    ) -> None:
        """``client`` is the escape hatch — when given, the other
        backend kwargs are ignored. A warning is logged so the
        mismatch is not invisible."""
        fake_client = _FakeAsyncMem0Client()
        with self.assertLogs(
            "as",  # the project-wide logger name
            level="WARNING",
        ) as captured:
            mw = Mem0Middleware(
                user_id="alice",
                client=fake_client,
                chat_model=MagicMock(),  # ignored
                embedding_model=MagicMock(),  # ignored
                mem0_config=MagicMock(),  # ignored
            )

        # The fake client wired through unchanged — no AgentScope-
        # adapter construction machinery ran.
        self.assertIs(mw._client, fake_client)

        # Warning mentions all three ignored kwargs.
        joined = "\n".join(captured.output)
        self.assertIn("chat_model", joined)
        self.assertIn("embedding_model", joined)
        self.assertIn("mem0_config", joined)

    def test_client_alone_does_not_warn(self) -> None:
        """Sanity: pure client-only path stays quiet."""
        fake_client = _FakeAsyncMem0Client()
        with self.assertNoLogs("as", level="WARNING"):
            Mem0Middleware(user_id="alice", client=fake_client)

    def test_only_one_of_chat_or_embedding_without_config_raises(
        self,
    ) -> None:
        """Models-only construction requires both chat and embedding."""
        with self.assertRaises(ValueError):
            Mem0Middleware(user_id="alice", chat_model=MagicMock())
        with self.assertRaises(ValueError):
            Mem0Middleware(user_id="alice", embedding_model=MagicMock())


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
        middleware: Mem0Middleware,
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
        fake = _FakeAsyncMem0Client(
            search_return={"results": [{"memory": "alice loves coffee"}]},
        )
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            agent_id="agent_under_test",
            mode="static_control",
        )

        agent = self._agent(mw, response_text="hi alice")
        reply = await agent.reply(UserMsg("user", "remind me what I like"))

        # 1. search called with unified call shape (filters dict, top_k)
        self.assertEqual(len(fake.search_calls), 1)
        self.assertEqual(
            fake.search_calls[0]["query"],
            "remind me what I like",
        )
        self.assertEqual(
            fake.search_calls[0]["filters"],
            {"user_id": "alice", "agent_id": "agent_under_test"},
        )
        self.assertEqual(fake.search_calls[0]["top_k"], 5)

        # 2. write called post-turn with user+assistant pair
        self.assertEqual(len(fake.add_calls), 1)
        self.assertEqual(
            fake.add_calls[0]["messages"],
            [
                {"role": "user", "content": "remind me what I like"},
                {"role": "assistant", "content": "hi alice"},
            ],
        )
        self.assertEqual(fake.add_calls[0]["user_id"], "alice")
        self.assertEqual(fake.add_calls[0]["agent_id"], "agent_under_test")

        # 3. system prompt is unchanged — static_control mode does NOT
        #    add tool instructions (those only fire in agent_control /
        #    both modes via on_system_prompt).
        sys_msg = self.model.last_call_messages[0]
        self.assertEqual(sys_msg.role, "system")
        self.assertEqual(sys_msg.get_text_content(), "base system prompt")

        # 4. memory appended to the agent's persistent context as an
        #    assistant-role hint note named "memory". Formatters convert
        #    HintBlock content into a user message before provider calls.
        memory_msgs = []
        for msg in agent.state.context:
            if getattr(msg, "name", None) == "memory":
                memory_msgs.append(msg)
        self.assertEqual(len(memory_msgs), 1)
        self.assertEqual(memory_msgs[0].role, "assistant")
        memory_hints = memory_msgs[0].get_content_blocks("hint")
        self.assertEqual(len(memory_hints), 1)
        self.assertIsInstance(memory_hints[0], HintBlock)
        memory_text = memory_hints[0].hint
        self.assertIn("Relevant memories", memory_text)
        self.assertIn("alice loves coffee", memory_text)
        # The model saw it on its first call as well.
        delivered = [
            m
            for m in self.model.last_call_messages
            if getattr(m, "name", None) == "memory"
        ]
        self.assertEqual(len(delivered), 1)

        # 5. no agent-control tools registered (in any group)
        all_names = _all_tool_names(self.toolkit)
        self.assertNotIn("search_memory", all_names)
        self.assertNotIn("add_memory", all_names)

        self.assertEqual(reply.get_text_content(), "hi alice")

    async def test_memory_message_lands_after_user_message(self) -> None:
        """Mirroring AgentScope 1.x's ReActAgent placement: the memory
        note is inserted right AFTER the new user input lands in the
        agent context, not before."""
        fake = _FakeAsyncMem0Client(
            search_return={"results": [{"memory": "saved fact"}]},
        )
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            mode="static_control",
        )
        agent = self._agent(mw, response_text="answer")
        await agent.reply(UserMsg("user", "tell me what you know"))

        # context order should be: [...prior history..., USER_MSG,
        # MEMORY_NOTE, ASSISTANT_REPLY (added by the agent loop)]
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
        fake = _FakeAsyncMem0Client(search_return={"results": []})
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            mode="static_control",
        )

        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))

        # System prompt unchanged, no synthetic memory msg present.
        msgs = self.model.last_call_messages
        self.assertEqual(msgs[0].get_text_content(), "base system prompt")
        for m in msgs:
            self.assertNotEqual(getattr(m, "name", None), "memory")
        self.assertEqual(len(fake.search_calls), 1)

    async def test_search_failure_does_not_break_reply(self) -> None:
        """Search errors should be logged but not block the reply."""
        fake = _FakeAsyncMem0Client()
        fake.search = AsyncMock(side_effect=RuntimeError("mem0 down"))
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            mode="static_control",
        )

        agent = self._agent(mw, response_text="still works")
        reply = await agent.reply(UserMsg("user", "ping"))

        self.assertEqual(reply.get_text_content(), "still works")
        # Write still happened — a failed search must not block writes.
        self.assertEqual(len(fake.add_calls), 1)

    async def test_scope_search_by_agent_false_drops_agent_id(
        self,
    ) -> None:
        """Unscoped search should omit agent_id from mem0 filters."""
        fake = _FakeAsyncMem0Client()
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            agent_id="agent_under_test",
            mode="static_control",
            scope_search_by_agent=False,
        )
        agent = self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))

        self.assertNotIn("agent_id", fake.search_calls[0]["filters"])
        # Write still tags both for future scoped queries.
        self.assertEqual(fake.add_calls[0]["agent_id"], "agent_under_test")

    async def test_sync_client_rejected(self) -> None:
        """Sync mem0 clients (``Memory`` / ``MemoryClient``) must be
        rejected at construction time — only async clients are
        supported."""

        class SyncFake:
            """Fake sync mem0 client used to verify rejection."""

            def search(self, query: str, **kwargs: Any) -> Any:
                """Return an empty synchronous search result."""
                return {"results": []}

            def add(self, messages: list, **kwargs: Any) -> None:
                """No-op synchronous add method."""
                return None

        with self.assertRaises(TypeError) as ctx:
            Mem0Middleware(client=SyncFake(), user_id="alice")
        self.assertIn("AsyncMemory", str(ctx.exception))

    async def test_async_method_wrapped_by_sync_decorator_accepted(
        self,
    ) -> None:
        """mem0's hosted ``AsyncMemoryClient`` decorates its async
        methods with ``@api_error_handler`` — a sync wrapper that
        returns the coroutine produced by calling the underlying
        ``async def func``. ``inspect.iscoroutinefunction(wrapper)``
        is False, so the naive check would reject the client; we use
        ``inspect.unwrap`` to peel through ``@functools.wraps`` and
        find the real async function. This test simulates that
        pattern and asserts the wrapped-but-async client is accepted.
        """
        from functools import wraps

        def sync_wraps_async(func: Any) -> Any:
            """Wrap an async function in a sync functools wrapper."""

            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                """Return the coroutine created by the wrapped function."""
                return func(*args, **kwargs)

            return wrapper

        class WrappedFake:
            """Fake client whose async methods are sync-wrapped."""

            @sync_wraps_async
            async def search(self, query: str, **kwargs: Any) -> Any:
                """Return an empty async search result."""
                return {"results": []}

            @sync_wraps_async
            async def add(self, messages: list, **kwargs: Any) -> None:
                """No-op async add method."""
                return None

        # Should NOT raise — the unwrap+iscoroutinefunction check sees
        # through the sync wrapper.
        mw = Mem0Middleware(client=WrappedFake(), user_id="alice")
        self.assertIsInstance(mw._client, WrappedFake)


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
        middleware: Mem0Middleware,
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
        """Agent-control mode should expose tools and prompt guidance."""
        fake = _FakeAsyncMem0Client(
            search_return={"results": [{"memory": "found"}]},
        )
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            mode="agent_control",
        )
        agent = await self._agent(
            mw,
            system_prompt="base prompt",
        )
        await agent.reply(UserMsg("user", "hello"))

        # Tools are listed by the middleware and explicitly passed into
        # the toolkit by the caller.
        basic = _find_group(self.toolkit, "basic")
        names = [t.name for t in basic.tools]
        self.assertIn("search_memory", names)
        self.assertIn("add_memory", names)

        # No automatic search/add ran.
        self.assertEqual(fake.search_calls, [])
        self.assertEqual(fake.add_calls, [])

        # System prompt got a short nudge mentioning the tools — the
        # per-tool guidance comes via the standard tool schema, not
        # this nudge.
        msgs = self.model.last_call_messages
        prompt_text = msgs[0].get_text_content()
        self.assertIn("base prompt", prompt_text)
        self.assertIn("Long-term memory", prompt_text)
        self.assertIn("search_memory", prompt_text)
        self.assertIn("add_memory", prompt_text)
        # No synthetic memory message was injected in agent_control mode.
        for m in msgs:
            self.assertNotEqual(getattr(m, "name", None), "memory")

    async def test_middleware_does_not_mutate_toolkit(self) -> None:
        """Middleware should not register tools unless caller does so."""
        fake = _FakeAsyncMem0Client()
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
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

        self.assertNotIn("search_memory", _all_tool_names(toolkit))
        self.assertNotIn("add_memory", _all_tool_names(toolkit))

    async def test_search_memory_tool_invokes_mem0(self) -> None:
        """The search_memory tool should query mem0 per keyword."""
        fake = _FakeAsyncMem0Client(
            search_return={
                "results": [
                    {"memory": "first fact"},
                    {"memory": "second fact"},
                ],
            },
        )
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            agent_id="a",
            mode="agent_control",
        )
        agent = await self._agent(
            mw,
            system_prompt="base prompt",
        )
        # Trigger middleware tool registration by issuing a reply.
        await agent.reply(UserMsg("user", "hi"))

        search_tool = _find_tool(self.toolkit, "search_memory")

        # Exercise the tool directly. The signature mirrors
        # AgentScope 1.x: a LIST of keywords, each issued as an
        # independent parallel search.
        result = await search_tool(
            keywords=["what does alice like?", "alice preferences"],
            limit=3,
        )
        result_text = _chunk_text(result)
        self.assertIn("first fact", result_text)
        self.assertIn("second fact", result_text)

        # Each keyword produces one mem0 call; both share user/agent
        # filter and per-keyword limit.
        self.assertEqual(len(fake.search_calls), 2)
        for call in fake.search_calls:
            self.assertEqual(
                call["filters"],
                {"user_id": "alice", "agent_id": "a"},
            )
            self.assertEqual(call["top_k"], 3)

    async def test_async_search_accepts_per_call_top_k(self) -> None:
        """Per-call search limits should not mutate middleware state."""
        fake = _FakeAsyncMem0Client()
        mw = Mem0Middleware(
            client=fake,
            user_id="alice",
            mode="agent_control",
            top_k=9,
        )

        await mw._async_search(
            "q",
            user_id="alice",
            agent_id="a",
            top_k=3,
        )

        self.assertEqual(fake.search_calls[0]["top_k"], 3)
        self.assertEqual(mw._top_k, 9)

    async def test_tools_auto_allow_permission(self) -> None:
        """Memory tools should be auto-allowed by permission checks."""
        mw = Mem0Middleware(
            client=_FakeAsyncMem0Client(),
            user_id="alice",
            mode="agent_control",
        )
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))

        from agentscope.permission import PermissionBehavior

        search_tool = _find_tool(self.toolkit, "search_memory")
        add_tool = _find_tool(self.toolkit, "add_memory")
        decision_search = await search_tool.check_permissions({}, None)
        decision_add = await add_tool.check_permissions({}, None)
        self.assertEqual(decision_search.behavior, PermissionBehavior.ALLOW)
        self.assertEqual(decision_add.behavior, PermissionBehavior.ALLOW)

    async def test_search_memory_dedupes_across_keywords(self) -> None:
        """When two keywords return overlapping memories, the tool
        merges and dedupes."""
        fake = _FakeAsyncMem0Client(
            search_return={
                "results": [{"memory": "shared"}, {"memory": "unique"}],
            },
        )
        mw = Mem0Middleware(client=fake, user_id="alice", mode="agent_control")
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        search_tool = _find_tool(self.toolkit, "search_memory")

        result = await search_tool(
            keywords=["kw1", "kw2"],
            limit=5,
        )
        result_text = _chunk_text(result)
        # "shared" appears once even though both keywords returned it.
        self.assertEqual(result_text.count("shared"), 1)
        self.assertEqual(result_text.count("unique"), 1)

    async def test_search_memory_failure_returns_error_chunk(self) -> None:
        """mem0 raising during search produces a ToolChunk with
        state=ERROR so the toolkit marks the call as failed."""
        from agentscope.message import ToolResultState
        from agentscope.tool import ToolChunk

        fake = _FakeAsyncMem0Client()
        fake.search = AsyncMock(side_effect=RuntimeError("mem0 down"))
        mw = Mem0Middleware(client=fake, user_id="alice", mode="agent_control")
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        search_tool = _find_tool(self.toolkit, "search_memory")

        result = await search_tool(
            keywords=["q"],
            limit=5,
        )
        self.assertIsInstance(result, ToolChunk)
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertIn("mem0 down", result.content[0].text)

    async def test_add_memory_two_tier_fallback(self) -> None:
        """When mem0's extraction LLM returns no memories, the tool
        falls back to ``infer=False`` and saves raw text so the
        caller's ``add_memory`` invocation isn't silently dropped.

        Two tiers, not three — v1's tier-2 role switch (user →
        assistant) was a no-op against current mem0 (v2.x), which
        picks the extraction prompt based on the filter dict, not the
        message role. See the docstring of
        ``Mem0Middleware._async_add_with_fallback`` for the full
        rationale."""

        class CountingFake:
            """Fake mem0 client that triggers add fallback once."""

            def __init__(self) -> None:
                """Initialize call tracking and empty search behavior."""
                self.add_calls: list[dict] = []
                self.search = AsyncMock(return_value={"results": []})

            async def add(
                self,
                messages: list[dict],
                **kwargs: Any,
            ) -> dict:
                """Return empty extraction first, then a saved memory."""
                self.add_calls.append({"messages": messages, **kwargs})
                # First attempt: extracted nothing → triggers fallback.
                # Second attempt (with infer=False): succeeds.
                if len(self.add_calls) < 2:
                    return {"results": []}
                return {"results": [{"id": "m1", "memory": "saved"}]}

        fake = CountingFake()
        mw = Mem0Middleware(client=fake, user_id="alice", mode="agent_control")
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        add_tool = _find_tool(self.toolkit, "add_memory")

        result = await add_tool(
            thinking="my reasoning",
            content=["fact one"],
        )
        self.assertIn("Successfully recorded", _chunk_text(result))

        # Exactly 2 calls — the role-switch tier from v1 is gone.
        self.assertEqual(len(fake.add_calls), 2)
        # Both calls use the user role (no more switching).
        self.assertEqual(fake.add_calls[0]["messages"][0]["role"], "user")
        self.assertEqual(fake.add_calls[1]["messages"][0]["role"], "user")
        # First call uses default inference; second explicitly disables it.
        self.assertNotIn("infer", fake.add_calls[0])
        self.assertFalse(fake.add_calls[1]["infer"])

    async def test_add_memory_does_not_persist_thinking(self) -> None:
        """``thinking`` is the agent's rationale — it appears in the
        tool's return text (auditable in the transcript) but is NOT
        sent to mem0, so the stored memory stays clean of agent
        self-narration."""
        fake = _FakeAsyncMem0Client()
        # First attempt extracts something → no fallback.
        fake.add = AsyncMock(
            return_value={"results": [{"id": "m1", "memory": "saved"}]},
        )
        mw = Mem0Middleware(client=fake, user_id="alice", mode="agent_control")
        agent = await self._agent(mw)
        await agent.reply(UserMsg("user", "hi"))
        add_tool = _find_tool(self.toolkit, "add_memory")

        thinking = "user said X because Y"
        fact = "likes coffee"
        result = await add_tool(
            thinking=thinking,
            content=[fact],
        )
        result_text = _chunk_text(result)

        # mem0 only saw the fact, NOT the rationale.
        sent = fake.add.call_args.args[0][0]["content"]
        self.assertIn(fact, sent)
        self.assertNotIn(thinking, sent)

        # ...but the tool return text DOES echo the rationale, so the
        # decision is auditable in the agent transcript.
        self.assertIn(thinking, result_text)


# ----------------------------------------------------------------------
# Both mode — hooks + tools together
# ----------------------------------------------------------------------


class TestBothMode(IsolatedAsyncioTestCase):
    """Tests for combined static-control and agent-control behavior."""

    async def test_memory_msg_and_tool_hint_both_present(self) -> None:
        """Both mode should inject memory and expose memory tools."""
        model = RecordingMockModel(context_size=100_000)
        fake = _FakeAsyncMem0Client(
            search_return={"results": [{"memory": "auto-injected"}]},
        )
        mw = Mem0Middleware(client=fake, user_id="alice", mode="both")
        toolkit = Toolkit(tools=await mw.list_tools())
        model.set_responses([_single_response("ok")])
        agent = Agent(
            name="a",
            system_prompt="base",
            model=model,
            toolkit=toolkit,
            middlewares=[mw],
        )
        await agent.reply(UserMsg("user", "hi"))

        # Static hooks ran.
        self.assertEqual(len(fake.search_calls), 1)
        self.assertEqual(len(fake.add_calls), 1)

        msgs = model.last_call_messages

        # System prompt has the tool nudge (from on_system_prompt).
        prompt_text = msgs[0].get_text_content()
        self.assertIn("Long-term memory", prompt_text)
        self.assertIn("search_memory", prompt_text)

        # Memory injected as a synthetic HintBlock Msg.
        memory_msgs = [m for m in msgs if getattr(m, "name", None) == "memory"]
        self.assertEqual(len(memory_msgs), 1)
        memory_hints = memory_msgs[0].get_content_blocks("hint")
        self.assertEqual(len(memory_hints), 1)
        self.assertIn("auto-injected", memory_hints[0].hint)

        # Tools exposed.
        names = _all_tool_names(toolkit)
        self.assertIn("search_memory", names)
        self.assertIn("add_memory", names)
