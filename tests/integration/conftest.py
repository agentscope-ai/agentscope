# -*- coding: utf-8 -*-
"""Configuration and fixtures for ReActAgent integration tests.

This module provides:
1. `FakeChatModel`: A mock chat model that returns predefined responses
2. `fake_react_agent` fixture: A factory for creating ReActAgent instances
3. Helper functions: `create_text_response`, `create_tool_call_response`, etc.

Quick Start Guide:
------------------

### 1. Create a simple chat test (no tools)

```python
async def test_simple_chat(fake_react_agent):
    # Create response sequence
    responses = [
        create_text_response("Hello! How can I help you?"),
    ]

    # Create agent
    agent = fake_react_agent(
        responses=responses,
        name="ChatBot",
        sys_prompt="You are a friendly assistant.",
    )

    # Test
    response = await agent.reply(Msg("user", "Hello!", "user"))
    assert response.get_text_content() == "Hello! How can I help you?"
```

### 2. Create a tool call test

```python
def my_tool(a: int, b: int) -> ToolResponse:
    '''A simple tool that adds two numbers.'''
    result = a + b
    return ToolResponse(
        content=[TextBlock(type="text", text=f"Result: {result}")],
        metadata={"result": result},
    )

async def test_tool_call(fake_react_agent):
    # Create toolkit with tool
    toolkit = Toolkit()
    add_tool(toolkit, "my_tool", "Add two numbers", my_tool)

    # Create response sequence:
    # 1. Model decides to call tool
    # 2. Model returns final answer after seeing tool result
    responses = [
        create_tool_call_response(
            tool_name="my_tool",
            tool_id="call_001",
            tool_input={"a": 2, "b": 3},
            preceding_text="Let me calculate that.",
        ),
        create_text_response("The result is 5."),
    ]

    # Create agent
    agent = fake_react_agent(
        responses=responses,
        name="CalculatorBot",
        sys_prompt="You are a calculator assistant.",
        toolkit=toolkit,
    )

    # Test
    response = await agent.reply(Msg("user", "What is 2 + 3?", "user"))
    assert response is not None
```

### 3. Test max_iters limitation

```python
async def test_max_iters(fake_react_agent):
    toolkit = Toolkit()
    add_tool(toolkit, "concat", "Concatenate strings", concat_strings)

    # Model keeps calling tool (infinite loop)
    responses = [
        create_tool_call_response("concat", "c1", {"a": "a", "b": "b"}),
        create_tool_call_response("concat", "c2", {"a": "c", "b": "d"}),
        create_text_response("Summary: I tried many things."),  # For _summarizing()
    ]

    agent = fake_react_agent(
        responses=responses,
        toolkit=toolkit,
        max_iters=2,  # Limit to 2 iterations
    )

    response = await agent.reply(Msg("user", "Do work", "user"))
    # Agent should terminate after 2 iterations
```

### 4. Test two agents' memory isolation

```python
async def test_memory_isolation(fake_react_agent):
    # Create two separate agents
    agent1 = fake_react_agent(
        responses=[create_text_response("I'm agent 1")],
        name="Agent1",
    )
    agent2 = fake_react_agent(
        responses=[create_text_response("I'm agent 2")],
        name="Agent2",
    )

    # Each agent has its own memory
    await agent1.reply(Msg("user", "I'm user of agent 1", "user"))
    await agent2.reply(Msg("user", "I'm user of agent 2", "user"))

    # agent1's memory should NOT contain agent2's messages
    memories1 = await agent1.memory.get_memory()
    memories2 = await agent2.memory.get_memory()
    assert memories1 != memories2
```

Component Reference:
-------------------

- `FakeChatModel`: Returns responses in sequence. Raises IndexError if called too many times.
- `fake_react_agent(responses, name, sys_prompt, toolkit, max_iters)`: Factory for ReActAgent
- `create_text_response(text)`: Create a simple text response
- `create_tool_call_response(tool_name, tool_id, tool_input, preceding_text)`: Create a tool call response
- `add_tool(toolkit, name, description, func)`: Add a tool to a Toolkit

Note: All tool functions must return `ToolResponse` objects, not raw values.
"""
from typing import Any, AsyncGenerator, Optional, Sequence

import pytest

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import (
    TextBlock,
    ToolUseBlock,
    Msg,
)
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit, ToolResponse


