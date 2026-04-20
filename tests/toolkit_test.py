# -*- coding: utf-8 -*-
"""Toolkit test case."""
import json
from typing import Any, AsyncGenerator, Generator
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.agent import AgentState
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    DataBlock,
    Base64Source,
)
from agentscope.tool import (
    Toolkit,
    ToolBase,
    ToolChunk,
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    ToolResponse,
)


class Tool1(ToolBase):
    """A simple tool for testing."""

    name: str = "tool_1"
    description: str = "A simple tool for testing."
    input_schema: dict = {
        "type": "object",
        "properties": {},
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Do you want to use my_tool?",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Run the tool."""
        return ToolChunk(
            content=[TextBlock(text="Hello, world!")],
        )


class Tool2(ToolBase):
    """Test tool 2"""

    name: str = "tool_2"
    description: str = "Test tool 2."
    input_schema: dict = {
        "type": "object",
        "properties": {},
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Do you want to use my_tool?",
        )

    async def __call__(self, **kwargs: Any) -> AsyncGenerator[ToolChunk, None]:
        """Run the tool."""
        yield ToolChunk(
            content=[TextBlock(text="123", id="a")],
        )
        yield ToolChunk(
            content=[TextBlock(text="456", id="b")],
        )
        yield ToolChunk(
            content=[TextBlock(text="789", id="b")],
        )
        yield ToolChunk(
            content=[
                DataBlock(
                    id="1",
                    source=Base64Source(
                        data="abc",
                        media_type="image/jpeg",
                    ),
                ),
            ],
        )
        yield ToolChunk(
            content=[
                DataBlock(
                    id="2",
                    source=Base64Source(
                        data="***",
                        media_type="image/jpeg",
                    ),
                ),
            ],
        )
        yield ToolChunk(
            content=[
                DataBlock(
                    id="1",
                    source=Base64Source(
                        data="def",
                        media_type="image/jpeg",
                    ),
                ),
            ],
        )


class ToolkitTest(IsolatedAsyncioTestCase):
    """The toolkit test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""

    async def test_initialize(self) -> None:
        """The template test."""

        # Initialize the toolkit
        toolkit = Toolkit()

        # No tools
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 0)

        # Initialize the toolkit with tools
        toolkit = Toolkit(tools=[Tool1(), Tool2()])
        schemas = toolkit.get_function_schemas()
        self.assertListEqual(
            schemas,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "tool_1",
                        "description": "A simple tool for testing.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "tool_2",
                        "description": "Test tool 2.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            ],
        )

    async def test_tool(self) -> None:
        """Test executing a tool."""
        toolkit = Toolkit(tools=[Tool1(), Tool2()])
        state = AgentState()

        # Test Tool1 (returns single ToolChunk)
        tool_call_1 = ToolCallBlock(
            id="test_1",
            name="tool_1",
            input=json.dumps({}),
        )

        chunks_1 = []
        response_1 = None
        async for result in toolkit.call_tool(tool_call_1, state):
            if isinstance(result, ToolChunk):
                chunks_1.append(result)
            elif isinstance(result, ToolResponse):
                response_1 = result

        # Verify Tool1 chunks
        self.assertEqual(len(chunks_1), 1)
        self.assertDictEqual(
            chunks_1[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello, world!",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Verify Tool1 response
        self.assertIsNotNone(response_1)
        self.assertDictEqual(
            response_1.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Hello, world!",
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_1",
            },
        )

        # Test Tool2 (returns async generator of ToolChunks)
        tool_call_2 = ToolCallBlock(
            id="test_2",
            name="tool_2",
            input=json.dumps({}),
        )

        chunks_2 = []
        response_2 = None
        async for result in toolkit.call_tool(tool_call_2, state):
            if isinstance(result, ToolChunk):
                chunks_2.append(result)
            elif isinstance(result, ToolResponse):
                response_2 = result

        # Verify Tool2 chunks
        self.assertEqual(len(chunks_2), 6)

        # First chunk - TextBlock id="a"
        self.assertDictEqual(
            chunks_2[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": "a",
                        "text": "123",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Second chunk - TextBlock id="b"
        self.assertDictEqual(
            chunks_2[1].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": "b",
                        "text": "456",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Third chunk - TextBlock id="b" (same id as second)
        self.assertDictEqual(
            chunks_2[2].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": "b",
                        "text": "789",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Fourth chunk - DataBlock id="1"
        self.assertDictEqual(
            chunks_2[3].model_dump(),
            {
                "content": [
                    {
                        "type": "data",
                        "id": "1",
                        "name": None,
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "abc",
                        },
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Fifth chunk - DataBlock id="2"
        self.assertDictEqual(
            chunks_2[4].model_dump(),
            {
                "content": [
                    {
                        "type": "data",
                        "id": "2",
                        "name": None,
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "***",
                        },
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Sixth chunk - DataBlock id="1" (same id as fourth)
        self.assertDictEqual(
            chunks_2[5].model_dump(),
            {
                "content": [
                    {
                        "type": "data",
                        "id": "1",
                        "name": None,
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "def",
                        },
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Verify Tool2 response - blocks with same id are merged,
        # and consecutive TextBlocks are also merged
        # TextBlock id="a" (123) + id="b" (456) + id="b" (789) -> merged to
        # "123456789" with id="a"
        # DataBlock id="1" appears twice, should be merged to "abcdef"
        self.assertIsNotNone(response_2)
        self.assertDictEqual(
            response_2.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": "a",
                        # All consecutive TextBlocks merged
                        "text": "123456789",
                    },
                    {
                        "type": "data",
                        "id": "1",
                        "name": None,
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "abcdef",  # Merged: "abc" + "def"
                        },
                    },
                    {
                        "type": "data",
                        "id": "2",
                        "name": None,
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "***",
                        },
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_2",
            },
        )


class RegisterFunctionTest(IsolatedAsyncioTestCase):
    """Test registering different functions in the toolkit."""

    async def test_sync_non_streaming_function(self) -> None:
        """Test registering a synchronous non-streaming function."""

        def add_numbers(a: int, b: int) -> ToolChunk:
            """Add two numbers together.

            Args:
                a: The first number
                b: The second number
            """
            result = a + b
            return ToolChunk(
                content=[TextBlock(text=f"Result: {result}")],
            )

        toolkit = Toolkit()
        toolkit.register_function(add_numbers, group="basic")

        # Test schema
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertDictEqual(
            schemas[0],
            {
                "type": "function",
                "function": {
                    "name": "add_numbers",
                    "description": "Add two numbers together.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {
                                "type": "integer",
                                "description": "The first number",
                            },
                            "b": {
                                "type": "integer",
                                "description": "The second number",
                            },
                        },
                        "required": ["a", "b"],
                    },
                },
            },
        )

        # Test execution
        state = AgentState()
        tool_call = ToolCallBlock(
            id="test_add",
            name="add_numbers",
            input=json.dumps({"a": 3, "b": 5}),
        )

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        # Verify chunks
        self.assertEqual(len(chunks), 1)
        self.assertDictEqual(
            chunks[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Result: 8",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Verify response
        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Result: 8",
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_add",
            },
        )

    async def test_sync_streaming_function(self) -> None:
        """Test registering a synchronous streaming function."""

        def count_to_n(n: int) -> Generator[ToolChunk, None, None]:
            """Count from 1 to n.

            Args:
                n: The number to count to
            """
            for i in range(1, n + 1):
                yield ToolChunk(
                    content=[TextBlock(text=str(i))],
                )

        toolkit = Toolkit()
        toolkit.register_function(count_to_n, group="basic")

        # Test schema
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertDictEqual(
            schemas[0],
            {
                "type": "function",
                "function": {
                    "name": "count_to_n",
                    "description": "Count from 1 to n.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "n": {
                                "type": "integer",
                                "description": "The number to count to",
                            },
                        },
                        "required": ["n"],
                    },
                },
            },
        )

        # Test execution
        state = AgentState()
        tool_call = ToolCallBlock(
            id="test_count",
            name="count_to_n",
            input=json.dumps({"n": 3}),
        )

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        # Verify chunks
        self.assertEqual(len(chunks), 3)
        for i, chunk in enumerate(chunks, 1):
            self.assertDictEqual(
                chunk.model_dump(),
                {
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": str(i),
                        },
                    ],
                    "state": "running",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
            )

        # Verify response - consecutive TextBlocks are merged
        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "123",  # All consecutive TextBlocks merged
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_count",
            },
        )

    async def test_async_non_streaming_function(self) -> None:
        """Test registering an asynchronous non-streaming function."""

        async def multiply_numbers(x: float, y: float) -> ToolChunk:
            """Multiply two numbers.

            Args:
                x: The first number
                y: The second number
            """
            result = x * y
            return ToolChunk(
                content=[TextBlock(text=f"Product: {result}")],
            )

        toolkit = Toolkit()
        toolkit.register_function(multiply_numbers, group="basic")

        # Test schema
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertDictEqual(
            schemas[0],
            {
                "type": "function",
                "function": {
                    "name": "multiply_numbers",
                    "description": "Multiply two numbers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {
                                "type": "number",
                                "description": "The first number",
                            },
                            "y": {
                                "type": "number",
                                "description": "The second number",
                            },
                        },
                        "required": ["x", "y"],
                    },
                },
            },
        )

        # Test execution
        state = AgentState()
        tool_call = ToolCallBlock(
            id="test_multiply",
            name="multiply_numbers",
            input=json.dumps({"x": 2.5, "y": 4.0}),
        )

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        # Verify chunks
        self.assertEqual(len(chunks), 1)
        self.assertDictEqual(
            chunks[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Product: 10.0",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        # Verify response
        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "Product: 10.0",
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_multiply",
            },
        )

    async def test_async_streaming_function(self) -> None:
        """Test registering an asynchronous streaming function."""

        async def generate_sequence(
            start: int,
            end: int,
        ) -> AsyncGenerator[ToolChunk, None]:
            """Generate a sequence of numbers.

            Args:
                start: The starting number
                end: The ending number
            """
            for i in range(start, end + 1):
                yield ToolChunk(
                    content=[TextBlock(text=f"Number: {i}")],
                )

        toolkit = Toolkit()
        toolkit.register_function(generate_sequence, group="basic")

        # Test schema
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertDictEqual(
            schemas[0],
            {
                "type": "function",
                "function": {
                    "name": "generate_sequence",
                    "description": "Generate a sequence of numbers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "integer",
                                "description": "The starting number",
                            },
                            "end": {
                                "type": "integer",
                                "description": "The ending number",
                            },
                        },
                        "required": ["start", "end"],
                    },
                },
            },
        )

        # Test execution
        state = AgentState()
        tool_call = ToolCallBlock(
            id="test_sequence",
            name="generate_sequence",
            input=json.dumps({"start": 5, "end": 7}),
        )

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        # Verify chunks
        self.assertEqual(len(chunks), 3)
        for chunk, num in zip(chunks, [5, 6, 7]):
            self.assertDictEqual(
                chunk.model_dump(),
                {
                    "content": [
                        {
                            "type": "text",
                            "id": AnyString(),
                            "text": f"Number: {num}",
                        },
                    ],
                    "state": "running",
                    "is_last": True,
                    "metadata": {},
                    "id": AnyString(),
                },
            )

        # Verify response - consecutive TextBlocks are merged
        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        # All consecutive TextBlocks merged
                        "text": "Number: 5Number: 6Number: 7",
                    },
                ],
                "state": "finished",
                "metadata": {},
                "id": "test_sequence",
            },
        )


class ToolGroupTest(IsolatedAsyncioTestCase):
    """The tool group test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.meta_tool_description = (
            "This tool allows you to reset your equipped tools based on your "
            "current task requirements. These tools are organized into "
            "different groups, and you can activate/deactivate them "
            "by specifying the boolean values for each group in the input.\n\n"
            "**Important: The input booleans are the final state of their "
            "corresponding tool groups, not incremental changes.** Any "
            "group not explicitly set to True will be deactivated, "
            "regardless of its previous state.\n\n"
            "**Best practice**: Actively manage your tool groups——activate "
            "only what you need for the current task, and promptly "
            "deactivate groups as soon as they are no longer needed to"
            " conserve context space.\n\n"
            "This tool will return the usage instructions for the activated "
            "tool groups, which you **MUST pay attention to and follow**. "
            "You can also reuse this tool to re-check the instructions."
        )

    async def test_meta_tool(self) -> None:
        """Test creating a tool group."""
        toolkit = Toolkit()

        # Test meta tool when no groups exist
        schemas = toolkit.get_function_schemas()
        self.assertEqual(len(schemas), 0)

        # Without tools
        toolkit.create_tool_group(
            group_name="group_1",
            description="Group 1",
        )

        # The group is created successfully
        self.assertEqual(len(toolkit.groups), 1)
        # The builtin meta tool is activated
        schemas = toolkit.get_function_schemas()

        self.assertListEqual(
            schemas,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "reset_tools",
                        "description": self.meta_tool_description,
                        "parameters": {
                            "properties": {
                                "group_1": {
                                    "default": False,
                                    "description": "Group 1",
                                    "type": "boolean",
                                },
                            },
                            "type": "object",
                        },
                    },
                },
            ],
        )

        # Name conflict
        with self.assertRaises(ValueError):
            toolkit.create_tool_group(
                group_name="group_1",
                description="xxx",
            )

        # A new group with tools
        toolkit.create_tool_group(
            group_name="group_2",
            description="Group 2",
            instructions="This is group 2.",
            tools=[Tool1(), Tool2()],
        )

        self.assertEqual(len(toolkit.groups), 2)
        schemas = toolkit.get_function_schemas()
        self.assertListEqual(
            schemas,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "reset_tools",
                        "description": self.meta_tool_description,
                        "parameters": {
                            "properties": {
                                "group_1": {
                                    "default": False,
                                    "description": "Group 1",
                                    "type": "boolean",
                                },
                                "group_2": {
                                    "default": False,
                                    "description": "Group 2",
                                    "type": "boolean",
                                },
                            },
                            "type": "object",
                        },
                    },
                },
            ],
        )

        # Active one group
        state = AgentState()
        res = toolkit.call_tool(
            ToolCallBlock(
                id="xxx",
                name="reset_tools",
                input=json.dumps({"group_2": True}),
            ),
            state,
        )

        chunk = await anext(res)
        self.assertIsInstance(chunk, ToolChunk)

        chunk = await anext(res)
        self.assertIsInstance(chunk, ToolResponse)

        self.assertDictEqual(
            chunk.model_dump(),
            {
                "id": AnyString(),
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": """The currently activated tool group(s): group_2.
<tool-instructions>
The tool instructions are a collection of suggestions, rules and notifications about how to use the tools in the activated groups.
<group name="group_2">This is group 2.</group>
</tool-instructions>""",  # noqa: E501
                    },
                ],
                "metadata": {},
                "state": "finished",
            },
        )

        # Activate both groups
        res = toolkit.call_tool(
            ToolCallBlock(
                id="xxx",
                name="reset_tools",
                input=json.dumps({"group_2": True, "group_1": True}),
            ),
            state,
        )

        last_chunk = None
        async for chunk in res:
            last_chunk = chunk

        self.assertDictEqual(
            last_chunk.model_dump(),
            {
                "id": AnyString(),
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": """The currently activated tool group(s): group_2, group_1.
<tool-instructions>
The tool instructions are a collection of suggestions, rules and notifications about how to use the tools in the activated groups.
<group name="group_2">This is group 2.</group>
</tool-instructions>""",  # noqa: E501
                    },
                ],
                "metadata": {},
                "state": "finished",
            },
        )

        # deactivate all groups
        res = toolkit.call_tool(
            ToolCallBlock(
                id="xxx",
                name="reset_tools",
                input=json.dumps({}),
            ),
            state,
        )

        last_chunk = None
        async for chunk in res:
            last_chunk = chunk

        self.assertDictEqual(
            last_chunk.model_dump(),
            {
                "id": AnyString(),
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "All tool groups are currently deactivated.",
                    },
                ],
                "metadata": {},
                "state": "finished",
            },
        )
