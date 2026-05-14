# -*- coding: utf-8 -*-
"""Extract attributes from AgentScope components for OpenTelemetry tracing."""
import inspect
from typing import Any, Dict, TYPE_CHECKING

from ..message import Msg, ToolCallBlock

from ._attributes import (
    SpanAttributes,
    OperationNameValues,
    ProviderNameValues,
)
from ._converter import _convert_block_to_part
from ._utils import _serialize_to_str

if TYPE_CHECKING:
    from ..agent import Agent
    from ..model import ChatModelBase
    from ..tool import Toolkit, ToolChoice

_CLASS_NAME_MAP = {
    "dashscope": ProviderNameValues.DASHSCOPE,
    "openai": ProviderNameValues.OPENAI,
    "anthropic": ProviderNameValues.ANTHROPIC,
    "gemini": ProviderNameValues.GCP_GEMINI,
    "ollama": ProviderNameValues.OLLAMA,
    "deepseek": ProviderNameValues.DEEPSEEK,
    "trinity": ProviderNameValues.OPENAI,
}

# Map base URL fragments to provider names for OpenAI-compatible APIs
_BASE_URL_PROVIDER_MAP = [
    ("api.openai.com", ProviderNameValues.OPENAI),
    ("dashscope", ProviderNameValues.DASHSCOPE),
    ("deepseek", ProviderNameValues.DEEPSEEK),
    ("moonshot", ProviderNameValues.MOONSHOT),
    ("generativelanguage.googleapis.com", ProviderNameValues.GCP_GEMINI),
    ("openai.azure.com", ProviderNameValues.AZURE_AI_OPENAI),
    ("amazonaws.com", ProviderNameValues.AWS_BEDROCK),
]


def _get_common_attributes() -> Dict[str, str]:
    """Get common attributes for all spans.

    Returns:
        `Dict[str, str]`:
        Common span attributes including conversation ID
    """
    from ._trace import _current_session_id

    session_id = _current_session_id.get()
    return {
        SpanAttributes.GEN_AI_CONVERSATION_ID: (
            session_id if session_id else "[no_session_id]"
        ),
    }


def _get_provider_name(instance: "ChatModelBase") -> str:
    """Get provider name from ChatModelBase instance.

    Maps ChatModelBase class names to provider names, with special handling
    for OpenAI-compatible APIs that may use different base URLs.
    This follows the implementation pattern from agentscope-java PR #73.

    Args:
        instance (`ChatModelBase`):
            The chat model instance to get the provider name for.

    Returns:
        `str`:
            Provider name (e.g., "openai", "dashscope", "anthropic")
    """
    classname = instance.__class__.__name__

    # Special handling for OpenAIChatModel - check base_url
    if classname == "OpenAIChatModel":
        # Try to get base_url from the client
        base_url = None
        if hasattr(instance, "client") and hasattr(
            instance.client,
            "base_url",
        ):
            base_url = str(instance.client.base_url)

        # If base_url is None or empty, return default OpenAI
        if not base_url:
            return ProviderNameValues.OPENAI

        # Check base_url fragments to identify provider
        for url_fragment, provider_name in _BASE_URL_PROVIDER_MAP:
            if url_fragment in base_url:
                return provider_name

        # If no match found, return openai as default
        return ProviderNameValues.OPENAI

    # For other model types, use direct mapping
    prefix_key = (
        classname.removesuffix("ChatModel")
        .removesuffix("MultiAgentModel")
        .lower()
    )
    return _CLASS_NAME_MAP.get(prefix_key, "unknown")


def _get_tool_definitions(
    tools: list[dict[str, Any]] | None,
    tool_choice: "ToolChoice | None",
) -> str | None:
    """Extract and serialize tool definitions for tracing.

    Converts AgentScope/OpenAI nested tool format to OpenTelemetry GenAI
    flat format for tracing.

    Args:
        tools (`list[dict[str, Any]] | None`, optional):
            List of tool definitions in OpenAI format with nested
            structure: ``[{"type": "function", "function": {...}}]``
        tool_choice (`ToolChoice | None`, optional):
            Tool choice configuration with ``mode`` and optional ``tools``
            fields. If mode is ``"none"``, returns None to indicate tools
            should not be traced.

    Returns:
        `str | None`:
            Serialized tool definitions in flat format:
            ``[{"type": "function", "name": ..., "parameters": ...}]``
            or None if tools should not be traced (e.g., tools is None/empty
            or tool_choice is "none").
    """
    # No tools provided
    if tools is None or not isinstance(tools, list) or len(tools) == 0:
        return None

    # Tool choice is explicitly "none" (model should not use tools)
    if tool_choice is not None and tool_choice.mode == "none":
        return None

    try:
        # Convert nested format to flat format for OpenTelemetry GenAI
        # TODO: Currently only supports "function" type tools. If other tool
        # types are added in the future (e.g., "retrieval", "code_interpreter",
        # "browser"), this conversion logic needs to be updated to handle them.
        flat_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or "function" not in tool:
                continue

            func_def = tool["function"]
            flat_tool = {
                "type": tool.get("type", "function"),
                "name": func_def.get("name"),
                "description": func_def.get("description"),
                "parameters": func_def.get("parameters"),
            }
            # Remove None values
            flat_tool = {k: v for k, v in flat_tool.items() if v is not None}
            flat_tools.append(flat_tool)

        if flat_tools:
            return _serialize_to_str(flat_tools)
        return None

    except Exception:
        return None


