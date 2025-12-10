# -*- coding: utf-8 -*-
"""Unit tests for the tracing extractor module."""
from unittest import TestCase
from unittest.mock import Mock

from agentscope.message import Msg, TextBlock, ToolUseBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.agent import AgentBase
from agentscope.tool import Toolkit
from agentscope.formatter import FormatterBase
from agentscope.embedding import EmbeddingModelBase
from agentscope.tracing._extractor import (
    _get_common_attributes,
    _get_format_target,
    _get_provider_name,
    _get_tool_definitions,
    _get_llm_request_attributes,
    _get_llm_span_name,
    _get_llm_output_messages,
    _get_llm_response_attributes,
    _get_agent_messages,
    _get_agent_request_attributes,
    _get_agent_span_name,
    _get_agent_response_attributes,
    _get_tool_request_attributes,
    _get_tool_span_name,
    _get_tool_response_attributes,
    _get_formatter_request_attributes,
    _get_formatter_span_name,
    _get_formatter_response_attributes,
    _get_generic_function_request_attributes,
    _get_generic_function_span_name,
    _get_generic_function_response_attributes,
    _get_embedding_request_attributes,
    _get_embedding_span_name,
    _get_embedding_response_attributes,
)
from agentscope.tracing._attributes import (
    SpanAttributes,
    OperationNameValues,
    ProviderNameValues,
)