class FakeChatModel(ChatModelBase):
    """A fake chat model that returns predefined responses in sequence.

    This model is used for testing ReActAgent without requiring real API keys.
    It accepts a list of ChatResponse objects and returns them in order
    when called.

    Attributes:
        call_count: Number of times the model has been called.

    Example:
        ```python
        model = FakeChatModel(responses=[
            create_text_response("Hello"),
            create_text_response("Goodbye"),
        ])
        response1 = await model([...])  # Returns "Hello"
        response2 = await model([...])  # Returns "Goodbye"
        response3 = await model([...])  # Raises IndexError
        ```
    """

    def __init__(
        self,
        responses: Sequence[ChatResponse],
        stream: bool = False,
    ) -> None:
        """Initialize the fake chat model.

        Args:
            responses: A sequence of ChatResponse objects to return in order.
            stream: Whether to simulate streaming mode (default: False).
        """
        super().__init__("fake_model", stream=stream)
        self._responses = list(responses)
        self._call_count = 0

    async def __call__(
        self,
        _messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Return the next predefined response.

        Args:
            _messages: The messages (ignored in fake model).
            **kwargs: Additional keyword arguments (ignored).

        Returns:
            The next ChatResponse from the predefined list, or raises
            IndexError if no more responses are available.
        """
        if self._call_count >= len(self._responses):
            raise IndexError(
                f"FakeChatModel has only {len(self._responses)} responses, "
                f"but has been called {self._call_count + 1} times.",
            )

        response = self._responses[self._call_count]
        self._call_count += 1
        return response

    @property
    def call_count(self) -> int:
        """Return the number of times the model has been called."""
        return self._call_count


@pytest.fixture
def fake_react_agent():
    """Create a fixture for building ReActAgent instances with fake responses.

    This fixture returns a factory function that creates a ReActAgent
    configured with a FakeChatModel that returns the provided responses.

    Example:
        def test_something(fake_react_agent):
            responses = [
                ChatResponse(content=[TextBlock(type="text", text="Hello")]),
            ]
            agent = fake_react_agent(responses)
            result = await agent(Msg("user", "Hi", "user"))
    """

    def _create_agent(
        responses: Sequence[ChatResponse],
        name: str = "TestAgent",
        sys_prompt: str = "You are a helpful assistant.",
        toolkit: Optional[Toolkit] = None,
        max_iters: int = 10,
    ) -> ReActAgent:
        """Create a ReActAgent with fake model responses.

        Args:
            responses: Sequence of ChatResponse objects to return.
            name: Agent name (default: "TestAgent").
            sys_prompt: System prompt (default: helpful assistant).
            toolkit: Optional Toolkit with registered tools.
            max_iters: Maximum iterations (default: 10).

        Returns:
            A configured ReActAgent instance.
        """
        model = FakeChatModel(responses=responses)
        formatter = OpenAIChatFormatter()
        memory = InMemoryMemory()

        return ReActAgent(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            toolkit=toolkit or Toolkit(),
            max_iters=max_iters,
        )

    return _create_agent


def create_text_response(text: str) -> ChatResponse:
    """Create a ChatResponse with only text content.

    Args:
        text: The text content.

    Returns:
        A ChatResponse containing a single TextBlock.
    """
    return ChatResponse(
        content=[
            TextBlock(type="text", text=text),
        ],
    )


def create_tool_call_response(
    tool_name: str,
    tool_id: str,
    tool_input: dict[str, Any],
    preceding_text: str = "",
) -> ChatResponse:
    """Create a ChatResponse with a tool call.

    Args:
        tool_name: Name of the tool to call.
        tool_id: Unique ID for the tool call.
        tool_input: Input arguments for the tool.
        preceding_text: Optional text before the tool call.

    Returns:
        A ChatResponse containing a ToolUseBlock and optional TextBlock.
    """
    content: list[TextBlock | ToolUseBlock] = []
    if preceding_text:
        content.append(TextBlock(type="text", text=preceding_text))

    content.append(
        ToolUseBlock(
            type="tool_use",
            name=tool_name,
            id=tool_id,
            input=tool_input,
        ),
    )

    return ChatResponse(content=content)


def create_simple_tool(
    name: str,
    description: str,
    func: Any,
) -> Toolkit:
    """Create a Toolkit with a single simple tool function.

    Args:
        name: Name of the tool.
        description: Description of the tool.
        func: The function to register (must return ToolResponse).

    Returns:
        A Toolkit with the registered tool.
    """
    toolkit = Toolkit()
    toolkit.register_tool_function(func, func_name=name, func_description=description)
    return toolkit


def add_tool(toolkit: Toolkit, name: str, description: str, func: Any) -> None:
    """Add a tool to an existing Toolkit.

    Args:
        toolkit: The Toolkit to add the tool to.
        name: Name of the tool.
        description: Description of the tool.
        func: The function to register (must return ToolResponse).
    """
    toolkit.register_tool_function(func, func_name=name, func_description=description)
