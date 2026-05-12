# -*- coding: utf-8 -*-
"""The tracing types class in agentscope."""
from opentelemetry.semconv._incubating.attributes import (
    gen_ai_attributes as GenAIAttributes,
)


class SpanAttributes:
    """The span attributes."""

    # GenAI Common Attributes
    GEN_AI_CONVERSATION_ID = GenAIAttributes.GEN_AI_CONVERSATION_ID
    """The gen ai conversation ID."""

    GEN_AI_OPERATION_NAME = GenAIAttributes.GEN_AI_OPERATION_NAME
    """The gen ai operation name."""

    GEN_AI_PROVIDER_NAME = GenAIAttributes.GEN_AI_PROVIDER_NAME
    """The gen ai provider name."""

    # GenAI Request Attributes
    GEN_AI_REQUEST_MODEL = GenAIAttributes.GEN_AI_REQUEST_MODEL
    """The gen ai request model."""

    GEN_AI_REQUEST_TEMPERATURE = GenAIAttributes.GEN_AI_REQUEST_TEMPERATURE
    """The gen ai request temperature."""

    GEN_AI_REQUEST_TOP_P = GenAIAttributes.GEN_AI_REQUEST_TOP_P
    """The gen ai request top_p."""

    GEN_AI_REQUEST_TOP_K = GenAIAttributes.GEN_AI_REQUEST_TOP_K
    """The gen ai request top_k."""

    GEN_AI_REQUEST_MAX_TOKENS = GenAIAttributes.GEN_AI_REQUEST_MAX_TOKENS
    """The gen ai request max_tokens."""

    GEN_AI_REQUEST_PRESENCE_PENALTY = (
        GenAIAttributes.GEN_AI_REQUEST_PRESENCE_PENALTY
    )
    """The gen ai request presence_penalty."""

    GEN_AI_REQUEST_FREQUENCY_PENALTY = (
        GenAIAttributes.GEN_AI_REQUEST_FREQUENCY_PENALTY
    )
    """The gen ai request frequency_penalty."""

    GEN_AI_REQUEST_STOP_SEQUENCES = (
        GenAIAttributes.GEN_AI_REQUEST_STOP_SEQUENCES
    )
    """The gen ai request stop_sequences."""

    GEN_AI_REQUEST_SEED = GenAIAttributes.GEN_AI_REQUEST_SEED
    """The gen ai request seed."""

    # GenAI Response Attributes
    GEN_AI_RESPONSE_ID = GenAIAttributes.GEN_AI_RESPONSE_ID
    """The gen ai response ID."""

    GEN_AI_RESPONSE_FINISH_REASONS = (
        GenAIAttributes.GEN_AI_RESPONSE_FINISH_REASONS
    )
    """The gen ai response finish reasons."""

    # GenAI Usage Attributes
    GEN_AI_USAGE_INPUT_TOKENS = GenAIAttributes.GEN_AI_USAGE_INPUT_TOKENS
    """The gen ai usage input tokens."""

    GEN_AI_USAGE_OUTPUT_TOKENS = GenAIAttributes.GEN_AI_USAGE_OUTPUT_TOKENS
    """The gen ai usage output tokens."""

    # GenAI Message Attributes
    GEN_AI_INPUT_MESSAGES = GenAIAttributes.GEN_AI_INPUT_MESSAGES
    """The gen ai input messages."""

    GEN_AI_OUTPUT_MESSAGES = GenAIAttributes.GEN_AI_OUTPUT_MESSAGES
    """The gen ai output messages."""

    # GenAI Agent Attributes
    GEN_AI_AGENT_ID = GenAIAttributes.GEN_AI_AGENT_ID
    """The gen ai agent ID."""

    GEN_AI_AGENT_NAME = GenAIAttributes.GEN_AI_AGENT_NAME
    """The gen ai agent name."""

    GEN_AI_AGENT_DESCRIPTION = GenAIAttributes.GEN_AI_AGENT_DESCRIPTION
    """The gen ai agent description."""

    # GenAI Tool Attributes
    GEN_AI_TOOL_CALL_ID = GenAIAttributes.GEN_AI_TOOL_CALL_ID
    """The gen ai tool call ID."""

    GEN_AI_TOOL_NAME = GenAIAttributes.GEN_AI_TOOL_NAME
    """The gen ai tool name."""

    GEN_AI_TOOL_DESCRIPTION = GenAIAttributes.GEN_AI_TOOL_DESCRIPTION
    """The gen ai tool description."""

    GEN_AI_TOOL_CALL_ARGUMENTS = GenAIAttributes.GEN_AI_TOOL_CALL_ARGUMENTS
    """The gen ai tool call arguments."""

    GEN_AI_TOOL_CALL_RESULT = GenAIAttributes.GEN_AI_TOOL_CALL_RESULT
    """The gen ai tool call result."""

    GEN_AI_TOOL_DEFINITIONS = GenAIAttributes.GEN_AI_TOOL_DEFINITIONS
    """The gen ai tool definitions."""

    AGENTSCOPE_REPLY_ID = "agentscope.agent.reply_id"
    """The reply ID of the current agent reply.

    Shared by both calls in a HITL or external-execution chain, allowing
    observers to group the two ``invoke_agent`` spans that belong to the
    same logical reply.
    """

    AGENTSCOPE_HITL_PENDING_TOOLS = "agentscope.agent.hitl_pending_tools"
    """JSON list of tool names that are waiting for human confirmation.

    Set on the first ``invoke_agent`` span when the agent pauses due to a
    ``RequireUserConfirmEvent``.
    """

    AGENTSCOPE_EXTERNAL_EXECUTION_PENDING_TOOLS = (
        "agentscope.agent.external_execution_pending_tools"
    )
    """JSON list of tool names submitted for external execution.

    Set on the first ``invoke_agent`` span when the agent pauses due to a
    ``RequireExternalExecutionEvent``.
    """

    AGENTSCOPE_INCOMING_EVENT_TYPE = "agentscope.agent.incoming_event_type"
    """Type of the continuation event passed to the second reply call.

    Possible values: ``"user_confirm_result"``,
    ``"external_execution_result"``.
    """

    AGENTSCOPE_IS_EXTERNAL_EXECUTION = "agentscope.agent.is_external_execution"
    """Marks a synthetic ``execute_tool`` span that represents a tool executed
    externally (i.e. via ``ExternalExecutionResultEvent``).

    The timestamp reflects when the result was received, not when the tool
    actually ran on the external system.
    """


