# -*- coding: utf-8 -*-
"""Unit tests for AgenticMemoryMiddleware with real Agent execution."""
import os
import shutil
import tempfile
from typing import Any, Type
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import BaseModel
from utils import AnyString, AnyValue, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.message import (
    HintBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit


class _RecordingMockModel(MockModel):
    """A ``MockModel`` that records chat and structured-output calls."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the recording mock model.

        Args:
            **kwargs (`Any`):
                Keyword arguments forwarded to :class:`MockModel`.
        """
        kwargs.setdefault("context_size", 100_000)
        super().__init__(**kwargs)
        self.chat_messages: list[list[Msg]] = []
        self.structured_messages: list[list[Msg]] = []

    async def _call_api(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse:
        """Record the chat messages and delegate to ``MockModel``.

        Args:
            *args (`Any`):
                Positional arguments forwarded to ``MockModel``.
            **kwargs (`Any`):
                Keyword arguments forwarded to ``MockModel``.

        Returns:
            `ChatResponse`:
                The configured mock chat response.
        """
        self.chat_messages.append(kwargs["messages"])
        return await super()._call_api(*args, **kwargs)

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Record structured-output messages and delegate to ``MockModel``.

        Args:
            model_name (`str`):
                The model name.
            messages (`list[Msg]`):
                The structured-output prompt messages.
            structured_model (`Type[BaseModel] | dict`):
                The expected structured-output schema.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to ``MockModel``.

        Returns:
            `StructuredResponse`:
                The configured mock structured response.
        """
        self.structured_messages.append(messages)
        return await super()._call_api_with_structured_output(
            model_name,
            messages,
            structured_model,
            **kwargs,
        )


class _DummyTool(ToolBase):
    """A minimal tool that forces a second Agent reasoning iteration."""

    name: str = "dummy"
    description: str = "A dummy tool for middleware tests."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow every dummy tool call.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                The allow decision.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Dummy tool always allows.",
            message="Dummy tool always allows.",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Return a fixed tool result.

        Args:
            **kwargs (`Any`):
                Ignored tool arguments.

        Returns:
            `ToolChunk`:
                The fixed tool output.
        """
        return ToolChunk(content=[TextBlock(text="tool result")])


def _text_response(text: str) -> ChatResponse:
    """Build a text-only chat response.

    Args:
        text (`str`):
            The response text.

    Returns:
        `ChatResponse`:
            A complete chat response with one text block.
    """
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


def _tool_response() -> ChatResponse:
    """Build a chat response that calls the dummy tool.

    Returns:
        `ChatResponse`:
            A complete chat response with one tool call block.
    """
    return ChatResponse(
        content=[
            ToolCallBlock(
                id="call_dummy",
                name="dummy",
                input="{}",
            ),
        ],
        is_last=True,
    )


def _structured_response(selected_files: list[str]) -> StructuredResponse:
    """Build a structured memory-selection response.

    Args:
        selected_files (`list[str]`):
            The selected memory filenames.

    Returns:
        `StructuredResponse`:
            The structured response consumed by the middleware.
    """
    return StructuredResponse(content={"selected_files": selected_files})


def _block_to_dict(block: Any) -> dict:
    """Convert a message block into a stable assertion dictionary.

    Args:
        block (`Any`):
            The message block to convert.

    Returns:
        `dict`:
            The stable block representation.
    """
    if isinstance(block, TextBlock):
        return {
            "type": "text",
            "text": block.text,
            "id": AnyString(),
        }
    if isinstance(block, HintBlock):
        return {
            "type": "hint",
            "hint": block.hint,
            "id": AnyString(),
            "source": block.source,
        }
    if isinstance(block, ToolCallBlock):
        return {
            "type": "tool_call",
            "id": AnyString(),
            "name": block.name,
            "input": block.input,
            "state": block.state,
            "suggested_rules": block.suggested_rules,
        }
    return block.model_dump()


def _message_to_dict(msg: Msg) -> dict:
    """Convert a message into a stable assertion dictionary.

    Args:
        msg (`Msg`):
            The message to convert.

    Returns:
        `dict`:
            The stable message representation.
    """
    return {
        "id": AnyString(),
        "name": msg.name,
        "role": msg.role,
        "content": [_block_to_dict(block) for block in msg.content],
        "metadata": msg.metadata,
    }


def _hint_texts(agent: Agent) -> list[str]:
    """Collect hint texts from an agent context.

    Args:
        agent (`Agent`):
            The agent whose context is inspected.

    Returns:
        `list[str]`:
            The hint texts in context order.
    """
    return [
        block.hint
        for msg in agent.state.context
        for block in msg.content
        if isinstance(block, HintBlock)
    ]


def _write_memory_file(
    memory_dir: str,
    filename: str,
    description: str,
    memory_type: str,
    body: str,
) -> None:
    """Write one Markdown memory file with frontmatter.

    Args:
        memory_dir (`str`):
            The memory directory.
        filename (`str`):
            The memory filename relative to ``memory_dir``.
        description (`str`):
            The frontmatter description.
        memory_type (`str`):
            The frontmatter memory type.
        body (`str`):
            The Markdown body.
    """
    path = os.path.join(memory_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "---\n"
            f"name: {filename}\n"
            f"description: {description}\n"
            f"type: {memory_type}\n"
            "---\n\n"
            f"{body}\n",
        )


class AgenticMemoryMiddlewareTest(IsolatedAsyncioTestCase):
    """Agent-level tests for :class:`AgenticMemoryMiddleware`."""

    async def asyncSetUp(self) -> None:
        """Create a temporary workspace for each test."""
        self.temp_dir = tempfile.mkdtemp()

    async def asyncTearDown(self) -> None:
        """Remove the temporary workspace after each test."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_agent(
        self,
        model: _RecordingMockModel,
        middleware: AgenticMemoryMiddleware,
        toolkit: Toolkit | None = None,
    ) -> Agent:
        """Build an Agent with the filesystem memory middleware attached.

        Args:
            model (`_RecordingMockModel`):
                The mock model used by the agent.
            middleware (`AgenticMemoryMiddleware`):
                The middleware under test.
            toolkit (`Toolkit | None`, optional):
                The toolkit for the agent. Defaults to an empty toolkit.

        Returns:
            `Agent`:
                The configured agent.
        """
        return Agent(
            name="assistant",
            system_prompt="You are helpful.",
            model=model,
            toolkit=toolkit or Toolkit(),
            middlewares=[middleware],
            # The runtime state injection is covered by
            # agent_injection_test, turn it off to keep the assertions
            # focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def test_agent_reply_creates_layout_and_injects_memory_prompt(
        self,
    ) -> None:
        """Agent reply should create layout and inject memory instructions."""
        model = _RecordingMockModel()
        model.set_responses([_text_response("done")])
        middleware = AgenticMemoryMiddleware(workdir=self.temp_dir)
        agent = self._make_agent(model, middleware)

        reply = await agent.reply(UserMsg("user", "hello"))
        memory_dir = os.path.join(self.temp_dir, "Memory")
        system_prompt = model.chat_messages[0][0].get_text_content()

        self.assertDictEqual(
            {
                "reply": _message_to_dict(reply),
                "memory_dir_exists": os.path.isdir(memory_dir),
                "memory_md_exists": os.path.isfile(
                    os.path.join(memory_dir, "MEMORY.md"),
                ),
                "system_prompt": {
                    "has_memory_dir": memory_dir in system_prompt,
                    "has_placeholder": "{memory_dir}" in system_prompt,
                    "has_memory_header": "## MEMORY.md" in system_prompt,
                    "has_empty_memory_text": (
                        "Your MEMORY.md is currently empty" in system_prompt
                    ),
                },
            },
            {
                "reply": {
                    "id": AnyString(),
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "done",
                            "id": AnyString(),
                        },
                    ],
                    "metadata": {},
                },
                "memory_dir_exists": True,
                "memory_md_exists": True,
                "system_prompt": {
                    "has_memory_dir": True,
                    "has_placeholder": False,
                    "has_memory_header": True,
                    "has_empty_memory_text": True,
                },
            },
        )

    async def test_agent_reasoning_injects_selected_memory_hint(
        self,
    ) -> None:
        """Agent reasoning should inject content selected by structured
        output."""
        memory_dir = os.path.join(self.temp_dir, "Memory")
        os.makedirs(memory_dir)
        with open(
            os.path.join(memory_dir, "MEMORY.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("- [User profile](user_profile.md) — User profile.\n")
        _write_memory_file(
            memory_dir,
            "user_profile.md",
            "User profile details",
            "user",
            "The user prefers concise Chinese answers.",
        )

        model = _RecordingMockModel()
        model.set_structured_response(
            _structured_response(["user_profile.md"]),
        )
        model.set_responses([_tool_response(), _text_response("final answer")])
        middleware = AgenticMemoryMiddleware(workdir=self.temp_dir)
        agent = self._make_agent(
            model,
            middleware,
            toolkit=Toolkit(tools=[_DummyTool()]),
        )

        reply = await agent.reply(UserMsg("user", "what do you remember?"))
        hint_texts = _hint_texts(agent)

        self.assertDictEqual(
            {
                "reply": _message_to_dict(reply),
                "hints": [
                    {
                        "has_selected_content": (
                            "The user prefers concise Chinese answers." in hint
                        ),
                        "has_selected_path": "user_profile.md" in hint,
                    }
                    for hint in hint_texts
                ],
                "context": [
                    _message_to_dict(msg) for msg in agent.state.context
                ],
                "structured_call_count": len(model.structured_messages),
            },
            {
                "reply": {
                    "id": AnyString(),
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "final answer",
                            "id": AnyString(),
                        },
                    ],
                    "metadata": {},
                },
                "hints": [
                    {
                        "has_selected_content": True,
                        "has_selected_path": True,
                    },
                ],
                "context": [
                    {
                        "id": AnyString(),
                        "name": "user",
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "what do you remember?",
                                "id": AnyString(),
                            },
                        ],
                        "metadata": {},
                    },
                    {
                        "id": AnyString(),
                        "name": "assistant",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_call",
                                "id": AnyString(),
                                "name": "dummy",
                                "input": "{}",
                                "state": "finished",
                                "suggested_rules": [],
                            },
                            AnyValue(),
                            {
                                "type": "hint",
                                "hint": AnyString(),
                                "id": AnyString(),
                                "source": None,
                            },
                            {
                                "type": "text",
                                "text": "final answer",
                                "id": AnyString(),
                            },
                        ],
                        "metadata": {},
                    },
                ],
                "structured_call_count": 1,
            },
        )

    async def test_agent_filters_hallucinated_memory_filenames(self) -> None:
        """Agent retrieval should ignore filenames not present in memory."""
        memory_dir = os.path.join(self.temp_dir, "Memory")
        os.makedirs(memory_dir)
        with open(
            os.path.join(memory_dir, "MEMORY.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("- [User profile](user_profile.md) — User profile.\n")
        _write_memory_file(
            memory_dir,
            "user_profile.md",
            "User profile details",
            "user",
            "Only this real memory should be injected.",
        )

        model = _RecordingMockModel()
        model.set_structured_response(
            _structured_response(["user_profile.md", "missing.md"]),
        )
        model.set_responses([_tool_response(), _text_response("filtered")])
        middleware = AgenticMemoryMiddleware(workdir=self.temp_dir)
        agent = self._make_agent(
            model,
            middleware,
            toolkit=Toolkit(tools=[_DummyTool()]),
        )

        await agent.reply(UserMsg("user", "recall memory"))
        hint_texts = _hint_texts(agent)

        self.assertListEqual(
            [
                {
                    "has_real_memory": (
                        "Only this real memory should be injected." in hint
                    ),
                    "has_missing_memory": "missing.md" in hint,
                }
                for hint in hint_texts
            ],
            [
                {
                    "has_real_memory": True,
                    "has_missing_memory": False,
                },
            ],
        )

    async def test_agent_does_not_inject_hint_when_no_file_selected(
        self,
    ) -> None:
        """Agent retrieval should inject no hint when selection is empty."""
        memory_dir = os.path.join(self.temp_dir, "Memory")
        os.makedirs(memory_dir)
        with open(
            os.path.join(memory_dir, "MEMORY.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("- [User profile](user_profile.md) — User profile.\n")
        _write_memory_file(
            memory_dir,
            "user_profile.md",
            "User profile details",
            "user",
            "This memory is available but not selected.",
        )

        model = _RecordingMockModel()
        model.set_structured_response(_structured_response([]))
        model.set_responses([_tool_response(), _text_response("no hint")])
        middleware = AgenticMemoryMiddleware(workdir=self.temp_dir)
        agent = self._make_agent(
            model,
            middleware,
            toolkit=Toolkit(tools=[_DummyTool()]),
        )

        reply = await agent.reply(UserMsg("user", "ignore memories"))

        self.assertDictEqual(
            {
                "reply": _message_to_dict(reply),
                "hints": _hint_texts(agent),
                "structured_call_count": len(model.structured_messages),
            },
            {
                "reply": {
                    "id": AnyString(),
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "no hint",
                            "id": AnyString(),
                        },
                    ],
                    "metadata": {},
                },
                "hints": [],
                "structured_call_count": 1,
            },
        )

    async def test_agent_does_not_retrieve_when_only_memory_index_exists(
        self,
    ) -> None:
        """Agent retrieval should skip structured output without topic
        files."""
        model = _RecordingMockModel()
        model.set_structured_response(_structured_response(["missing.md"]))
        model.set_responses([_tool_response(), _text_response("index only")])
        middleware = AgenticMemoryMiddleware(workdir=self.temp_dir)
        agent = self._make_agent(
            model,
            middleware,
            toolkit=Toolkit(tools=[_DummyTool()]),
        )

        reply = await agent.reply(UserMsg("user", "hello"))

        self.assertDictEqual(
            {
                "reply": _message_to_dict(reply),
                "hints": _hint_texts(agent),
                "structured_call_count": len(model.structured_messages),
                "memory_md_exists": os.path.isfile(
                    os.path.join(self.temp_dir, "Memory", "MEMORY.md"),
                ),
            },
            {
                "reply": {
                    "id": AnyString(),
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "index only",
                            "id": AnyString(),
                        },
                    ],
                    "metadata": {},
                },
                "hints": [],
                "structured_call_count": 0,
                "memory_md_exists": True,
            },
        )

    async def test_agent_system_prompt_contains_truncation_reminder(
        self,
    ) -> None:
        """Agent system prompt should contain reminder for truncated index."""
        memory_dir = os.path.join(self.temp_dir, "Memory")
        os.makedirs(memory_dir)
        with open(
            os.path.join(memory_dir, "MEMORY.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("0123456789" * 80)

        model = _RecordingMockModel()
        model.set_responses([_text_response("truncated")])
        middleware = AgenticMemoryMiddleware(
            workdir=self.temp_dir,
            parameters=AgenticMemoryMiddleware.Parameters(
                memory_max_tokens=10,
                retrieval_async=False,
            ),
        )
        agent = self._make_agent(model, middleware)

        await agent.reply(UserMsg("user", "hello"))
        system_prompt = model.chat_messages[0][0].get_text_content()

        self.assertDictEqual(
            {
                "has_truncated_marker": "<<<TRUNCATED>>>" in system_prompt,
                "has_offset_reminder": "Use the `Read` tool with offset"
                in system_prompt,
                "has_memory_path": os.path.join(memory_dir, "MEMORY.md")
                in system_prompt,
            },
            {
                "has_truncated_marker": True,
                "has_offset_reminder": True,
                "has_memory_path": True,
            },
        )

    async def test_agent_skips_retrieval_when_async_retrieval_disabled(
        self,
    ) -> None:
        """Agent should not run retrieval when ``retrieval_async`` is false."""
        memory_dir = os.path.join(self.temp_dir, "Memory")
        os.makedirs(memory_dir)
        with open(
            os.path.join(memory_dir, "MEMORY.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("- [User profile](user_profile.md) — User profile.\n")
        _write_memory_file(
            memory_dir,
            "user_profile.md",
            "User profile details",
            "user",
            "This memory should not be retrieved.",
        )

        model = _RecordingMockModel()
        model.set_structured_response(
            _structured_response(["user_profile.md"]),
        )
        model.set_responses([_tool_response(), _text_response("disabled")])
        middleware = AgenticMemoryMiddleware(
            workdir=self.temp_dir,
            parameters=AgenticMemoryMiddleware.Parameters(
                retrieval_async=False,
            ),
        )
        agent = self._make_agent(
            model,
            middleware,
            toolkit=Toolkit(tools=[_DummyTool()]),
        )

        reply = await agent.reply(UserMsg("user", "remember?"))

        self.assertDictEqual(
            {
                "reply": _message_to_dict(reply),
                "hints": _hint_texts(agent),
                "structured_call_count": len(model.structured_messages),
            },
            {
                "reply": {
                    "id": AnyString(),
                    "name": "assistant",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "disabled",
                            "id": AnyString(),
                        },
                    ],
                    "metadata": {},
                },
                "hints": [],
                "structured_call_count": 0,
            },
        )
