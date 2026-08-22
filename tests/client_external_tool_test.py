# -*- coding: utf-8 -*-
"""Tests for per-run client external tool definitions."""
from unittest import IsolatedAsyncioTestCase, TestCase

from pydantic import ValidationError
from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.app._client_external_tool import (
    ClientExternalTool,
    ClientExternalToolDefinition,
)
from agentscope.app._router._schema import ChatRequest
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import Toolkit


def _definition(
    name: str = "client__request_user_input",
    read_only: bool = False,
) -> ClientExternalToolDefinition:
    """Build a minimal valid client external tool definition."""
    return ClientExternalToolDefinition(
        name=name,
        description="Ask the user to choose.",
        read_only=read_only,
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    )


class ClientExternalToolDefinitionTest(TestCase):
    """Validate the public request contract and its guardrails."""

    def test_chat_request_preserves_valid_definition(self) -> None:
        """A valid definition is available to the current chat run."""
        request = ChatRequest(
            agent_id="agent-1",
            session_id="session-1",
            input=None,
            client_external_tools=[_definition()],
        )

        self.assertEqual(
            request.model_dump(mode="json"),
            {
                "agent_id": "agent-1",
                "session_id": "session-1",
                "input": None,
                "client_external_tools": [
                    {
                        "name": "client__request_user_input",
                        "description": "Ask the user to choose.",
                        "read_only": False,
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                            },
                            "required": ["question"],
                        },
                    },
                ],
            },
        )

    def test_tool_name_rejects_unsupported_characters(self) -> None:
        """Client tool names only allow provider-safe characters."""
        with self.assertRaises(ValidationError):
            _definition("client__invalid tool name")

    def test_tool_name_requires_client_namespace(self) -> None:
        """Client tools cannot shadow server or toolkit built-ins."""
        with self.assertRaises(ValidationError):
            _definition("ResetTools")

    def test_invalid_json_schema_is_rejected(self) -> None:
        """Malformed JSON Schema cannot enter the runtime toolkit."""
        with self.assertRaises(ValidationError):
            ClientExternalToolDefinition(
                name="client__invalid_schema",
                description="Invalid schema.",
                input_schema={
                    "type": 42,
                    "properties": {},
                },
            )

    def test_non_object_input_schema_is_rejected(self) -> None:
        """Tool schemas must match the toolkit's object contract."""
        with self.assertRaises(ValidationError):
            ClientExternalToolDefinition(
                name="client__string_tool",
                description="Invalid root type.",
                input_schema={
                    "type": "string",
                },
            )

    def test_remote_schema_reference_is_rejected(self) -> None:
        """A client schema cannot trigger URI retrieval on the server."""
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValidationError):
                    ClientExternalToolDefinition(
                        name="client__remote_reference",
                        description="Invalid remote reference.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "value": {
                                    keyword: (
                                        "https://example.com/schema.json"
                                    ),
                                },
                            },
                        },
                    )

    def test_local_schema_reference_is_allowed(self) -> None:
        """Local definitions remain available for structured schemas."""
        definition = ClientExternalToolDefinition(
            name="client__local_reference",
            description="Valid local reference.",
            input_schema={
                "type": "object",
                "$defs": {
                    "value": {"type": "string"},
                },
                "properties": {
                    "value": {"$ref": "#/$defs/value"},
                },
            },
        )

        self.assertEqual(
            definition.input_schema["properties"],
            {"value": {"$ref": "#/$defs/value"}},
        )

    def test_duplicate_names_are_rejected(self) -> None:
        """One request cannot register ambiguous duplicate names."""
        with self.assertRaises(ValidationError):
            ChatRequest(
                agent_id="agent-1",
                session_id="session-1",
                input=None,
                client_external_tools=[
                    _definition(),
                    _definition(),
                ],
            )

    def test_chat_request_enforces_client_tool_limit(self) -> None:
        """A request accepts at most sixteen client tool definitions."""
        definitions = [
            _definition(f"client__tool_{index}") for index in range(17)
        ]

        request = ChatRequest(
            agent_id="agent-1",
            session_id="session-1",
            input=None,
            client_external_tools=definitions[:16],
        )
        self.assertEqual(request.client_external_tools, definitions[:16])

        with self.assertRaises(ValidationError):
            ChatRequest(
                agent_id="agent-1",
                session_id="session-1",
                input=None,
                client_external_tools=definitions,
            )


class ClientExternalToolRuntimeTest(IsolatedAsyncioTestCase):
    """Validate failures at the agent's schema evaluation boundary."""

    async def test_permissions_follow_explicit_read_only_contract(
        self,
    ) -> None:
        """Only explicitly read-only client tools bypass confirmation."""
        effectful_tool = ClientExternalTool(_definition())
        read_only_tool = ClientExternalTool(_definition(read_only=True))

        self.assertEqual(
            await effectful_tool.check_permissions(
                {},
                PermissionContext(),
            ),
            PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=(
                    "Client external tool 'client__request_user_input' "
                    "may change client-side state."
                ),
                decision_reason=(
                    "The active client did not declare "
                    "'client__request_user_input' read-only."
                ),
            ),
        )
        self.assertEqual(
            await read_only_tool.check_permissions(
                {},
                PermissionContext(),
            ),
            PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=(
                    "Client external tool 'client__request_user_input' "
                    "is read-only."
                ),
                decision_reason=(
                    "The active client declared "
                    "'client__request_user_input' read-only."
                ),
            ),
        )
        self.assertFalse(effectful_tool.is_read_only)
        self.assertTrue(read_only_tool.is_read_only)

    async def test_unresolvable_local_reference_becomes_tool_error(
        self,
    ) -> None:
        """A broken local reference must not terminate the reply run."""
        definition = ClientExternalToolDefinition(
            name="client__broken_reference",
            description="A client tool with a broken local reference.",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {"$ref": "#/$defs/missing"},
                },
            },
        )
        model = MockModel()
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="broken-reference-call",
                            name=definition.name,
                            input='{"value":"test"}',
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(
                    content=[TextBlock(text="Recovered from schema error.")],
                    is_last=True,
                ),
            ],
        )
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=model,
            toolkit=Toolkit(tools=[ClientExternalTool(definition)]),
            injection_config=InjectionConfig(
                inject_runtime_state=False,
            ),
        )

        response = await agent.reply(
            UserMsg(name="user", content="Call the client tool."),
        )

        self.assertEqual(
            response.get_content_blocks("text")[0].text,
            "Recovered from schema error.",
        )
        tool_results = agent.state.context[-1].get_content_blocks(
            "tool_result",
        )
        self.assertEqual(len(tool_results), 1)
        self.assertIn(
            "Input schema evaluation failed for tool "
            "'client__broken_reference'",
            str(tool_results[0].output),
        )
