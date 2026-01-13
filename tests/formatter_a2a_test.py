# -*- coding: utf-8 -*-
"""Test the a2a formatter class."""
from unittest import IsolatedAsyncioTestCase

from a2a.types import (
    Message,
    TextPart,
    FilePart,
    DataPart,
    FileWithUri,
    FileWithBytes,
    Role,
    Part,
    Task,
    Artifact,
    TaskStatus,
    TaskState,
)

from agentscope.formatter import A2AChatFormatter
from agentscope.message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ImageBlock,
    URLSource,
    Base64Source,
    AudioBlock,
    VideoBlock,
)


class A2AFormatterTest(IsolatedAsyncioTestCase):
    """Test the A2A formatter class."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.formatter = A2AChatFormatter()
        self.as_msgs = [
            Msg(
                "user",
                content="Hello, how are you?",
                role="user",
            ),
            Msg(
                "user",
                content=[
                    TextBlock(
                        type="text",
                        text="Hello, how are you?",
                    ),
                    ThinkingBlock(
                        type="thinking",
                        thinking="yes",
                    ),
                    ToolUseBlock(
                        type="tool_use",
                        id="tool_1",
                        name="tool_1",
                        input={"param1": "value1"},
                    ),
                    ToolResultBlock(
                        type="tool_result",
                        id="tool_1",
                        name="tool_1",
                        output="Tool output here.",
                    ),
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url="https://example.com/image.png",
                        ),
                    ),
                    AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data="UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+"
                            "AAACABAAZGF0YQAAAAA=",
                            media_type="audio/wav",
                        ),
                    ),
                    VideoBlock(
                        type="video",
                        source=URLSource(
                            type="url",
                            url="https://example.com/video.mp4",
                        ),
                    ),
                ],
                role="user",
            ),
        ]
        self.a2a_msg = Message(
            role=Role.user,
            context_id="123",
            extensions=["ext1", "ext2"],
            message_id="abc",
            parts=[
                Part(
                    root=TextPart(
                        text="Hello, how are you?",
                    ),
                ),
                Part(
                    root=FilePart(
                        file=FileWithUri(
                            mime_type="audio/wav",
                            name="greeting.wav",
                            uri="https://example.com/greeting.wav",
                        ),
                    ),
                ),
                Part(
                    root=FilePart(
                        file=FileWithBytes(
                            bytes="UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+"
                            "AAACABAAZGF0YQAAAAA=",
                            mime_type="audio/wav",
                            name="greeting.wav",
                        ),
                    ),
                ),
                Part(
                    root=DataPart(
                        data={
                            "type": "tool_use",
                            "id": "tool_1",
                            "name": "tool_1",
                            "input": {
                                "param1": "value1",
                            },
                        },
                    ),
                ),
                Part(
                    root=DataPart(
                        data={
                            "type": "tool_result",
                            "id": "tool_1",
                            "name": "tool_1",
                            "output": "Tool output here.",
                        },
                    ),
                ),
                Part(
                    root=DataPart(
                        data={
                            "type": "unknown_type",
                            "content": "Some unknown content",
                        },
                    ),
                ),
            ],
        )

    async def test_as_to_a2a(self) -> None:
        """Test conversion from agentscope message to A2A message."""
        a2a_msg = await self.formatter.format(self.as_msgs)
        self.assertIsInstance(a2a_msg, Message)

        # Verify that all parts have metadata with msg source and id
        for part in a2a_msg.parts:
            # Metadata is stored on part.root, not on part itself
            self.assertIsNotNone(part.root.metadata)
            self.assertIn("_agentscope_msg_source", part.root.metadata)
            self.assertIn("_agentscope_msg_id", part.root.metadata)

        # Verify the content of each part (excluding metadata for comparison)
        parts_data = []
        for part in a2a_msg.parts:
            data = part.model_dump()
            # Remove metadata for content comparison
            data.pop("metadata", None)
            parts_data.append(data)

        self.assertListEqual(
            parts_data,
            [
                {
                    "kind": "text",
                    "text": "Hello, how are you?",
                },
                {
                    "kind": "text",
                    "text": "Hello, how are you?",
                },
                {
                    "kind": "text",
                    "text": "yes",
                },
                {
                    "data": {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "tool_1",
                        "input": {
                            "param1": "value1",
                        },
                    },
                    "kind": "data",
                },
                {
                    "data": {
                        "type": "tool_result",
                        "id": "tool_1",
                        "name": "tool_1",
                        "output": "Tool output here.",
                    },
                    "kind": "data",
                },
                {
                    "file": {
                        "mimeType": None,
                        "name": None,
                        "uri": "https://example.com/image.png",
                    },
                    "kind": "file",
                },
                {
                    "file": {
                        "bytes": "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+"
                        "AAACABAAZGF0YQAAAAA=",
                        "mimeType": "audio/wav",
                        "name": None,
                    },
                    "kind": "file",
                },
                {
                    "file": {
                        "mimeType": None,
                        "name": None,
                        "uri": "https://example.com/video.mp4",
                    },
                    "kind": "file",
                },
            ],
        )
        self.assertEqual(
            a2a_msg.role,
            "user",
        )

        a2a_msg = await self.formatter.format([])
        self.assertListEqual(
            a2a_msg.parts,
            [],
        )
        self.assertEqual(
            a2a_msg.role,
            "user",
        )

    async def test_a2a_msg_to_as(self) -> None:
        """Test conversion from A2A message to agentscope message."""
        as_msg = await self.formatter.format_a2a_message(
            "Friday",
            self.a2a_msg,
        )

        self.assertEqual(
            as_msg.role,
            "user",
        )
        self.assertListEqual(
            as_msg.get_content_blocks(),
            [
                {"type": "text", "text": "Hello, how are you?"},
                {
                    "type": "audio",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/greeting.wav",
                    },
                },
                {
                    "type": "audio",
                    "source": {
                        "type": "base64",
                        "media_type": "audio/wav",
                        "data": "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+"
                        "AAACABAAZGF0YQAAAAA=",
                    },
                },
                {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "tool_1",
                    "input": {"param1": "value1"},
                },
                {
                    "type": "tool_result",
                    "id": "tool_1",
                    "name": "tool_1",
                    "output": "Tool output here.",
                },
                {
                    "type": "text",
                    "text": "{'type': 'unknown_type', 'content': 'Some "
                    "unknown content'}",
                },
            ],
        )

    async def test_a2a_task_to_as(self) -> None:
        """Test conversion from A2A task to agentscope message.

        Role mapping: A2A Role.agent -> 'assistant', Role.user -> 'user'.
        Artifact content is merged into the message.
        """
        as_msgs = await self.formatter.format_a2a_task(
            name="Friday",
            task=Task(
                context_id="abc",
                artifacts=[
                    Artifact(
                        artifact_id="123",
                        parts=[
                            Part(
                                root=TextPart(
                                    text="This is an artifact text part.",
                                ),
                            ),
                            Part(
                                root=DataPart(
                                    data={
                                        "type": "tool_result",
                                        "id": "tool_2",
                                        "name": "tool_2",
                                        "output": "Artifact tool output.",
                                    },
                                ),
                            ),
                        ],
                    ),
                ],
                id="task_1",
                status=TaskStatus(
                    message=self.a2a_msg,
                    state=TaskState.completed,
                    timestamp="def",
                ),
            ),
        )

        # self.a2a_msg has Role.user, so first msg has role='user'
        # Artifact creates a new assistant msg since first msg is not assistant
        self.assertEqual(len(as_msgs), 2)
        self.maxDiff = None

        # First message from status.message (Role.user -> 'user')
        self.assertEqual(as_msgs[0].name, "Friday")
        self.assertEqual(as_msgs[0].role, "user")

        # Second message from artifact (role='assistant')
        self.assertEqual(as_msgs[1].name, "Friday")
        self.assertEqual(as_msgs[1].role, "assistant")

        # Verify artifact content
        artifact_blocks = as_msgs[1].get_content_blocks()
        text_blocks = [b for b in artifact_blocks if b.get("type") == "text"]
        self.assertTrue(
            any(
                "This is an artifact text part." in b["text"]
                for b in text_blocks
            ),
        )

        tool_results = [
            b for b in artifact_blocks if b.get("type") == "tool_result"
        ]
        artifact_tool_result = [
            b for b in tool_results if b.get("id") == "tool_2"
        ]
        self.assertEqual(len(artifact_tool_result), 1)
        self.assertEqual(
            artifact_tool_result[0]["output"],
            "Artifact tool output.",
        )

    async def test_context_id_included_when_set(self) -> None:
        """Test that context_id is included in A2A Message when set."""
        formatter_with_context = A2AChatFormatter(
            context_id="test-context-123"
        )
        a2a_msg = await formatter_with_context.format(self.as_msgs)

        self.assertIsInstance(a2a_msg, Message)
        self.assertEqual(a2a_msg.context_id, "test-context-123")

    async def test_context_id_not_included_when_none(self) -> None:
        """Test that context_id is not included in A2A Message when not set."""
        formatter_without_context = (
            A2AChatFormatter()
        )  # context_id defaults to None
        a2a_msg = await formatter_without_context.format(self.as_msgs)

        self.assertIsInstance(a2a_msg, Message)
        self.assertIsNone(a2a_msg.context_id)
