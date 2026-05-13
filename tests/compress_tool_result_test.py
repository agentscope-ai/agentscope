# -*- coding: utf-8 -*-
"""The unittests for the tool result compression."""
# pylint: disable=protected-access, unused-argument
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, ContextConfig
from agentscope.message import (
    ToolResultBlock,
    TextBlock,
    DataBlock,
    Base64Source,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class ToolResultCompressionTest(IsolatedAsyncioTestCase):
    """Test cases for tool result compression."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.mock_model = MockModel()
        self.agent = Agent(
            name="TestAgent",
            system_prompt="Test system prompt",
            model=self.mock_model,
            toolkit=Toolkit(),
            context_config=ContextConfig(
                tool_result_limit=100,
            ),
            state=AgentState(session_id="test_session"),
        )

    async def test_below_limit(self) -> None:
        """Test when tool result is below the token limit."""
        tool_result = ToolResultBlock(
            id="test_1",
            name="test_tool",
            output=[
                TextBlock(text="Short text 1"),
                TextBlock(text="Short text 2"),
            ],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function that returns a fixed count."""
            return 50

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        self.assertEqual(reserved, tool_result)
        self.assertIsNone(offload)

    async def test_equal_to_limit(self) -> None:
        """Test when tool result is exactly at the token limit."""
        tool_result = ToolResultBlock(
            id="test_2",
            name="test_tool",
            output=[TextBlock(text="Text at limit")],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function that returns a fixed count."""
            return 100

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        self.assertEqual(reserved, tool_result)
        self.assertIsNone(offload)

    async def test_boundary_last_block_text(self) -> None:
        """Test when boundary is the last block and it is a TextBlock."""
        block1 = TextBlock(text="A" * 20, id="block1")
        block2 = TextBlock(text="B" * 20, id="block2")
        block3 = TextBlock(text="C" * 100, id="block3")

        tool_result = ToolResultBlock(
            id="test_3",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function that counts text length in
            blocks."""
            content = messages[0].content
            if isinstance(content, list):
                total = sum(len(b.text) for b in content if hasattr(b, "text"))
                return total
            return 0

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results
        self.assertIsNotNone(reserved)
        self.assertIsNotNone(offload)

        # Verify ToolResultBlock metadata
        self.assertEqual(reserved.id, tool_result.id)
        self.assertEqual(reserved.name, tool_result.name)
        self.assertEqual(reserved.state, tool_result.state)
        self.assertEqual(offload.id, tool_result.id)
        self.assertEqual(offload.name, tool_result.name)
        self.assertEqual(offload.state, tool_result.state)

        # Verify results using assertListEqual
        expected_reserved = [
            {"type": "text", "text": "A" * 20, "id": "block1"},
            {"type": "text", "text": "B" * 20 + "C" * 60, "id": "block2"},
        ]
        expected_offload = [
            {"type": "text", "text": "C" * 40, "id": "block3"},
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def test_boundary_last_block_data(self) -> None:
        """Test when boundary is the last block and it is a DataBlock."""
        block1 = TextBlock(text="A" * 20, id="block1")
        block2 = TextBlock(text="B" * 20, id="block2")
        block3 = DataBlock(
            source=Base64Source(data="base64data", media_type="image/png"),
            id="block3",
        )

        tool_result = ToolResultBlock(
            id="test_4",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function based on content length."""
            content = messages[0].content
            if isinstance(content, list):
                if len(content) == 3:
                    return 150
                elif len(content) == 2:
                    return 80
            return 50

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results
        self.assertIsNotNone(reserved)
        self.assertIsNotNone(offload)

        # Verify ToolResultBlock metadata
        self.assertEqual(reserved.id, tool_result.id)
        self.assertEqual(offload.id, tool_result.id)

        # Verify results using assertListEqual
        expected_reserved = [
            {"type": "text", "text": "A" * 20, "id": "block1"},
            {"type": "text", "text": "B" * 20, "id": "block2"},
        ]
        expected_offload = [
            {
                "type": "data",
                "id": "block3",
                "source": {
                    "type": "base64",
                    "data": "base64data",
                    "media_type": "image/png",
                },
                "name": None,
            },
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def test_boundary_first_block_text(self) -> None:
        """Test when boundary is the first block and it is a TextBlock."""
        block1 = TextBlock(text="A" * 100, id="block1")
        block2 = TextBlock(text="B" * 20, id="block2")
        block3 = TextBlock(text="C" * 20, id="block3")

        tool_result = ToolResultBlock(
            id="test_5",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function that counts text length in
            blocks."""
            content = messages[0].content
            if isinstance(content, list):
                total = sum(len(b.text) for b in content if hasattr(b, "text"))
                return total
            return 0

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results using assertListEqual
        expected_reserved = [
            {"type": "text", "text": "A" * 100, "id": "block1"},
        ]
        expected_offload = [
            {"type": "text", "text": "B" * 20, "id": "block2"},
            {"type": "text", "text": "C" * 20, "id": "block3"},
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        print(offload.output)
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def test_boundary_first_block_data(self) -> None:
        """Test when boundary is the first block and it is a DataBlock."""
        block1 = DataBlock(
            source=Base64Source(data="base64data", media_type="image/png"),
            id="block1",
        )
        block2 = TextBlock(text="B" * 20, id="block2")
        block3 = TextBlock(text="C" * 20, id="block3")

        tool_result = ToolResultBlock(
            id="test_6",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function based on content length."""
            content = messages[0].content
            if isinstance(content, list):
                if len(content) == 3:
                    return 150
                elif len(content) == 2:
                    return 80
                elif len(content) == 1:
                    return 60
            return 50

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results
        self.assertIsNotNone(reserved)
        self.assertIsNotNone(offload)

        # Verify ToolResultBlock metadata
        self.assertEqual(reserved.id, tool_result.id)
        self.assertEqual(offload.id, tool_result.id)

        # Verify results using assertListEqual
        expected_reserved = [
            {
                "type": "data",
                "id": "block1",
                "source": {
                    "type": "base64",
                    "data": "base64data",
                    "media_type": "image/png",
                },
                "name": None,
            },
            {"type": "text", "text": "B" * 20 + "C" * 5, "id": "block2"},
        ]
        expected_offload = [
            {"type": "text", "text": "C" * 15, "id": "block3"},
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def test_boundary_middle_block_text(self) -> None:
        """Test when boundary is a middle block and it is a TextBlock."""
        block1 = TextBlock(text="A" * 20, id="block1")
        block2 = TextBlock(text="B" * 100, id="block2")
        block3 = TextBlock(text="C" * 20, id="block3")

        tool_result = ToolResultBlock(
            id="test_7",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function that counts text length in
            blocks."""
            content = messages[0].content
            if isinstance(content, list):
                total = sum(len(b.text) for b in content if hasattr(b, "text"))
                return total
            return 0

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results
        self.assertIsNotNone(reserved)
        self.assertIsNotNone(offload)

        # Verify ToolResultBlock metadata
        self.assertEqual(reserved.id, tool_result.id)
        self.assertEqual(offload.id, tool_result.id)

        # Verify results using assertListEqual
        expected_reserved = [
            {"type": "text", "text": "A" * 20 + "B" * 80, "id": "block1"},
        ]
        expected_offload = [
            {"type": "text", "text": "B" * 20 + "C" * 20, "id": "block3"},
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def test_boundary_middle_block_data(self) -> None:
        """Test when boundary is a middle block and it is a DataBlock."""
        block1 = TextBlock(text="A" * 20, id="block1")
        block2 = DataBlock(
            source=Base64Source(data="base64data", media_type="image/png"),
            id="block2",
        )
        block3 = TextBlock(text="C" * 20, id="block3")

        tool_result = ToolResultBlock(
            id="test_8",
            name="test_tool",
            output=[block1, block2, block3],
        )

        async def mock_count_tokens(
            messages: list,
            tools: list | None = None,
        ) -> int:
            """Mock token counting function based on content length."""
            content = messages[0].content
            if isinstance(content, list):
                if len(content) == 3:
                    return 150
                elif len(content) == 2:
                    return 80
                elif len(content) == 1:
                    return 40
            return 50

        self.mock_model.count_tokens = mock_count_tokens
        (
            reserved,
            offload,
        ) = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        # Verify results
        self.assertIsNotNone(reserved)
        self.assertIsNotNone(offload)

        # Verify ToolResultBlock metadata
        self.assertEqual(reserved.id, tool_result.id)
        self.assertEqual(offload.id, tool_result.id)

        # Verify results using assertListEqual
        expected_reserved = [
            {"type": "text", "text": "A" * 20, "id": "block1"},
            {
                "type": "data",
                "id": "block2",
                "source": {
                    "type": "base64",
                    "data": "base64data",
                    "media_type": "image/png",
                },
                "name": None,
            },
            {"type": "text", "text": "C" * 5, "id": "block3"},
        ]
        expected_offload = [
            {"type": "text", "text": "C" * 15, "id": "block3"},
        ]

        self.assertListEqual(
            [b.model_dump() for b in reserved.output],
            expected_reserved,
        )
        self.assertListEqual(
            [b.model_dump() for b in offload.output],
            expected_offload,
        )

    async def asyncTearDown(self) -> None:
        """The async teardown method."""
