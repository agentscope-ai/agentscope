# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The A2A agent unittests."""
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    Message as A2AMessage,
    Part,
    Role as A2ARole,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    Artifact,
)

from agentscope.agent import A2AAgent
from agentscope.message import Msg


class MockA2AClient:
    """Mock A2A client for testing."""

    def __init__(self, response_type: str = "message") -> None:
        """Initialize mock client.

        Args:
            response_type (`str`):
                Type of response to simulate: "message", "task", or "error".
        """
        self.response_type = response_type
        self.sent_messages = []

    async def send_message(
        self,
        message: A2AMessage,
    ) -> AsyncIterator[A2AMessage | tuple[Task, Any]]:
        """Mock send_message method."""
        self.sent_messages.append(message)

        if self.response_type == "message":
            # Return a simple A2A message
            response = A2AMessage(
                message_id="test-msg-id",
                role=A2ARole.agent,
                parts=[
                    Part(root=TextPart(text="Hello from remote agent")),
                ],
            )
            yield response

        elif self.response_type == "task":
            # Return a task with completed state
            task = Task(
                id="test-task-id",
                context_id="test-context-id",
                status=TaskStatus(
                    state=TaskState.completed,
                    message=A2AMessage(
                        message_id="status-msg-id",
                        role=A2ARole.agent,
                        parts=[
                            Part(root=TextPart(text="Task completed")),
                        ],
                    ),
                ),
                artifacts=[
                    Artifact(
                        artifact_id="artifact-1",
                        name="test_artifact",
                        description="Test artifact",
                        parts=[
                            Part(root=TextPart(text="Artifact content")),
                        ],
                    ),
                ],
            )
            yield (task, None)

        elif self.response_type == "error":
            raise RuntimeError("Simulated communication error")


class MockClientFactory:
    """Mock ClientFactory for testing."""

    def __init__(self, response_type: str = "message") -> None:
        """Initialize mock factory."""
        self.response_type = response_type
        self.created_clients = []

    def create(self, card: AgentCard) -> MockA2AClient:
        """Create a mock client."""
        _ = card  # Used by real ClientFactory, not needed in mock
        client = MockA2AClient(self.response_type)
        self.created_clients.append(client)
        return client


class A2AAgentTest(IsolatedAsyncioTestCase):
    """Test class for A2AAgent."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.test_agent_card = AgentCard(
            name="TestAgent",
            url="http://localhost:8000",
            description="Test A2A agent",
            version="1.0.0",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[],
        )
        self.agent = A2AAgent(self.test_agent_card)

    async def test_reply_with_task(self) -> None:
        """Test reply method with task response."""
        # Mock the client factory
        self.agent._a2a_client_factory = MockClientFactory(
            response_type="task",
        )

        response = await self.agent(
            Msg(name="user", content="Process this", role="user"),
        )

        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")

        # Should contain artifact content
        self.assertEqual(
            response.content,
            [
                {
                    "type": "text",
                    "text": "Task completed",
                },
                {
                    "type": "text",
                    "text": "Artifact content",
                },
            ],
        )

    async def test_reply_with_no_messages(self) -> None:
        """Test reply method with no messages returns prompt message."""
        self.agent._a2a_client_factory = MockClientFactory()

        # Test with None - should return prompt message
        response = await self.agent(None)
        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        self.assertListEqual(
            response.get_content_blocks(),
            [
                {
                    "type": "text",
                    "text": "Hello from remote agent",
                },
            ],
        )

        # Test with empty list - should return prompt message
        response = await self.agent([])
        self.assertListEqual(
            response.get_content_blocks(),
            [
                {
                    "type": "text",
                    "text": "Hello from remote agent",
                },
            ],
        )

        # Test with list of None - should return prompt message
        response = await self.agent([None, None])
        self.assertListEqual(
            response.get_content_blocks(),
            [
                {
                    "type": "text",
                    "text": "Hello from remote agent",
                },
            ],
        )

    async def test_observe_method(self) -> None:
        """Test observe method stores messages for next reply."""
        # Initially no observed messages
        self.assertEqual(len(self.agent._observed_msgs), 0)

        # Observe single message
        await self.agent.observe(
            Msg(name="user", content="First observed", role="user"),
        )
        self.assertEqual(len(self.agent._observed_msgs), 1)

        # Observe multiple messages
        msg2 = Msg(name="user", content="Second observed", role="user")
        msg3 = Msg(name="user", content="Third observed", role="user")
        await self.agent.observe([msg2, msg3])
        self.assertEqual(len(self.agent._observed_msgs), 3)

        # Observe None should not change anything
        await self.agent.observe(None)
        self.assertEqual(len(self.agent._observed_msgs), 3)

    async def test_observe_and_reply_merge(self) -> None:
        """Test that observed messages are merged with reply input."""
        mock_factory = MockClientFactory()
        self.agent._a2a_client_factory = mock_factory

        # Observe some messages
        await self.agent.observe(
            Msg(name="user", content="Observed message", role="user"),
        )

        # Reply with another message
        await self.agent.reply(
            Msg(name="user", content="Reply message", role="user"),
        )

        # Check that the send A2A message contains both observed and input
        sent_msg = mock_factory.created_clients[0].sent_messages[0]
        self.assertEqual(len(sent_msg.parts), 2)

        # Check observed messages were cleared after reply
        self.assertEqual(len(self.agent._observed_msgs), 0)

    async def test_reply_with_only_observed_messages(self) -> None:
        """Test reply with None input uses only observed messages."""
        mock_factory = MockClientFactory()
        self.agent._a2a_client_factory = mock_factory

        # Observe a message
        await self.agent.observe(
            Msg(name="user", content="Only observed", role="user"),
        )

        # Reply with None
        await self.agent(None)

        # Should have sent the observed message
        sent_msg = mock_factory.created_clients[0].sent_messages[0]
        self.assertEqual(len(sent_msg.parts), 1)
        self.assertEqual(sent_msg.parts[0].root.text, "Only observed")

        # Observed messages should be cleared
        self.assertEqual(len(self.agent._observed_msgs), 0)
