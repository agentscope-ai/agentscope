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
        self.assertListEqual(
            [_.model_dump() for _ in a2a_msg.parts],
            [
                {
                    "kind": "text",
                    "metadata": None,
                    "text": "Hello, how are you?",
                },
                {
                    "kind": "text",
                    "metadata": None,
                    "text": "Hello, how are you?",
                },
                {
                    "kind": "text",
                    "metadata": None,
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
                    "metadata": None,
                },
                {
                    "data": {
                        "type": "tool_result",
                        "id": "tool_1",
                        "name": "tool_1",
                        "output": "Tool output here.",
                    },
                    "kind": "data",
                    "metadata": None,
                },
                {
                    "file": {
                        "mimeType": None,
                        "name": None,
                        "uri": "https://example.com/image.png",
                    },
                    "kind": "file",
                    "metadata": None,
                },
                {
                    "file": {
                        "bytes": "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+"
                        "AAACABAAZGF0YQAAAAA=",
                        "mimeType": "audio/wav",
                        "name": None,
                    },
                    "kind": "file",
                    "metadata": None,
                },
                {
                    "file": {
                        "mimeType": None,
                        "name": None,
                        "uri": "https://example.com/video.mp4",
                    },
                    "kind": "file",
                    "metadata": None,
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
        """Test conversion from A2A task to agentscope message."""

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
        self.assertEqual(len(as_msgs), 2)
        self.maxDiff = None
        self.assertDictEqual(
            as_msgs[0].to_dict(),
            {
                "id": as_msgs[0].id,
                "name": "Friday",
                "role": "user",
                "content": [
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
                "metadata": None,
                "timestamp": as_msgs[0].timestamp,
            },
        )

        self.assertDictEqual(
            as_msgs[1].to_dict(),
            {
                "id": as_msgs[1].id,
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "This is an artifact text part."},
                    {
                        "type": "tool_result",
                        "id": "tool_2",
                        "name": "tool_2",
                        "output": "Artifact tool output.",
                    },
                ],
                "metadata": None,
                "timestamp": as_msgs[1].timestamp,
            },
        )
