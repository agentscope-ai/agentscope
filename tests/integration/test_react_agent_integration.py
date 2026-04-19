# -*- coding: utf-8 -*-
"""Integration tests for ReActAgent.

This test suite covers end-to-end scenarios for ReActAgent, including:
- Single tool call
- Multi-step tool chain
- Tool error handling
- Max steps limitation
- Empty tool list
- Memory persistence across calls
- Memory isolation between instances and sessions
- SysPrompt coverage
- Tool not found scenario

All tests use fake model responses and simple tools, no external dependencies.
"""
import pytest

from agentscope.agent import ReActAgent
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit, ToolResponse

from tests.integration.conftest import (
    FakeChatModel,
    create_text_response,
    create_tool_call_response,
    add_tool,
)


def add_numbers(a: int, b: int) -> ToolResponse:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        ToolResponse with the sum.
    """
    result = a + b
    return ToolResponse(
        content=[
            TextBlock(type="text", text=f"The sum of {a} and {b} is {result}"),
        ],
        metadata={"result": result},
    )


def multiply_numbers(a: int, b: int) -> ToolResponse:
    """Multiply two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        ToolResponse with the product.
    """
    result = a * b
    return ToolResponse(
        content=[
            TextBlock(type="text", text=f"The product of {a} and {b} is {result}"),
        ],
        metadata={"result": result},
    )


def get_weather(city: str) -> ToolResponse:
    """Get the weather for a city.

    Args:
        city: City name.

    Returns:
        ToolResponse with weather info.
    """
    weather_data = {
        "Beijing": {"temp": 25, "condition": "sunny"},
        "Shanghai": {"temp": 28, "condition": "cloudy"},
        "Shenzhen": {"temp": 30, "condition": "rainy"},
    }
    info = weather_data.get(city, {"temp": 20, "condition": "unknown"})
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Weather in {city}: {info['condition']}, {info['temp']}°C",
            ),
        ],
        metadata=info,
    )


def get_clothing_suggestion(temp: int, condition: str) -> ToolResponse:
    """Get clothing suggestion based on weather.

    Args:
        temp: Temperature in Celsius.
        condition: Weather condition (sunny, cloudy, rainy, etc.)

    Returns:
        ToolResponse with clothing suggestion.
    """
    if temp < 10:
        clothing = "Wear a heavy coat, scarf, and gloves."
    elif temp < 20:
        clothing = "Wear a light jacket or sweater."
    else:
        clothing = "Wear light and comfortable clothing."

    if condition == "rainy":
        clothing += " Don't forget an umbrella!"

    return ToolResponse(
        content=[
            TextBlock(type="text", text=f"Clothing suggestion: {clothing}"),
        ],
    )


def raise_error_tool(message: str) -> ToolResponse:
    """A tool that raises an error.

    Args:
        message: Error message to raise.

    Raises:
        ValueError: Always raises with the given message.
    """
    raise ValueError(f"Tool error: {message}")


def concat_strings(a: str, b: str) -> ToolResponse:
    """Concatenate two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        ToolResponse with concatenated result.
    """
    result = a + b
    return ToolResponse(
        content=[
            TextBlock(type="text", text=f"Concatenated: '{result}'"),
        ],
        metadata={"result": result},
    )


class TestReActAgentSingleToolCall:
    """Tests for single tool call scenario.

    Scenario: Agent receives user request, calls one tool,
    and returns the tool result in the final answer.
    """

    @pytest.mark.asyncio
    async def test_single_tool_call_add_numbers(self, fake_react_agent):
        """Test: Agent calls add_numbers tool and returns result.

        Given:
            - A ReActAgent with add_numbers tool registered
            - Fake model responses that:
              1. First call tool to add numbers
              2. Then return final answer with result

        When:
            - User asks "What is 2 plus 3?"

        Then:
            - Agent should call the add_numbers tool
            - Agent should return a final answer
            - Memory should contain the tool call and result
        """
        toolkit = Toolkit()
        add_tool(
            toolkit,
            "add_numbers",
            "Add two numbers. Use this when you need to calculate the sum of two numbers.",
            add_numbers,
        )

        responses = [
            create_tool_call_response(
                tool_name="add_numbers",
                tool_id="tool_call_001",
                tool_input={"a": 2, "b": 3},
                preceding_text="Let me calculate that for you.",
            ),
            create_text_response("The result of 2 plus 3 is 5."),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="CalculatorAgent",
            sys_prompt="You are a helpful calculator assistant. Use tools to perform calculations.",
            toolkit=toolkit,
        )

        user_msg = Msg("user", "What is 2 plus 3?", "user")
        response = await agent.reply(user_msg)

        assert response is not None
        assert response.role == "assistant"

        memories = await agent.memory.get_memory()
        tool_use_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_use")
        )
        tool_result_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_result")
        )

        assert tool_use_count >= 1, "Expected at least one tool use in memory"
        assert tool_result_count >= 1, "Expected at least one tool result in memory"


class TestReActAgentMultiStepToolChain:
    """Tests for multi-step tool chain scenario.

    Scenario: Agent needs to call multiple tools in sequence,
    where each step's observation is passed to the next step's reasoning.

    Example flow:
    1. User asks: "What should I wear in Beijing today?"
    2. Agent calls get_weather("Beijing") → gets temp=25, condition=sunny
    3. Agent calls get_clothing_suggestion(25, "sunny") → gets suggestion
    4. Agent returns final answer with suggestion
    """

    @pytest.mark.asyncio
    async def test_multi_step_tool_chain_weather_and_clothing(self, fake_react_agent):
        """Test: Agent chains get_weather and get_clothing_suggestion tools.

        Given:
            - A ReActAgent with get_weather and get_clothing_suggestion tools
            - Fake model responses that:
              1. First call get_weather for Beijing
              2. After getting weather result, call get_clothing_suggestion
              3. Return final answer with clothing suggestion

        When:
            - User asks "What should I wear in Beijing today?"

        Then:
            - Agent should call both tools in sequence
            - Both tool results should be in memory
            - Agent should return a final answer
        """
        toolkit = Toolkit()
        add_tool(
            toolkit,
            "get_weather",
            "Get weather information for a city. Returns temperature and condition.",
            get_weather,
        )
        add_tool(
            toolkit,
            "get_clothing_suggestion",
            "Get clothing suggestion based on temperature and weather condition.",
            get_clothing_suggestion,
        )

        responses = [
            create_tool_call_response(
                tool_name="get_weather",
                tool_id="tool_call_weather_001",
                tool_input={"city": "Beijing"},
                preceding_text="Let me check the weather in Beijing first.",
            ),
            create_tool_call_response(
                tool_name="get_clothing_suggestion",
                tool_id="tool_call_clothing_001",
                tool_input={"temp": 25, "condition": "sunny"},
                preceding_text="Now I'll suggest clothing based on the weather.",
            ),
            create_text_response(
                "In Beijing, it's sunny and 25°C. You should wear light and comfortable clothing."
            ),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="WeatherAssistant",
            sys_prompt="You are a weather and clothing assistant. "
            "First check the weather, then suggest appropriate clothing.",
            toolkit=toolkit,
        )

        user_msg = Msg("user", "What should I wear in Beijing today?", "user")
        response = await agent.reply(user_msg)

        assert response is not None
        assert response.role == "assistant"

        memories = await agent.memory.get_memory()
        tool_use_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_use")
        )
        tool_result_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_result")
        )

        assert tool_use_count >= 2, f"Expected at least 2 tool uses, got {tool_use_count}"
        assert tool_result_count >= 2, f"Expected at least 2 tool results, got {tool_result_count}"


class TestReActAgentToolErrorHandling:
    """Tests for tool error handling scenario.

    Scenario: A tool throws an exception during execution.
    Agent should catch the error, report it to the user, and not crash.
    """

    @pytest.mark.asyncio
    async def test_tool_raises_exception_agent_handles_it(self, fake_react_agent):
        """Test: Agent handles tool exception gracefully.

        Given:
            - A ReActAgent with a tool that raises ValueError
            - Fake model responses that:
              1. Call the error-raising tool
              2. After getting error, return error message to user

        When:
            - User asks something that triggers the error tool

        Then:
            - Agent should not crash
            - Agent should return a response
            - Tool result with error should be in memory
        """
        toolkit = Toolkit()
        add_tool(
            toolkit,
            "raise_error_tool",
            "A tool that raises an error. Use for testing error handling.",
            raise_error_tool,
        )

        responses = [
            create_tool_call_response(
                tool_name="raise_error_tool",
                tool_id="tool_call_error_001",
                tool_input={"message": "Something went wrong"},
                preceding_text="Let me try to execute this operation.",
            ),
            create_text_response(
                "I encountered an error while trying to help you: Tool error: Something went wrong. "
                "Please try again with different parameters."
            ),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="ErrorTestAgent",
            sys_prompt="You are a test agent. Handle errors gracefully and report them to the user.",
            toolkit=toolkit,
        )

        user_msg = Msg("user", "Please execute the error tool for testing.", "user")

        response = None
        try:
            response = await agent.reply(user_msg)
        except Exception as e:
            pytest.fail(f"Agent crashed with exception: {e}")

        assert response is not None, "Agent should return a response even after tool error"
        assert response.role == "assistant"

        memories = await agent.memory.get_memory()
        tool_result_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_result")
        )
        assert tool_result_count >= 1, "Expected tool result (with error) in memory"


class TestReActAgentMaxStepsLimitation:
    """Tests for max steps limitation scenario.

    Scenario: Agent keeps calling tools in a loop or indefinitely.
    Agent should be terminated after reaching max_iters and return a signal.
    """

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=False,
        reason="Feature not implemented: ReActAgent does not set max_steps_reached signal in metadata. "
        "Current implementation calls _summarizing() to generate a summary, but does not "
        "indicate in metadata, logger, or exception that max_steps was reached. "
        "Compare: handle_interrupt() sets _is_interrupted: True in metadata. "
        "Next task should let ReActAgent identify and report max_steps_reached.",
    )
    async def test_max_steps_reached_signal(self, fake_react_agent):
        """Test: Agent should signal max_steps_reached when max_iters is reached.

        **Expected behavior (not yet implemented):**
        When max_iters is reached, the agent should:
        - Set a signal (e.g., `_max_steps_reached: True`) in the response metadata
        - Or log a warning
        - Or raise a specific exception

        **Current actual behavior (observed from src/agentscope/agent/_react_agent.py):**
        - Lines 520-525: When `reply_msg is None` after loop, calls `_summarizing()`
        - `_summarizing()` sends hint to model: "You have failed to generate response within
          the maximum iterations. Now respond directly by summarizing the current situation."
        - Then calls model again to generate summary
        - **NO signal** is set in metadata (unlike `handle_interrupt()` which sets `_is_interrupted: True`)
        - **NO warning** is logged
        - **NO exception** is raised

        Given:
            - A ReActAgent with max_iters=2
            - Fake model responses that always call a tool (creating infinite loop)
            - Extra response for _summarizing() to call

        When:
            - Agent tries to keep calling tools

        Then:
            - Response should have max_steps_reached signal in metadata
            - Or warning should be logged
            - Or specific exception should be raised
        """
        toolkit = Toolkit()
        add_tool(
            toolkit,
            "concat_strings",
            "Concatenate two strings.",
            concat_strings,
        )

        responses = [
            create_tool_call_response(
                tool_name="concat_strings",
                tool_id="tool_call_1",
                tool_input={"a": "Hello", "b": " World"},
                preceding_text="Let me concatenate these strings.",
            ),
            create_tool_call_response(
                tool_name="concat_strings",
                tool_id="tool_call_2",
                tool_input={"a": "Another", "b": " call"},
                preceding_text="Let me do more concatenation.",
            ),
            create_text_response("Summary: I tried to concatenate strings but couldn't finish."),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="LoopTestAgent",
            sys_prompt="You are a test agent that keeps calling tools.",
            toolkit=toolkit,
            max_iters=2,
        )

        user_msg = Msg("user", "Please help me concatenate some strings.", "user")
        response = await agent.reply(user_msg)

        assert response is not None
        assert response.role == "assistant"

        max_steps_reached = response.metadata and response.metadata.get(
            "_max_steps_reached"
        )
        assert max_steps_reached is True, (
            "Expected _max_steps_reached: True in response metadata. "
            f"Got metadata: {response.metadata}"
        )

    @pytest.mark.asyncio
    async def test_max_steps_termination_no_crash(self, fake_react_agent, caplog):
        """Test: Agent does not crash when reaching max_iters.

        This is a positive test for current behavior: agent should not crash.

        Given:
            - A ReActAgent with max_iters=2
            - Fake model responses that always call a tool
            - Extra response for _summarizing()

        When:
            - Agent tries to keep calling tools

        Then:
            - Agent should not crash
            - Should return a response
        """
        toolkit = Toolkit()
        add_tool(
            toolkit,
            "concat_strings",
            "Concatenate two strings.",
            concat_strings,
        )

        responses = [
            create_tool_call_response(
                tool_name="concat_strings",
                tool_id="tool_call_1",
                tool_input={"a": "Hello", "b": " World"},
                preceding_text="Let me concatenate these strings.",
            ),
            create_tool_call_response(
                tool_name="concat_strings",
                tool_id="tool_call_2",
                tool_input={"a": "Another", "b": " call"},
                preceding_text="Let me do more concatenation.",
            ),
            create_text_response("Summary: I tried to concatenate strings but couldn't finish."),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="LoopTestAgent",
            sys_prompt="You are a test agent that keeps calling tools.",
            toolkit=toolkit,
            max_iters=2,
        )

        user_msg = Msg("user", "Please help me concatenate some strings.", "user")

        response = None
        try:
            response = await agent.reply(user_msg)
        except Exception as e:
            pytest.fail(f"Agent crashed when reaching max_iters: {e}")

        assert response is not None
        assert response.role == "assistant"


class TestReActAgentEmptyToolList:
    """Tests for empty tool list scenario.

    Scenario 1: Agent has no tools, model returns text → normal chat.
    Scenario 2: Agent has no tools, model returns tool_call → should handle tool not found.
    """

    @pytest.mark.asyncio
    async def test_agent_without_tools_chats_normally(self, fake_react_agent):
        """Test: Agent with empty toolkit chats like a normal assistant.

        Given:
            - A ReActAgent with empty toolkit (no tools registered)
            - Fake model responses that return text only (no tool calls)

        When:
            - User asks a normal question like "Hello, how are you?"

        Then:
            - Agent should not crash
            - Agent should return a text response
            - No tool calls should be made
        """
        responses = [
            create_text_response("Hello! I'm doing well, thank you for asking. How can I help you today?"),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="ChatAgent",
            sys_prompt="You are a friendly chat assistant.",
            toolkit=Toolkit(),
        )

        user_msg = Msg("user", "Hello, how are you?", "user")
        response = await agent.reply(user_msg)

        assert response is not None
        assert response.role == "assistant"

        text_content = response.get_text_content()
        assert text_content is not None, "Response should have text content"

        memories = await agent.memory.get_memory()
        tool_use_count = sum(
            1 for msg in memories if msg.has_content_blocks("tool_use")
        )
        assert tool_use_count == 0, f"Expected no tool calls, got {tool_use_count}"

    @pytest.mark.asyncio
    async def test_model_says_tool_call_but_no_tool_registered(self, fake_react_agent):
        """Test: Model returns tool_call but tool is not registered.

        This tests the edge case where:
        - Agent has no tools registered (empty toolkit)
        - Fake model returns a tool_call response (simulating a misbehaving model)

        Expected behavior (from _toolkit.py:call_tool_function):
        - Line 873-885: If tool not in self.tools, returns FunctionNotFoundError
        - The error should be captured in ToolResultBlock in memory

        Given:
            - A ReActAgent with empty toolkit
            - Fake model response that calls a non-existent tool
            - Second response that acknowledges the error

        When:
            - Agent receives user message and model tries to call non-existent tool

        Then:
            - Agent should not crash
            - Tool result with error (FunctionNotFoundError) should be in memory
            - Agent should return a response
        """
        responses = [
            create_tool_call_response(
                tool_name="non_existent_tool",
                tool_id="tool_call_ghost",
                tool_input={"param": "value"},
                preceding_text="Let me try to use this tool.",
            ),
            create_text_response(
                "I tried to use a tool but it doesn't seem to be available. "
                "Let me try a different approach."
            ),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="GhostToolTestAgent",
            sys_prompt="You are a test agent.",
            toolkit=Toolkit(),
        )

        user_msg = Msg("user", "Please help me with this task.", "user")

        response = None
        try:
            response = await agent.reply(user_msg)
        except Exception as e:
            pytest.fail(f"Agent crashed when model called non-existent tool: {e}")

        assert response is not None, "Agent should return a response"
        assert response.role == "assistant"

        memories = await agent.memory.get_memory()
        tool_result_blocks = []
        for msg in memories:
            tool_result_blocks.extend(msg.get_content_blocks("tool_result"))

        assert len(tool_result_blocks) >= 1, (
            "Expected at least one tool_result block in memory. "
            f"Tool result blocks: {tool_result_blocks}"
        )

        error_found = False
        for block in tool_result_blocks:
            output = block.get("output", "")
            if isinstance(output, str):
                if "FunctionNotFoundError" in output or "non_existent_tool" in output:
                    error_found = True
                    break
            elif isinstance(output, list):
                for item in output:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if "FunctionNotFoundError" in text or "non_existent_tool" in text:
                            error_found = True
                            break

        assert error_found, (
            "Expected FunctionNotFoundError or 'non_existent_tool' in tool_result output. "
            f"Tool result blocks: {tool_result_blocks}"
        )


class TestReActAgentMemoryIsolation:
    """Tests for memory isolation.

    Scenarios:
    1. Two independent ReActAgent instances should have separate memories.
    2. Same ReActAgent instance with different sessions should have isolated memory.
    """

    @pytest.mark.asyncio
    async def test_two_agents_memory_isolated(self, fake_react_agent):
        """Test: Two independent ReActAgent instances have separate memories.

        Given:
            - Two separate ReActAgent instances: agent_alice and agent_bob
            - Each has their own conversation

        When:
            - agent_alice talks about "Alice's secret"
            - agent_bob talks about "Bob's secret"

        Then:
            - agent_alice's memory should NOT contain Bob's messages
            - agent_bob's memory should NOT contain Alice's messages
            - The two agents are completely independent
        """
        responses_alice = [
            create_text_response("Hello Alice! I've noted your secret: Alice's secret."),
        ]
        responses_bob = [
            create_text_response("Hello Bob! I've noted your secret: Bob's secret."),
        ]

        agent_alice = fake_react_agent(
            responses=responses_alice,
            name="AliceAssistant",
            sys_prompt="You are Alice's personal assistant.",
            toolkit=Toolkit(),
        )

        agent_bob = fake_react_agent(
            responses=responses_bob,
            name="BobAssistant",
            sys_prompt="You are Bob's personal assistant.",
            toolkit=Toolkit(),
        )

        msg_alice = Msg("user", "My name is Alice and my secret is: Alice's secret", "user")
        msg_bob = Msg("user", "My name is Bob and my secret is: Bob's secret", "user")

        await agent_alice.reply(msg_alice)
        await agent_bob.reply(msg_bob)

        memories_alice = await agent_alice.memory.get_memory()
        memories_bob = await agent_bob.memory.get_memory()

        alice_texts = " ".join(
            str(m.get_text_content() or "") for m in memories_alice
        )
        bob_texts = " ".join(
            str(m.get_text_content() or "") for m in memories_bob
        )

        assert "Alice's secret" in alice_texts, "Alice's memory should have Alice's secret"
        assert "Bob's secret" not in alice_texts, "Alice's memory should NOT have Bob's secret"

        assert "Bob's secret" in bob_texts, "Bob's memory should have Bob's secret"
        assert "Alice's secret" not in bob_texts, "Bob's memory should NOT have Alice's secret"

    @pytest.mark.asyncio
    async def test_agent_state_dict_contains_memory(self, fake_react_agent):
        """Test: ReActAgent state_dict contains memory state.

        This verifies that the agent's state (including memory) can be
        serialized via state_dict(), which is the foundation for session isolation.

        Note: In AgentScope, session isolation is achieved via:
        - save_session_state(): saves state_modules to storage
        - load_session_state(): loads state_modules from storage

        Given:
            - A ReActAgent instance
            - User sends a message, agent replies

        When:
            - Call agent.state_dict()

        Then:
            - state_dict should contain 'memory' key
            - Memory content should be in the state
        """
        responses = [
            create_text_response("Hello! I remember your message."),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="StateTestAgent",
            sys_prompt="You are a test assistant.",
            toolkit=Toolkit(),
        )

        msg = Msg("user", "Please remember this message: TestSession123", "user")
        await agent.reply(msg)

        state = agent.state_dict()

        assert "memory" in state, (
            "state_dict should contain 'memory' key. "
            f"State keys: {list(state.keys())}"
        )

        memory_state = state["memory"]
        assert "content" in memory_state, (
            "memory state should contain 'content'. "
            f"Memory state keys: {list(memory_state.keys())}"
        )


class TestReActAgentSysPromptCoverage:
    """Tests for sys_prompt coverage.

    Verify that the sys_prompt passed during creation is actually used.
    """

    @pytest.mark.asyncio
    async def test_sys_prompt_stored_in_state(self, fake_react_agent):
        """Test: sys_prompt is stored in agent's state.

        From _react_agent.py:363-364:
            self.register_state("name")
            self.register_state("_sys_prompt")

        So _sys_prompt should be in state_dict().

        Given:
            - A ReActAgent with a specific sys_prompt

        When:
            - Check agent._sys_prompt
            - Check agent.sys_prompt property
            - Check agent.state_dict()

        Then:
            - _sys_prompt should match the provided sys_prompt
            - state_dict should contain '_sys_prompt'
        """
        custom_sys_prompt = (
            "You are a specialized weather assistant. "
            "Your name is WeatherBot. "
            "Always start your response with 'WeatherBot says:'."
        )

        responses = [
            create_text_response("WeatherBot says: Hello! How can I help you with weather?"),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="WeatherBot",
            sys_prompt=custom_sys_prompt,
            toolkit=Toolkit(),
        )

        assert hasattr(agent, "_sys_prompt"), "Agent should have _sys_prompt attribute"
        assert agent._sys_prompt == custom_sys_prompt, (
            f"_sys_prompt should match. "
            f"Expected: {custom_sys_prompt}, Got: {agent._sys_prompt}"
        )

        state = agent.state_dict()
        assert "_sys_prompt" in state, (
            f"state_dict should contain '_sys_prompt'. "
            f"State keys: {list(state.keys())}"
        )
        assert state["_sys_prompt"] == custom_sys_prompt, (
            f"state_dict['_sys_prompt'] should match. "
            f"Expected: {custom_sys_prompt}, Got: {state['_sys_prompt']}"
        )

    @pytest.mark.asyncio
    async def test_sys_prompt_property_returns_sys_prompt(self, fake_react_agent):
        """Test: sys_prompt property returns the stored _sys_prompt.

        Note: Regular tools (registered via register_tool_function) are NOT
        included in sys_prompt. Tools are passed to the model via the `tools`
        parameter in each model call, not via the system prompt.

        The sys_prompt property only appends agent_skill_prompt (from
        `Toolkit.skills`, a different mechanism than regular tools).

        Given:
            - A ReActAgent with a custom sys_prompt
            - With or without tools registered

        When:
            - Check agent.sys_prompt property
            - Check agent._sys_prompt attribute

        Then:
            - _sys_prompt should equal the provided sys_prompt
            - sys_prompt property should include _sys_prompt
        """
        custom_sys_prompt = "You are a calculator assistant. Always be precise."

        toolkit = Toolkit()
        add_tool(
            toolkit,
            "add_numbers",
            "Add two numbers.",
            add_numbers,
        )

        responses = [
            create_text_response("The sum is 5."),
        ]

        agent = fake_react_agent(
            responses=responses,
            name="CalculatorBot",
            sys_prompt=custom_sys_prompt,
            toolkit=toolkit,
        )

        assert agent._sys_prompt == custom_sys_prompt, (
            f"_sys_prompt should equal the provided sys_prompt. "
            f"Expected: {custom_sys_prompt}, Got: {agent._sys_prompt}"
        )

        sys_prompt_property = agent.sys_prompt
        assert custom_sys_prompt in sys_prompt_property, (
            f"sys_prompt property should include _sys_prompt. "
            f"sys_prompt: {sys_prompt_property}"
        )


class TestReActAgentMemoryPersistence:
    """Tests for memory persistence across multiple calls.

    Scenario: User has a conversation across multiple agent.reply() calls.
    The conversation history should be correctly accumulated.
    """

    @pytest.mark.asyncio
    async def test_memory_accumulates_across_calls(self, fake_react_agent):
        """Test: Memory accumulates conversation history across multiple calls.

        Given:
            - A ReActAgent with no tools (simple chat)
            - First exchange: User says "My name is Alice"
            - Second exchange: User asks "What's my name?"

        When:
            - Agent replies to both messages

        Then:
            - After first call, memory should contain user's first message and agent's reply
            - After second call, memory should contain all four messages
            - Agent should have access to previous context
        """
        responses_call_1 = [
            create_text_response("Nice to meet you, Alice!"),
        ]

        responses_call_2 = [
            create_text_response("Your name is Alice."),
        ]

        toolkit = Toolkit()

        agent = fake_react_agent(
            responses=responses_call_1 + responses_call_2,
            name="MemoryTestAgent",
            sys_prompt="You are a helpful assistant. Remember what the user tells you.",
            toolkit=toolkit,
        )

        msg_1 = Msg("user", "My name is Alice.", "user")
        response_1 = await agent.reply(msg_1)

        assert response_1 is not None
        memories_after_1 = await agent.memory.get_memory()
        assert len(memories_after_1) >= 2, "Memory should have at least 2 messages after first call"

        msg_2 = Msg("user", "What's my name?", "user")
        response_2 = await agent.reply(msg_2)

        assert response_2 is not None
        memories_after_2 = await agent.memory.get_memory()
        assert len(memories_after_2) >= 4, "Memory should have at least 4 messages after second call"

        user_messages = [m for m in memories_after_2 if m.role == "user"]
        assert len(user_messages) >= 2, "Should have at least 2 user messages"