def _get_llm_request_attributes(
    instance: "ChatModelBase",
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Get LLM request attributes for OpenTelemetry tracing.

    Extracts request parameters from LLM model calls into GenAI attributes.

    Args:
        instance (`ChatModelBase`):
            The chat model instance making the request.
        kwargs (`Dict[str, Any]`):
            Keyword arguments including generation parameters such as
            temperature, top_p, top_k, max_tokens, presence_penalty,
            frequency_penalty, stop_sequences, seed, tools, and tool_choice.

    Returns:
        `Dict[str, Any]`:
            OpenTelemetry GenAI attributes with string values, including
            operation name, provider name, model name, generation parameters,
            and tool definitions.
    """

    attributes = {
        # required attributes
        SpanAttributes.GEN_AI_OPERATION_NAME: OperationNameValues.CHAT,
        SpanAttributes.GEN_AI_PROVIDER_NAME: _get_provider_name(instance),
        # conditionally required attributes
        SpanAttributes.GEN_AI_REQUEST_MODEL: getattr(
            instance,
            "model",
            "unknown_model",
        ),
        # recommended attributes
        SpanAttributes.GEN_AI_REQUEST_TEMPERATURE: kwargs.get("temperature"),
        SpanAttributes.GEN_AI_REQUEST_TOP_P: kwargs.get("p")
        or kwargs.get("top_p"),
        SpanAttributes.GEN_AI_REQUEST_TOP_K: kwargs.get("top_k"),
        SpanAttributes.GEN_AI_REQUEST_MAX_TOKENS: kwargs.get("max_tokens"),
        SpanAttributes.GEN_AI_REQUEST_PRESENCE_PENALTY: kwargs.get(
            "presence_penalty",
        ),
        SpanAttributes.GEN_AI_REQUEST_FREQUENCY_PENALTY: kwargs.get(
            "frequency_penalty",
        ),
        SpanAttributes.GEN_AI_REQUEST_STOP_SEQUENCES: kwargs.get(
            "stop_sequences",
        ),
        SpanAttributes.GEN_AI_REQUEST_SEED: kwargs.get("seed"),
    }

    # Extract tool definitions if provided
    tool_definitions = _get_tool_definitions(
        tools=kwargs.get("tools"),
        tool_choice=kwargs.get("tool_choice"),
    )
    if tool_definitions:
        attributes[SpanAttributes.GEN_AI_TOOL_DEFINITIONS] = tool_definitions

    return {k: v for k, v in attributes.items() if v is not None}


def _get_llm_span_name(attributes: Dict[str, str]) -> str:
    """Generate span name for LLM operations.

    Args:
        attributes (`Dict[str, str]`):
            LLM request attributes dictionary containing operation name and
            model name.

    Returns:
        `str`:
            Formatted span name in the format "{operation} {model}",
            e.g., "chat gpt-4" or "chat qwen-plus".
    """
    return (
        f"{attributes[SpanAttributes.GEN_AI_OPERATION_NAME]} "
        f"{attributes[SpanAttributes.GEN_AI_REQUEST_MODEL]}"
    )


def _get_llm_output_messages(
    chat_response: Any,
) -> list[dict[str, Any]]:
    """Extract and format LLM output messages for tracing.

    Converts ChatResponse objects to standardized message format compatible
    with OpenTelemetry GenAI specification.

    Args:
        chat_response (`Any`):
            Chat response object with content blocks. Should be a ChatResponse
            instance containing content blocks (text, tool_use, etc.).

    Returns:
        `list[dict[str, Any]]`:
            List containing a single formatted message dictionary with role,
            parts, and finish_reason. Returns the original response if it's
            not a ChatResponse instance, or an error message format if
            conversion fails.
    """
    try:
        from agentscope.model import ChatResponse

        if not isinstance(chat_response, ChatResponse):
            return chat_response

        parts = []
        finish_reason = "stop"  # Default finish reason

        for block in chat_response.content:
            part = _convert_block_to_part(block)
            if part:
                parts.append(part)

        output_message = {
            "role": "assistant",
            "parts": parts,
            "finish_reason": finish_reason,
        }

        return [output_message]

    except Exception:
        return [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "text",
                        "content": "<error processing response>",
                    },
                ],
                "finish_reason": "error",
            },
        ]


def _get_llm_response_attributes(
    chat_response: Any,
) -> Dict[str, Any]:
    """Get LLM response attributes for OpenTelemetry tracing.

    Extracts response metadata and formats into GenAI attributes.

    Args:
        chat_response (`Any`):
            Chat response object with data and usage info. Should have
            attributes like id, usage (with input_tokens and output_tokens),
            and content blocks.

    Returns:
        `Dict[str, Any]`:
            OpenTelemetry GenAI response attributes including response ID,
            finish reasons, token usage (input/output tokens), and output
            messages.
    """
    attributes = {
        SpanAttributes.GEN_AI_RESPONSE_ID: getattr(
            chat_response,
            "id",
            "unknown_id",
        ),
        # FIXME: finish reason should be capture in chat response
        SpanAttributes.GEN_AI_RESPONSE_FINISH_REASONS: '["stop"]',
    }
    if hasattr(chat_response, "usage") and chat_response.usage:
        attributes[
            SpanAttributes.GEN_AI_USAGE_INPUT_TOKENS
        ] = chat_response.usage.input_tokens
        attributes[
            SpanAttributes.GEN_AI_USAGE_OUTPUT_TOKENS
        ] = chat_response.usage.output_tokens

    output_messages = _get_llm_output_messages(chat_response)
    if output_messages:
        attributes[SpanAttributes.GEN_AI_OUTPUT_MESSAGES] = _serialize_to_str(
            output_messages,
        )

    return attributes


def _get_agent_messages(
    msg: Msg | list[Msg],
) -> list[dict[str, Any]]:
    """Convert AgentScope message(s) to standardized parts format.

    Transforms Msg objects into OpenTelemetry GenAI format.

    Args:
        msg (`Msg | list[Msg]`):
            AgentScope message object or list of message objects with
            content blocks.

    Returns:
        `list[dict[str, Any]]`:
            List of formatted message dictionaries with role, parts, name,
            and finish_reason.
    """
    try:
        if isinstance(msg, Msg):
            msg = [msg]

        formatted_msgs = []
        for m in msg:
            parts = []
            for block in m.get_content_blocks():
                part = _convert_block_to_part(block)
                if part:
                    parts.append(part)
            formatted_msg = {
                "role": m.role,
                "parts": parts,
                "name": m.name,
                "finish_reason": "stop",
            }
            formatted_msgs.append(formatted_msg)

        return formatted_msgs
    except Exception:
        # Fallback: try simple attribute access on the original object.
        # If msg was already converted to a list or lacks role/name, return
        # an empty list rather than raising a secondary exception.
        try:
            single = msg[0] if isinstance(msg, list) else msg
            return [
                {
                    "role": single.role,
                    "parts": [
                        {
                            "type": "text",
                            "content": (
                                str(single.content) if single.content else ""
                            ),
                        },
                    ],
                    "name": single.name,
                    "finish_reason": "stop",
                },
            ]
        except Exception:
            return []


def _get_agent_request_attributes(
    instance: "Agent",
    kwargs: Dict[str, Any],
) -> Dict[str, str]:
    """Get agent request attributes for OpenTelemetry tracing.

    Extracts agent metadata and input data into GenAI attributes.

    Args:
        instance (`Agent`):
            The agent instance making the request.
        kwargs (`Dict[str, Any]`):
            Keyword arguments passed to the agent's reply method.

    Returns:
        `Dict[str, str]`:
            OpenTelemetry GenAI attributes including operation name, agent ID,
            agent name, agent description, and input messages (if provided).
    """
    attributes = {
        SpanAttributes.GEN_AI_OPERATION_NAME: (
            OperationNameValues.INVOKE_AGENT
        ),
        SpanAttributes.GEN_AI_AGENT_ID: getattr(instance, "id", "unknown"),
        SpanAttributes.GEN_AI_AGENT_NAME: getattr(
            instance,
            "name",
            "unknown_agent",
        ),
        SpanAttributes.GEN_AI_AGENT_DESCRIPTION: inspect.getdoc(
            instance.__class__,
        )
        or "No description available",
    }

    msg = None
    if "msgs" in kwargs:
        msg = kwargs["msgs"]
    elif "msg" in kwargs:
        msg = kwargs["msg"]
    if msg:
        input_messages = _get_agent_messages(msg)
        attributes[SpanAttributes.GEN_AI_INPUT_MESSAGES] = _serialize_to_str(
            input_messages,
        )

    # Record the incoming continuation event type for HITL/external execution
    event = kwargs.get("event")
    if event is not None:
        from ..event import (
            ExternalExecutionResultEvent,
            UserConfirmResultEvent,
        )

        if isinstance(event, UserConfirmResultEvent):
            attributes[
                SpanAttributes.AGENTSCOPE_INCOMING_EVENT_TYPE
            ] = "user_confirm_result"
        elif isinstance(event, ExternalExecutionResultEvent):
            attributes[
                SpanAttributes.AGENTSCOPE_INCOMING_EVENT_TYPE
            ] = "external_execution_result"

    return attributes


def _get_agent_span_name(attributes: Dict[str, str]) -> str:
    """Generate span name for agent operations.

    Args:
        attributes (`Dict[str, str]`):
            Agent request attributes dictionary containing operation name and
            agent name.

    Returns:
        `str`:
            Formatted span name in the format "{operation} {agent_name}",
            e.g., "invoke_agent MyAgent".
    """
    return (
        f"{attributes[SpanAttributes.GEN_AI_OPERATION_NAME]} "
        f"{attributes[SpanAttributes.GEN_AI_AGENT_NAME]}"
    )


def _get_agent_response_attributes(
    agent_response: Any,
) -> Dict[str, str]:
    """Get agent response attributes for OpenTelemetry tracing.

    Args:
        agent_response (`Any`):
            Response object returned by agent. Should be a Msg object with
            content blocks.

    Returns:
        `Dict[str, str]`:
            OpenTelemetry GenAI response attributes including output messages.
    """
    attributes = {
        SpanAttributes.GEN_AI_OUTPUT_MESSAGES: _serialize_to_str(
            _get_agent_messages(agent_response),
        ),
    }
    return attributes


def _get_tool_request_attributes(
    instance: "Toolkit",
    tool_call: ToolCallBlock,
) -> Dict[str, str]:
    """Get tool request attributes for OpenTelemetry tracing.

    Extracts tool execution metadata into GenAI attributes.

    Args:
        instance (`Toolkit`):
            Toolkit instance with tool definitions. Used to extract tool
            description from the tool's JSON schema.
        tool_call (`ToolCallBlock`):
            Tool use block with call information including id, name, and input
            arguments.

    Returns:
        `Dict[str, str]`:
            OpenTelemetry GenAI tool attributes including operation name, tool
            call ID, tool name, tool description (if available), and tool call
            arguments.
    """
    attributes = {
        SpanAttributes.GEN_AI_OPERATION_NAME: (
            OperationNameValues.EXECUTE_TOOL
        ),
    }

    if tool_call:
        tool_name = tool_call.name
        attributes[SpanAttributes.GEN_AI_TOOL_CALL_ID] = tool_call.id
        attributes[SpanAttributes.GEN_AI_TOOL_NAME] = tool_name
        # tool_call.input is already a JSON string; pass it directly to avoid
        # double-encoding (e.g. '{"city": "Beijing"}' → '"{\\"city\\"...}"')
        attributes[SpanAttributes.GEN_AI_TOOL_CALL_ARGUMENTS] = tool_call.input

        if tool_name:
            registered = getattr(instance, "tools", {}).get(tool_name)
            if registered is not None:
                tool_obj = getattr(registered, "tool", None)
                description = getattr(tool_obj, "description", None)
                if description:
                    attributes[
                        SpanAttributes.GEN_AI_TOOL_DESCRIPTION
                    ] = description

    return attributes


def _get_tool_span_name(attributes: Dict[str, str]) -> str:
    """Generate span name for tool operations.

    Args:
        attributes (`Dict[str, str]`):
            Tool request attributes dictionary containing operation name and
            tool name.

    Returns:
        `str`:
            Formatted span name in the format "{operation} {tool_name}",
            e.g., "execute_tool search".
    """
    return (
        f"{attributes[SpanAttributes.GEN_AI_OPERATION_NAME]} "
        f"{attributes[SpanAttributes.GEN_AI_TOOL_NAME]}"
    )


def _get_tool_response_attributes(
    tool_response: Any,
) -> Dict[str, str]:
    """Get tool response attributes for OpenTelemetry tracing.

    Args:
        tool_response (`Any`):
            Response object from tool execution. Can be any serializable object
            returned by the tool function.

    Returns:
        `Dict[str, str]`:
            OpenTelemetry GenAI response attributes including tool call result.
    """
    attributes = {
        SpanAttributes.GEN_AI_TOOL_CALL_RESULT: _serialize_to_str(
            tool_response,
        ),
    }
    return attributes
