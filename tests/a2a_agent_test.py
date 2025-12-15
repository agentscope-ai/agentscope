# -*- coding: utf-8 -*-
"""The A2A agent unittests."""
import json
from pathlib import Path
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
import tempfile

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message as A2AMessage,
    Part,
    Role as A2ARole,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    TextPart,
    Artifact,
)

from agentscope.agent import (
    A2aAgent,
    FixedAgentCardResolver,
    FileAgentCardResolver,
)
from agentscope.message import (
    AudioBlock,
    Base64Source,
    ImageBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    URLSource,
)


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
        client = MockA2AClient(self.response_type)
        self.created_clients.append(client)
        return client


class A2aAgentTest(IsolatedAsyncioTestCase):
    """Test class for A2aAgent."""

    def setUp(self) -> None:
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

    async def test_fixed_agent_card_resolver(self) -> None:
        """Test FixedAgentCardResolver."""
        resolver = FixedAgentCardResolver(self.test_agent_card)
        card = await resolver.get_agent_card()

        self.assertEqual(card.name, "TestAgent")
        self.assertEqual(str(card.url), "http://localhost:8000")

    async def test_file_agent_card_resolver(self) -> None:
        """Test FileAgentCardResolver."""
        # Create a temporary agent card file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            json.dump(
                {
                    "name": "FileAgent",
                    "url": "http://localhost:8001",
                    "description": "Agent from file",
                    "version": "1.0.0",
                    "capabilities": {},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                },
                f,
            )
            temp_file = f.name

        try:
            resolver = FileAgentCardResolver(temp_file)
            card = await resolver.get_agent_card()

            self.assertEqual(card.name, "FileAgent")
            self.assertEqual(str(card.url), "http://localhost:8001")
        finally:
            Path(temp_file).unlink()

    async def test_file_agent_card_resolver_invalid_file(self) -> None:
        """Test FileAgentCardResolver with invalid file."""
        resolver = FileAgentCardResolver("/nonexistent/path/card.json")

        with self.assertRaises(RuntimeError):
            await resolver.get_agent_card()

    async def test_convert_text_msg_to_a2a(self) -> None:
        """Test converting text Msg to A2A message."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Mock the client factory
        agent._a2a_client_factory = MockClientFactory()
        agent._is_ready = True

        msg = Msg(
            name="user",
            content="Hello",
            role="user",
        )

        a2a_msg = agent._convert_msgs_to_a2a_message([msg])

        self.assertEqual(len(a2a_msg.parts), 1)
        self.assertIsInstance(a2a_msg.parts[0].root, TextPart)
        self.assertEqual(a2a_msg.parts[0].root.text, "Hello")

    async def test_convert_content_blocks_to_a2a(self) -> None:
        """Test converting ContentBlocks to A2A message."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._a2a_client_factory = MockClientFactory()
        agent._is_ready = True

        msg = Msg(
            name="user",
            content=[
                TextBlock(type="text", text="Hello"),
                ThinkingBlock(type="thinking", thinking="Let me think..."),
                ImageBlock(
                    type="image",
                    source=URLSource(
                        type="url",
                        url="http://example.com/image.jpg",
                    ),
                ),
            ],
            role="user",
        )

        a2a_msg = agent._convert_msgs_to_a2a_message([msg])

        # Should have 3 parts: text, thinking, and image
        self.assertEqual(len(a2a_msg.parts), 3)

        # Verify text part
        self.assertIsInstance(a2a_msg.parts[0].root, TextPart)
        self.assertEqual(a2a_msg.parts[0].root.text, "Hello")
        self.assertEqual(
            a2a_msg.parts[0].root.metadata.get("_agentscope_block_type"),
            "text",
        )

        # Verify thinking part
        self.assertIsInstance(a2a_msg.parts[1].root, TextPart)
        self.assertEqual(a2a_msg.parts[1].root.text, "Let me think...")
        self.assertEqual(
            a2a_msg.parts[1].root.metadata.get("_agentscope_block_type"),
            "thinking",
        )

        # Verify image part
        self.assertIsInstance(a2a_msg.parts[2].root, FilePart)
        self.assertIsInstance(a2a_msg.parts[2].root.file, FileWithUri)
        self.assertEqual(
            a2a_msg.parts[2].root.file.uri,
            "http://example.com/image.jpg",
        )
        self.assertEqual(a2a_msg.parts[2].root.file.mime_type, "image/*")

    async def test_convert_tool_blocks_to_a2a(self) -> None:
        """Test converting ToolUse/ToolResult blocks to A2A."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._a2a_client_factory = MockClientFactory()
        agent._is_ready = True

        msg = Msg(
            name="assistant",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    name="get_weather",
                    id="call-1",
                    input={"city": "Beijing"},
                ),
                ToolResultBlock(
                    type="tool_result",
                    name="get_weather",
                    id="call-1",
                    output="Sunny, 25°C",
                ),
            ],
            role="assistant",
        )

        a2a_msg = agent._convert_msgs_to_a2a_message([msg])

        self.assertEqual(len(a2a_msg.parts), 2)
        # Check tool use
        self.assertIsInstance(a2a_msg.parts[0].root, DataPart)
        self.assertEqual(
            a2a_msg.parts[0].root.metadata.get("_agentscope_block_type"),
            "tool_use",
        )
        # Check tool result
        self.assertIsInstance(a2a_msg.parts[1].root, DataPart)
        self.assertEqual(
            a2a_msg.parts[1].root.metadata.get("_agentscope_block_type"),
            "tool_result",
        )

    async def test_convert_a2a_to_msg(self) -> None:
        """Test converting A2A message to Msg."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        a2a_msg = A2AMessage(
            message_id="test-id",
            role=A2ARole.agent,
            parts=[
                Part(root=TextPart(text="Response text")),
            ],
        )

        msg = agent._convert_a2a_message_to_msg(a2a_msg)

        self.assertEqual(msg.name, "TestAgent")
        self.assertEqual(msg.role, "assistant")
        self.assertEqual(len(msg.content), 1)
        # ContentBlock is a TypedDict, check structure instead of isinstance
        self.assertEqual(msg.content[0]["type"], "text")
        self.assertEqual(msg.content[0]["text"], "Response text")

    async def test_convert_a2a_file_part_to_msg(self) -> None:
        """Test converting A2A FilePart to media blocks."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Test image file
        a2a_msg = A2AMessage(
            message_id="test-id",
            role=A2ARole.agent,
            parts=[
                Part(
                    root=FilePart(
                        file=FileWithUri(
                            uri="http://example.com/image.png",
                            mime_type="image/png",
                        ),
                    ),
                ),
            ],
        )

        msg = agent._convert_a2a_message_to_msg(a2a_msg)

        self.assertEqual(len(msg.content), 1)
        # ContentBlock is a TypedDict, check structure
        self.assertEqual(msg.content[0]["type"], "image")
        self.assertEqual(
            msg.content[0]["source"]["url"],
            "http://example.com/image.png",
        )

    async def test_reply_with_direct_message(self) -> None:
        """Test reply method with direct message response."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Mock the client factory
        agent._a2a_client_factory = MockClientFactory(response_type="message")
        agent._is_ready = True

        input_msg = Msg(name="user", content="Hello", role="user")

        response = await agent.reply(input_msg)

        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        self.assertIn("Hello from remote agent", response.content[0]["text"])

    async def test_reply_with_task(self) -> None:
        """Test reply method with task response."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Mock the client factory
        agent._a2a_client_factory = MockClientFactory(response_type="task")
        agent._is_ready = True

        input_msg = Msg(name="user", content="Process this", role="user")

        response = await agent.reply(input_msg)

        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        # Should contain artifact content
        self.assertGreater(len(response.content), 0)

    async def test_reply_with_error(self) -> None:
        """Test reply method handles errors gracefully."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Mock the client factory to raise error
        agent._a2a_client_factory = MockClientFactory(response_type="error")
        agent._is_ready = True

        input_msg = Msg(name="user", content="Hello", role="user")

        # Should return error message, not raise exception
        response = await agent.reply(input_msg)

        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        self.assertTrue(response.metadata.get("error", False))
        self.assertIn("Error", response.content[0]["text"])

    async def test_reply_with_no_messages(self) -> None:
        """Test reply method with no messages returns prompt message."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._a2a_client_factory = MockClientFactory()
        agent._is_ready = True

        # Test with None - should return prompt message
        response = await agent.reply(None)
        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        self.assertIn("No input message", response.content)

        # Test with empty list - should return prompt message
        response = await agent.reply([])
        self.assertIn("No input message", response.content)

        # Test with list of None - should return prompt message
        response = await agent.reply([None, None])
        self.assertIn("No input message", response.content)

    async def test_construct_msg_from_task_status(self) -> None:
        """Test constructing Msg from task status."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Task with status message
        task = Task(
            id="task-1",
            context_id="context-1",
            status=TaskStatus(
                state=TaskState.working,
                message=A2AMessage(
                    message_id="status-1",
                    role=A2ARole.agent,
                    parts=[Part(root=TextPart(text="Processing..."))],
                ),
            ),
        )

        msg = agent._construct_msg_from_task_status(task)

        self.assertEqual(msg.name, "TestAgent")
        self.assertIn("Task ID: task-1", msg.content[0]["text"])

        # Task without status message
        task_no_msg = Task(
            id="task-2",
            context_id="context-2",
            status=TaskStatus(state=TaskState.submitted),
        )

        msg_no_status = agent._construct_msg_from_task_status(task_no_msg)

        self.assertEqual(msg_no_status.name, "TestAgent")
        self.assertIn("Task ID: task-2", msg_no_status.content[0]["text"])

    async def test_convert_task_artifacts_to_msg(self) -> None:
        """Test converting task artifacts to Msg."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        task = Task(
            id="task-1",
            context_id="context-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="art-1",
                    name="output",
                    description="Generated output",
                    parts=[
                        Part(root=TextPart(text="Result data")),
                    ],
                ),
            ],
        )

        msg = agent._convert_task_artifacts_to_msg(task)

        self.assertIsNotNone(msg)
        self.assertEqual(msg.name, "TestAgent")
        # Should have artifact content (without metadata TextBlock)
        self.assertEqual(len(msg.content), 1)
        self.assertEqual(msg.content[0]["text"], "Result data")

        # Test with no artifacts
        task_no_artifacts = Task(
            id="task-2",
            context_id="context-2",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[],
        )

        msg_none = agent._convert_task_artifacts_to_msg(task_no_artifacts)
        self.assertIsNone(msg_none)

    async def test_validate_agent_card(self) -> None:
        """Test agent card validation."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Valid card
        valid_card = AgentCard(
            name="Valid",
            url="http://localhost:8000",
            description="Valid agent",
            version="1.0.0",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[],
        )
        await agent._validate_agent_card(valid_card)

        # Invalid card - malformed URL
        invalid_card = AgentCard(
            name="Invalid",
            url="invalid-url-format",
            description="Invalid agent",
            version="1.0.0",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[],
        )
        with self.assertRaises(RuntimeError):
            await agent._validate_agent_card(invalid_card)

    async def test_get_agent_card_with_validation(self) -> None:
        """Test _get_agent_card with validation and fallback."""
        # Create a valid agent card
        valid_card = AgentCard(
            name="ValidAgent",
            url="http://localhost:8000",
            description="Valid agent",
            version="1.0.0",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[],
        )

        # Create agent with fixed resolver
        agent = A2aAgent(
            name="TestAgent",
            agent_card=valid_card,
        )

        # First call should succeed
        card = await agent._get_agent_card()
        self.assertEqual(card.name, "ValidAgent")
        self.assertEqual(agent._agent_card.name, "ValidAgent")

        # Now test with invalid card (missing url) that should fall back
        # Note: We can't create invalid AgentCard in Pydantic, so we mock
        # the resolver to return an invalid card during validation

        # Create a mock that raises validation error
        async def mock_get_invalid_card() -> None:
            raise RuntimeError("Invalid agent card")

        agent._agent_card_resolver.get_agent_card = mock_get_invalid_card

        # Should fall back to previous valid card
        card = await agent._get_agent_card()
        self.assertEqual(card.name, "ValidAgent")

    async def test_msg_metadata_preservation(self) -> None:
        """Test that message metadata is preserved during conversion."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._is_ready = True

        original_msg = Msg(
            name="user",
            content="Test",
            role="user",
            metadata={"custom_key": "custom_value"},
        )

        # Convert to A2A and back
        a2a_msg = agent._convert_msgs_to_a2a_message([original_msg])

        # Check metadata is stored
        self.assertIsNotNone(a2a_msg.metadata)
        self.assertIn(original_msg.id, a2a_msg.metadata)

        # Convert back
        result_msg = agent._convert_a2a_message_to_msg(a2a_msg)

        # Metadata should be preserved
        self.assertEqual(result_msg.metadata, {"custom_key": "custom_value"})

    async def test_media_block_conversions(self) -> None:
        """Test media block conversions (Image, Audio, Video)."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Test URL source (without media_type, as URLSource doesn't have it)
        url_image = ImageBlock(
            type="image",
            source=URLSource(
                type="url",
                url="http://example.com/img.jpg",
            ),
        )
        part = agent._convert_content_block_to_part(url_image)
        self.assertIsNotNone(part)
        self.assertIsInstance(part.root.file, FileWithUri)
        # Should be inferred as "image/*"
        self.assertEqual(part.root.file.mime_type, "image/*")

        # Test base64 source with data
        base64_audio = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                media_type="audio/mp3",
                data="SGVsbG8=",
            ),
        )
        part = agent._convert_content_block_to_part(base64_audio)
        self.assertIsNotNone(part)
        self.assertIsInstance(part.root.file, FileWithBytes)

    async def test_multiple_msgs_merge(self) -> None:
        """Test merging multiple Msgs into single A2A message."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        msgs = [
            Msg(name="user", content="First message", role="user"),
            Msg(name="user", content="Second message", role="user"),
            Msg(name="assistant", content="Third message", role="assistant"),
        ]

        a2a_msg = agent._convert_msgs_to_a2a_message(msgs)

        # All messages should be merged into one A2A message
        self.assertEqual(len(a2a_msg.parts), 3)
        # Each part should have tracking metadata
        for part in a2a_msg.parts:
            self.assertIn("_agentscope_msg_id", part.root.metadata)
            self.assertIn("_agentscope_msg_source", part.root.metadata)

    async def test_observe_method(self) -> None:
        """Test observe method stores messages for next reply."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._a2a_client_factory = MockClientFactory()
        agent._is_ready = True

        # Initially no observed messages
        self.assertEqual(len(agent._observed_msgs), 0)

        # Observe single message
        msg1 = Msg(name="user", content="First observed", role="user")
        await agent.observe(msg1)
        self.assertEqual(len(agent._observed_msgs), 1)

        # Observe multiple messages
        msg2 = Msg(name="user", content="Second observed", role="user")
        msg3 = Msg(name="user", content="Third observed", role="user")
        await agent.observe([msg2, msg3])
        self.assertEqual(len(agent._observed_msgs), 3)

        # Observe None should not change anything
        await agent.observe(None)
        self.assertEqual(len(agent._observed_msgs), 3)

    async def test_observe_and_reply_merge(self) -> None:
        """Test that observed messages are merged with reply input."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        mock_factory = MockClientFactory()
        agent._a2a_client_factory = mock_factory
        agent._is_ready = True

        # Observe some messages
        msg1 = Msg(name="user", content="Observed message", role="user")
        await agent.observe(msg1)

        # Reply with another message
        msg2 = Msg(name="user", content="Reply message", role="user")
        await agent.reply(msg2)

        # Check that the sent A2A message contains both observed and input
        sent_msg = mock_factory.created_clients[0].sent_messages[0]
        self.assertEqual(len(sent_msg.parts), 2)

        # Check observed messages were cleared after reply
        self.assertEqual(len(agent._observed_msgs), 0)

    async def test_reply_with_only_observed_messages(self) -> None:
        """Test reply with None input uses only observed messages."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        mock_factory = MockClientFactory()
        agent._a2a_client_factory = mock_factory
        agent._is_ready = True

        # Observe a message
        msg = Msg(name="user", content="Only observed", role="user")
        await agent.observe(msg)

        # Reply with None
        await agent.reply(None)

        # Should have sent the observed message
        sent_msg = mock_factory.created_clients[0].sent_messages[0]
        self.assertEqual(len(sent_msg.parts), 1)
        self.assertEqual(sent_msg.parts[0].root.text, "Only observed")

        # Observed messages should be cleared
        self.assertEqual(len(agent._observed_msgs), 0)

    async def test_handle_interrupt(self) -> None:
        """Test handle_interrupt method."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )
        agent._is_ready = True

        # Initially no observed messages
        self.assertEqual(len(agent._observed_msgs), 0)

        # Call handle_interrupt
        response = await agent.handle_interrupt()

        # Check response
        self.assertEqual(response.name, "TestAgent")
        self.assertEqual(response.role, "assistant")
        self.assertIn("interrupted", response.content)
        self.assertTrue(response.metadata.get("_is_interrupted", False))

        # Check that response was added to observed messages
        self.assertEqual(len(agent._observed_msgs), 1)
        self.assertEqual(agent._observed_msgs[0], response)

    async def test_convert_task_status_to_msg(self) -> None:
        """Test core method _convert_task_status_to_msg."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        # Test with status message
        status = TaskStatus(
            state=TaskState.working,
            message=A2AMessage(
                message_id="msg-1",
                role=A2ARole.agent,
                parts=[Part(root=TextPart(text="Working on it..."))],
            ),
        )
        msg = agent._convert_task_status_to_msg(status, "task-123")

        self.assertEqual(msg.name, "TestAgent")
        self.assertIn("Task ID: task-123", msg.content[0]["text"])

        # Test without status message
        status_no_msg = TaskStatus(state=TaskState.submitted)
        msg_no_content = agent._convert_task_status_to_msg(
            status_no_msg,
            "task-456",
        )

        self.assertIn("Task ID: task-456", msg_no_content.content[0]["text"])
        self.assertIn("submitted", msg_no_content.content[0]["text"])

    async def test_convert_artifact_to_content_blocks(self) -> None:
        """Test core method _convert_artifact_to_content_blocks."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        artifact = Artifact(
            artifact_id="art-1",
            name="test_artifact",
            parts=[
                Part(root=TextPart(text="Content 1")),
                Part(root=TextPart(text="Content 2")),
            ],
        )

        blocks = agent._convert_artifact_to_content_blocks(artifact)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["text"], "Content 1")
        self.assertEqual(blocks[1]["text"], "Content 2")

    async def test_convert_status_event_to_msg(self) -> None:
        """Test _convert_status_event_to_msg method."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        event = TaskStatusUpdateEvent(
            task_id="task-789",
            context_id="ctx-1",
            final=False,
            status=TaskStatus(
                state=TaskState.working,
                message=A2AMessage(
                    message_id="msg-1",
                    role=A2ARole.agent,
                    parts=[Part(root=TextPart(text="Processing..."))],
                ),
            ),
            metadata={"custom_key": "custom_value"},
        )

        msg = agent._convert_status_event_to_msg(event)

        self.assertEqual(msg.name, "TestAgent")
        self.assertIn("Task ID: task-789", msg.content[0]["text"])

        # Check event-specific metadata
        self.assertEqual(msg.metadata["_a2a_event_type"], "status_update")
        self.assertEqual(msg.metadata["_a2a_is_final"], False)
        self.assertEqual(msg.metadata["_a2a_context_id"], "ctx-1")

        # Check preserved original metadata
        self.assertEqual(msg.metadata["custom_key"], "custom_value")

    async def test_convert_artifact_event_to_msg(self) -> None:
        """Test _convert_artifact_event_to_msg method."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        event = TaskArtifactUpdateEvent(
            task_id="task-999",
            context_id="ctx-2",
            append=False,
            last_chunk=True,
            artifact=Artifact(
                artifact_id="art-2",
                name="output_artifact",
                parts=[Part(root=TextPart(text="Generated content"))],
                metadata={"artifact_meta": "artifact_value"},
            ),
            metadata={"event_meta": "event_value"},
        )

        msg = agent._convert_artifact_event_to_msg(event)

        self.assertEqual(msg.name, "TestAgent")
        self.assertEqual(len(msg.content), 1)
        self.assertEqual(msg.content[0]["text"], "Generated content")

        # Check event-specific metadata
        self.assertEqual(msg.metadata["_a2a_event_type"], "artifact_update")
        self.assertEqual(msg.metadata["_a2a_task_id"], "task-999")
        self.assertEqual(msg.metadata["_a2a_context_id"], "ctx-2")
        self.assertEqual(msg.metadata["_a2a_artifact_id"], "art-2")
        self.assertEqual(msg.metadata["_a2a_artifact_name"], "output_artifact")
        self.assertEqual(msg.metadata["_a2a_is_append"], False)
        self.assertEqual(msg.metadata["_a2a_is_last_chunk"], True)

        # Check preserved original metadata
        self.assertEqual(msg.metadata["artifact_meta"], "artifact_value")
        self.assertEqual(msg.metadata["event_meta"], "event_value")

    async def test_convert_task_artifacts_to_msg_with_metadata(self) -> None:
        """Test artifact metadata preservation."""
        agent = A2aAgent(
            name="TestAgent",
            agent_card=self.test_agent_card,
        )

        task = Task(
            id="task-1",
            context_id="context-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="art-1",
                    name="first",
                    parts=[Part(root=TextPart(text="Content 1"))],
                    metadata={"key1": "value1"},
                ),
                Artifact(
                    artifact_id="art-2",
                    name="second",
                    parts=[Part(root=TextPart(text="Content 2"))],
                    metadata={"key2": "value2"},
                ),
            ],
        )

        msg = agent._convert_task_artifacts_to_msg(task)

        self.assertIsNotNone(msg)
        self.assertEqual(len(msg.content), 2)

        # Check artifacts metadata is preserved
        self.assertIn("_a2a_artifacts_metadata", msg.metadata)
        artifacts_meta = msg.metadata["_a2a_artifacts_metadata"]
        self.assertEqual(artifacts_meta["art-1"], {"key1": "value1"})
        self.assertEqual(artifacts_meta["art-2"], {"key2": "value2"})