class OperationNameValues:
    """The operation name values."""

    CHAT = GenAIAttributes.GenAiOperationNameValues.CHAT.value
    """The chat operation name."""

    INVOKE_AGENT = GenAIAttributes.GenAiOperationNameValues.INVOKE_AGENT.value
    """The invoke agent operation name."""

    EXECUTE_TOOL = GenAIAttributes.GenAiOperationNameValues.EXECUTE_TOOL.value
    """The execute tool operation name."""


class ProviderNameValues:
    """The provider name values."""

    DASHSCOPE = "dashscope"
    """The dashscope provider name."""

    OLLAMA = "ollama"
    """The ollama provider name."""

    DEEPSEEK = GenAIAttributes.GenAiProviderNameValues.DEEPSEEK.value
    """The deepseek provider name."""

    OPENAI = GenAIAttributes.GenAiProviderNameValues.OPENAI.value
    """The openai provider name."""

    ANTHROPIC = GenAIAttributes.GenAiProviderNameValues.ANTHROPIC.value
    """The anthropic provider name."""

    GCP_GEMINI = GenAIAttributes.GenAiProviderNameValues.GCP_GEMINI.value
    """The gcp gemini provider name."""

    MOONSHOT = "moonshot"
    """The moonshot provider name."""

    AZURE_AI_OPENAI = (
        GenAIAttributes.GenAiProviderNameValues.AZURE_AI_OPENAI.value
    )
    """The azure openai provider name."""

    AWS_BEDROCK = GenAIAttributes.GenAiProviderNameValues.AWS_BEDROCK.value
    """The aws bedrock provider name."""