class ExtractorTest(TestCase):
    """Test cases for the extractor module."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = Mock(spec=ChatModelBase)
        self.mock_model.model_name = "test-model"
        self.mock_model.__class__.__name__ = "TestChatModel"

        self.mock_agent = Mock(spec=AgentBase)
        self.mock_agent.id = "agent-1"
        self.mock_agent.name = "TestAgent"
        self.mock_agent.__class__.__doc__ = "Test agent description"

        self.mock_formatter = Mock(spec=FormatterBase)
        self.mock_formatter.__class__.__name__ = "OpenAIChatFormatter"

        self.mock_embedding = Mock(spec=EmbeddingModelBase)
        self.mock_embedding.model_name = "embedding-model"

    def test_get_common_attributes(self) -> None:
        """Test _get_common_attributes."""
        from agentscope import _config

        original_run_id = _config.run_id
        _config.run_id = "test-run-id"

        try:
            attributes = _get_common_attributes()
            self.assertIn(SpanAttributes.GEN_AI_CONVERSATION_ID, attributes)
            self.assertEqual(
                attributes[SpanAttributes.GEN_AI_CONVERSATION_ID],
                '"test-run-id"',
            )
        finally:
            _config.run_id = original_run_id

    def test_get_format_target(self) -> None:
        """Test _get_format_target."""
        # Test OpenAI formatter
        formatter = Mock()
        formatter.__class__.__name__ = "OpenAIChatFormatter"
        self.assertEqual(
            _get_format_target(formatter),
            ProviderNameValues.OPENAI,
        )

        # Test DashScope formatter
        formatter.__class__.__name__ = "DashScopeChatFormatter"
        self.assertEqual(
            _get_format_target(formatter),
            ProviderNameValues.DASHSCOPE,
        )

        # Test unknown formatter
        formatter.__class__.__name__ = "UnknownFormatter"
        self.assertEqual(_get_format_target(formatter), "unknown")

    def test_get_provider_name(self) -> None:
        """Test _get_provider_name."""
        # Test OpenAI model
        model = Mock(spec=ChatModelBase)
        model.__class__.__name__ = "OpenAIChatModel"
        model.client = Mock()
        model.client.base_url = "https://api.openai.com/v1"
        self.assertEqual(_get_provider_name(model), ProviderNameValues.OPENAI)

        # Test DashScope model
        model.__class__.__name__ = "DashScopeChatModel"
        self.assertEqual(
            _get_provider_name(model),
            ProviderNameValues.DASHSCOPE,
        )

        # Test OpenAI model with custom base_url
        model.__class__.__name__ = "OpenAIChatModel"
        model.client.base_url = "https://api.deepseek.com/v1"
        self.assertEqual(
            _get_provider_name(model),
            ProviderNameValues.DEEPSEEK,
        )

        # Test model without base_url
        model.client.base_url = None
        self.assertEqual(_get_provider_name(model), ProviderNameValues.OPENAI)

    def test_get_tool_definitions(self) -> None:
        """Test _get_tool_definitions."""
        # Test with valid tools
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        result = _get_tool_definitions(tools, "auto")
        self.assertIsNotNone(result)
        self.assertIn("test_tool", result)

        # Test with None tools
        self.assertIsNone(_get_tool_definitions(None, "auto"))

        # Test with empty tools
        self.assertIsNone(_get_tool_definitions([], "auto"))

        # Test with tool_choice="none"
        self.assertIsNone(_get_tool_definitions(tools, "none"))

        # Test with invalid tool format
        invalid_tools = [{"type": "function"}]
        self.assertIsNone(_get_tool_definitions(invalid_tools, "auto"))

    def test_get_llm_request_attributes(self) -> None:
        """Test _get_llm_request_attributes and _get_llm_span_name."""
        args = ()
        kwargs = {
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,
            "max_tokens": 100,
            "presence_penalty": 0.1,
            "frequency_penalty": 0.2,
            "stop_sequences": ["stop"],
            "seed": 42,
        }

        attributes = _get_llm_request_attributes(self.mock_model, args, kwargs)

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.CHAT,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_REQUEST_MODEL],
            "test-model",
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_REQUEST_TEMPERATURE],
            0.7,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_REQUEST_TOP_P],
            0.9,
        )
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_INPUT, attributes)

        # Test span name generation
        span_name = _get_llm_span_name(attributes)
        self.assertEqual(span_name, "chat test-model")

    def test_get_llm_response_attributes(self) -> None:
        """Test _get_llm_response_attributes and _get_llm_output_messages."""
        # Create a mock usage object
        usage = Mock()
        usage.input_tokens = 10
        usage.output_tokens = 20

        response = ChatResponse(
            id="test-id",
            content=[TextBlock(type="text", text="Hello")],
        )
        response.usage = usage

        # Test output messages extraction
        messages = _get_llm_output_messages(response)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertIn("parts", messages[0])

        # Test with non-ChatResponse
        result = _get_llm_output_messages("not a response")
        self.assertEqual(result, "not a response")

        # Test response attributes
        attributes = _get_llm_response_attributes(response)

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_RESPONSE_ID],
            "test-id",
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_USAGE_INPUT_TOKENS],
            10,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_USAGE_OUTPUT_TOKENS],
            20,
        )
        self.assertIn(SpanAttributes.GEN_AI_OUTPUT_MESSAGES, attributes)

    def test_get_agent_request_attributes(self) -> None:
        """Test _get_agent_messages, request_attributes and span_name."""
        # Test agent messages conversion
        msg = Msg(
            "test_user",
            [TextBlock(type="text", text="Hello")],
            "user",
        )
        result = _get_agent_messages(msg)
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["name"], "test_user")
        self.assertIn("parts", result)

        # Test request attributes
        args = (msg,)
        kwargs = {}
        attributes = _get_agent_request_attributes(
            self.mock_agent,
            args,
            kwargs,
        )

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.INVOKE_AGENT,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_AGENT_ID],
            "agent-1",
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_AGENT_NAME],
            "TestAgent",
        )
        self.assertIn(SpanAttributes.GEN_AI_INPUT_MESSAGES, attributes)
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_INPUT, attributes)

        # Test span name generation
        span_name = _get_agent_span_name(attributes)
        self.assertEqual(span_name, "invoke_agent TestAgent")

    def test_get_agent_response_attributes(self) -> None:
        """Test _get_agent_response_attributes."""
        response = Msg(
            "assistant",
            [TextBlock(type="text", text="Hi")],
            "assistant",
        )
        attributes = _get_agent_response_attributes(response)

        self.assertIn(SpanAttributes.GEN_AI_OUTPUT_MESSAGES, attributes)
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT, attributes)

    def test_get_tool_request_attributes(self) -> None:
        """Test _get_tool_request_attributes and _get_tool_span_name."""
        # Create a mock toolkit with tool definition
        toolkit = Mock(spec=Toolkit)
        tool_func = Mock()
        tool_func.json_schema = {
            "function": {
                "description": "Test tool description",
            },
        }
        toolkit.tools = {"test_tool": tool_func}

        tool_call = ToolUseBlock(
            type="tool_use",
            id="call-1",
            name="test_tool",
            input={"arg1": "value1"},
        )

        attributes = _get_tool_request_attributes(toolkit, tool_call)

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.EXECUTE_TOOL,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_TOOL_CALL_ID],
            "call-1",
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_TOOL_NAME],
            "test_tool",
        )
        self.assertIn(SpanAttributes.GEN_AI_TOOL_CALL_ARGUMENTS, attributes)
        self.assertIn(SpanAttributes.GEN_AI_TOOL_DESCRIPTION, attributes)

        # Test span name generation
        span_name = _get_tool_span_name(attributes)
        self.assertEqual(span_name, "execute_tool test_tool")

    def test_get_tool_response_attributes(self) -> None:
        """Test _get_tool_response_attributes."""
        response = {"result": "success"}
        attributes = _get_tool_response_attributes(response)

        self.assertIn(SpanAttributes.GEN_AI_TOOL_CALL_RESULT, attributes)
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT, attributes)

    def test_get_formatter_request_attributes(self) -> None:
        """Test formatter request_attributes and span_name."""
        args = ()
        kwargs = {}

        attributes = _get_formatter_request_attributes(
            self.mock_formatter,
            args,
            kwargs,
        )

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.FORMATTER,
        )
        self.assertIn(SpanAttributes.AGENTSCOPE_FORMAT_TARGET, attributes)
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_INPUT, attributes)

        # Test span name generation
        span_name = _get_formatter_span_name(attributes)
        self.assertEqual(span_name, "format openai")

    def test_get_formatter_response_attributes(self) -> None:
        """Test _get_formatter_response_attributes."""
        response = [{"role": "user", "content": "Hello"}]
        attributes = _get_formatter_response_attributes(response)

        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT, attributes)
        self.assertEqual(
            attributes[SpanAttributes.AGENTSCOPE_FORMAT_COUNT],
            1,
        )

    def test_get_generic_function_request_attributes(self) -> None:
        """Test generic function request_attributes, span_name and response."""
        args = (1, 2, 3)
        kwargs = {"key": "value"}

        attributes = _get_generic_function_request_attributes(
            "test_function",
            args,
            kwargs,
        )

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.INVOKE_GENERIC_FUNCTION,
        )
        self.assertEqual(
            attributes[SpanAttributes.AGENTSCOPE_FUNCTION_NAME],
            "test_function",
        )
        self.assertIn(SpanAttributes.AGENTSCOPE_FUNCTION_INPUT, attributes)

        # Test span name generation
        span_name = _get_generic_function_span_name(attributes)
        self.assertEqual(span_name, "invoke_generic_function test_function")

        # Test response attributes
        response = {"result": "success"}
        response_attributes = _get_generic_function_response_attributes(
            response,
        )
        self.assertIn(
            SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT,
            response_attributes,
        )

    def test_get_embedding_request_attributes(self) -> None:
        """Test _get_embedding_request_attributes, span_name and response."""
        args = ()
        kwargs = {"dimensions": 768}

        attributes = _get_embedding_request_attributes(
            self.mock_embedding,
            args,
            kwargs,
        )

        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_OPERATION_NAME],
            OperationNameValues.EMBEDDINGS,
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_REQUEST_MODEL],
            "embedding-model",
        )
        self.assertEqual(
            attributes[SpanAttributes.GEN_AI_EMBEDDINGS_DIMENSION_COUNT],
            768,
        )

        # Test span name generation
        span_name = _get_embedding_span_name(attributes)
        self.assertEqual(span_name, "embeddings embedding-model")

        # Test response attributes
        response = [[0.1, 0.2, 0.3]]
        response_attributes = _get_embedding_response_attributes(response)
        self.assertIn(
            SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT,
            response_attributes,
        )
