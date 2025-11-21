# -*- coding: utf-8 -*-
# mypy: disable-error-code="index"
# pylint: disable=too-many-lines
"""Test toolkit module in agentscope."""
import asyncio
import time
from copy import deepcopy
from functools import partial
from typing import Union, Optional, Any, AsyncGenerator, Generator, Tuple
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel, Field

from agentscope.message import ToolUseBlock, TextBlock
from agentscope.tool import ToolResponse, Toolkit


async def aenumerate(
    agen: AsyncGenerator[ToolResponse, None],
) -> AsyncGenerator[Tuple[int, ToolResponse], None]:
    """Asynchronous enumerate function."""
    n = 0
    async for item in agen:
        yield n, item
        n += 1


response1 = ToolResponse(
    content=[TextBlock(type="text", text="1")],
    stream=True,
)
response2 = ToolResponse(
    content=[TextBlock(type="text", text="12")],
    stream=True,
)
response3 = ToolResponse(
    content=[TextBlock(type="text", text="123")],
    is_last=True,
)


async def async_func(raise_cancel: bool) -> ToolResponse:
    """An async function for testing."""
    if raise_cancel:
        await asyncio.sleep(1)
        raise asyncio.CancelledError("test")
    return response1


def sync_func(
    arg1: int,
    arg2: Optional[list[Union[str, int]]] = None,
) -> ToolResponse:
    """A sync function for testing.

    Long description.

    Args:
        arg1 (`int`):
            Test argument 1.
        arg2 (`Optional[list[Union[str, int]]]`, defaults to `None`):
            Test argument 2.
    """
    time.sleep(1)
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"arg1: {arg1}, arg2: {arg2}",
            ),
        ],
    )


class TestCls:
    """A test class for testing."""

    def sync_func(self) -> ToolResponse:
        """A duplicate sync function for testing."""
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="test",
                ),
            ],
        )


async def async_generator_func(
    raise_cancel: bool,
) -> AsyncGenerator[ToolResponse, None]:
    """An async generator function for testing."""
    yield response1
    yield deepcopy(response2)
    if raise_cancel:
        await asyncio.sleep(1)
        raise asyncio.CancelledError("test")
    yield response3


async def async_func_return_async_generator(
    raise_cancel: bool,
) -> AsyncGenerator[ToolResponse, None]:
    """Async function that returns async generator"""
    return async_generator_func(raise_cancel=raise_cancel)


async def async_func_return_sync_generator() -> Generator[
    ToolResponse,
    None,
    None,
]:
    """Async function that returns sync generator"""
    return sync_generator_func()


def sync_generator_func() -> Generator[ToolResponse, None, None]:
    """A sync generator function for testing."""
    yield response1
    yield response2
    yield response3


class StructuredModel(BaseModel):
    """Test structured model"""

    arg3: int = Field(description="Test argument 3.")


class MyBaseModel1(BaseModel):
    """A base model for testing nested $defs merging."""

    c: int = Field(description="Field c")


class MyBaseModel2(BaseModel):
    """A base model that contains nested MyBaseModel1."""

    b: list[MyBaseModel1] = Field(description="List of MyBaseModel1")


class ExtendedModelReusingBaseModel(BaseModel):
    """Extended model that reuses the same BaseModel from original function."""

    another_model: MyBaseModel2 = Field(description="Reusing MyBaseModel2")
    extra_field: str = Field(description="Extra field")


class ToolkitTest(IsolatedAsyncioTestCase):
    """Unittest for the toolkit module."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment before each test."""
        self.toolkit = Toolkit()

        self.sync_func_schema = {
            "type": "function",
            "function": {
                "name": "sync_func",
                "parameters": {
                    "properties": {
                        "arg1": {
                            "description": "Test argument 1.",
                            "type": "integer",
                        },
                        "arg2": {
                            "anyOf": [
                                {
                                    "items": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "integer"},
                                        ],
                                    },
                                    "type": "array",
                                },
                                {"type": "null"},
                            ],
                            "default": None,
                            "description": "Test argument 2.",
                        },
                    },
                    "required": ["arg1"],
                    "type": "object",
                },
                "description": "A sync function for testing.\n\n"
                "Long description.",
            },
        }

    async def test_duplicate_tool_registration(self) -> None:
        """Test duplicate tool function registration."""
        tool_call = ToolUseBlock(
            type="tool_use",
            id="123",
            name="sync_func",
            input={
                "arg1": 55,
            },
        )

        # Add a function
        self.toolkit.register_tool_function(
            sync_func,
        )
        self.assertListEqual(
            [self.sync_func_schema],
            self.toolkit.get_json_schemas(),
        )
        async for chunk in await self.toolkit.call_tool_function(tool_call):
            self.assertListEqual(
                chunk.content,
                [
                    TextBlock(
                        type="text",
                        text="arg1: 55, arg2: None",
                    ),
                ],
            )

        test = TestCls()

        # Try to add the same function with raise strategy
        with self.assertRaises(ValueError):
            self.toolkit.register_tool_function(test.sync_func)

        # Try to add the same function with skip strategy
        self.toolkit.register_tool_function(
            test.sync_func,
            namesake_strategy="skip",
        )
        self.assertListEqual(
            [self.sync_func_schema],
            self.toolkit.get_json_schemas(),
        )

        # Try to add the same function with rename strategy
        self.toolkit.register_tool_function(
            test.sync_func,
            namesake_strategy="rename",
        )
        new_func_name = list(self.toolkit.tools.keys())[1]
        new_func_schema = {
            "type": "function",
            "function": {
                "name": new_func_name,
                "parameters": {
                    "properties": {},
                    "type": "object",
                },
                "description": "A duplicate sync function for testing.",
            },
        }
        self.assertListEqual(
            [
                self.sync_func_schema,
                new_func_schema,
            ],
            self.toolkit.get_json_schemas(),
        )
        self.assertTrue(new_func_name.startswith("sync_func_"))
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name=new_func_name,
                input={},
            ),
        )
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [
                    TextBlock(
                        type="text",
                        text="test",
                    ),
                ],
            )
        res = await self.toolkit.call_tool_function(tool_call)
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [
                    TextBlock(
                        type="text",
                        text="arg1: 55, arg2: None",
                    ),
                ],
            )

        # Try to add the same function with override strategy
        self.toolkit.register_tool_function(
            test.sync_func,
            namesake_strategy="override",
        )
        self.assertListEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "sync_func",
                        "parameters": {
                            "properties": {},
                            "type": "object",
                        },
                        "description": "A duplicate sync function "
                        "for testing.",
                    },
                },
                new_func_schema,
            ],
            self.toolkit.get_json_schemas(),
        )
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="sync_func",
                input={},
            ),
        )
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [
                    TextBlock(
                        type="text",
                        text="test",
                    ),
                ],
            )

    async def test_basic_functionalities(self) -> None:
        """Test sync function:
        1. register tool function
        2. set/cancel extended model
        3. get JSON schemas
        4. call tool function
        """
        self.toolkit.register_tool_function(
            tool_func=sync_func,
            preset_kwargs={"arg1": 55},
        )
        sync_func_schema = deepcopy(self.sync_func_schema)
        sync_func_schema["function"]["parameters"]["properties"].pop("arg1")
        sync_func_schema["function"]["parameters"].pop("required")
        self.assertListEqual(
            [sync_func_schema],
            self.toolkit.get_json_schemas(),
        )

        # Test extended model
        self.toolkit.set_extended_model(
            "sync_func",
            StructuredModel,
        )
        self.assertListEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "sync_func",
                        "parameters": {
                            "properties": {
                                "arg2": {
                                    "anyOf": [
                                        {
                                            "items": {
                                                "anyOf": [
                                                    {
                                                        "type": "string",
                                                    },
                                                    {
                                                        "type": "integer",
                                                    },
                                                ],
                                            },
                                            "type": "array",
                                        },
                                        {
                                            "type": "null",
                                        },
                                    ],
                                    "default": None,
                                    "description": "Test argument 2.",
                                },
                                "arg3": {
                                    "description": "Test argument 3.",
                                    "type": "integer",
                                },
                            },
                            "type": "object",
                            "required": [
                                "arg3",
                            ],
                        },
                        "description": "A sync function for testing.\n\n"
                        "Long description.",
                    },
                },
            ],
            self.toolkit.get_json_schemas(),
        )

        self.toolkit.set_extended_model("sync_func", None)
        self.assertListEqual(
            [sync_func_schema],
            self.toolkit.get_json_schemas(),
        )

        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="sync_func",
                input={"arg2": [1, 2, 3]},
            ),
        )
        async for chunk in res:
            self.assertEqual(
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            type="text",
                            text="arg1: 55, arg2: [1, 2, 3]",
                        ),
                    ],
                ),
                chunk,
            )

    async def test_extended_model_reusing_same_base_model(self) -> None:
        """Test extended model reusing the same BaseModel from original
        function."""

        def func_with_nested_model(a: MyBaseModel2) -> ToolResponse:
            """Function with nested BaseModel parameter."""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"a: {a}",
                    ),
                ],
            )

        self.toolkit.register_tool_function(func_with_nested_model)

        # Set extended model that reuses the same MyBaseModel2
        self.toolkit.set_extended_model(
            "func_with_nested_model",
            ExtendedModelReusingBaseModel,
        )

        # Get and verify the schema - should not raise any conflicts
        schemas = self.toolkit.get_json_schemas()
        self.assertListEqual(
            schemas,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "func_with_nested_model",
                        "parameters": {
                            "$defs": {
                                "MyBaseModel1": {
                                    "description": "A base model for testing"
                                    " nested $defs merging.",
                                    "properties": {
                                        "c": {
                                            "description": "Field c",
                                            "title": "C",
                                            "type": "integer",
                                        },
                                    },
                                    "required": ["c"],
                                    "title": "MyBaseModel1",
                                    "type": "object",
                                },
                                "MyBaseModel2": {
                                    "description": "A base model that contains"
                                    " nested MyBaseModel1.",
                                    "properties": {
                                        "b": {
                                            "description": "List of "
                                            "MyBaseModel1",
                                            "items": {
                                                "$ref": "#/$defs/MyBaseModel1",
                                            },
                                            "title": "B",
                                            "type": "array",
                                        },
                                    },
                                    "required": ["b"],
                                    "title": "MyBaseModel2",
                                    "type": "object",
                                },
                            },
                            "properties": {
                                "a": {"$ref": "#/$defs/MyBaseModel2"},
                                "another_model": {
                                    "$ref": "#/$defs/MyBaseModel2",
                                    "description": "Reusing MyBaseModel2",
                                },
                                "extra_field": {
                                    "description": "Extra field",
                                    "type": "string",
                                },
                            },
                            "required": ["a", "another_model", "extra_field"],
                            "type": "object",
                        },
                        "description": "Function with nested BaseModel "
                        "parameter.",
                    },
                },
            ],
        )

    async def test_detailed_arguments(self) -> None:
        """Verify the arguments in `register_tool_function`."""

        def func(
            *args: Any,  # pylint: disable=unused-argument
            **kwargs: Any,
        ) -> ToolResponse:
            """A test function.

            Note this function is test.
            """
            return ToolResponse(content=[])

        # Test positional and keyword arguments
        self.toolkit.register_tool_function(
            func,
            include_var_positional=False,
            include_var_keyword=False,
        )

        self.assertListEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "func",
                        "parameters": {
                            "properties": {},
                            "type": "object",
                        },
                        "description": "A test function.\n\n"
                        "Note this function is test.",
                    },
                },
            ],
            self.toolkit.get_json_schemas(),
        )

        self.toolkit.remove_tool_function("func")

        # Test func_description
        self.toolkit.register_tool_function(func, func_description="你好")
        self.assertListEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "func",
                        "parameters": {
                            "properties": {},
                            "type": "object",
                        },
                        "description": "你好",
                    },
                },
            ],
            self.toolkit.get_json_schemas(),
        )
        self.toolkit.remove_tool_function("func")

        # Test long description
        self.toolkit.register_tool_function(
            func,
            include_long_description=False,
        )
        self.assertListEqual(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "func",
                        "parameters": {
                            "properties": {},
                            "type": "object",
                        },
                        "description": "A test function.",
                    },
                },
            ],
            self.toolkit.get_json_schemas(),
        )

    async def _verify_async_generator_wo_interruption(
        self,
        async_generator: AsyncGenerator[ToolResponse, None],
    ) -> None:
        """Verify async generator without interruption."""
        async for index, chunk in aenumerate(async_generator):
            if index == 0:
                assert chunk == response1
            elif index == 1:
                assert chunk == response2
            elif index == 2:
                assert chunk == response3

    async def test_async_func(self) -> None:
        """Test asynchronous tool function"""
        self.toolkit.register_tool_function(async_func)
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_func",
                input={"raise_cancel": False},
            ),
        )
        async for chunk in res:
            self.assertEqual(
                response1,
                chunk,
            )

        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_func",
                input={"raise_cancel": True},
            ),
        )
        async for chunk in res:
            self.assertEqual(
                ToolResponse(
                    id=chunk.id,
                    content=[
                        TextBlock(
                            type="text",
                            text="<system-info>"
                            "The tool call has been interrupted by the "
                            "user.</system-info>",
                        ),
                    ],
                    is_last=True,
                    stream=True,
                    is_interrupted=True,
                ),
                chunk,
            )

    async def test_register_async_generator_func(self) -> None:
        """Test asynchronous generator function"""
        # Without interruption
        self.toolkit.register_tool_function(async_generator_func)
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_generator_func",
                input={"raise_cancel": False},
            ),
        )
        await self._verify_async_generator_wo_interruption(res)

        # With interruption
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_generator_func",
                input={"raise_cancel": True},
            ),
        )
        async for index, chunk in aenumerate(res):
            if index == 0:
                self.assertEqual(response1, chunk)
            elif index == 1:
                self.assertEqual(response2, chunk)
            elif index == 2:
                self.assertEqual(
                    ToolResponse(
                        id=chunk.id,
                        content=[
                            TextBlock(
                                type="text",
                                text="12",
                            ),
                            TextBlock(
                                type="text",
                                text="<system-info>The tool call has been "
                                "interrupted by the user.</system-info>",
                            ),
                        ],
                        stream=True,
                        is_last=True,
                        is_interrupted=True,
                    ),
                    chunk,
                )

    async def test_register_async_func_return_async_generator(self) -> None:
        """Test async function that returns async generator"""
        # Without interruption
        self.toolkit.register_tool_function(async_func_return_async_generator)
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_func_return_async_generator",
                input={"raise_cancel": False},
            ),
        )
        await self._verify_async_generator_wo_interruption(res)

        # With interruption
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_func_return_async_generator",
                input={"raise_cancel": True},
            ),
        )
        async for index, chunk in aenumerate(res):
            if index == 0:
                self.assertEqual(response1, chunk)
            elif index == 1:
                self.assertEqual(response2, chunk)
            elif index == 2:
                self.assertEqual(
                    ToolResponse(
                        id=chunk.id,
                        content=[
                            TextBlock(
                                type="text",
                                text="12",
                            ),
                            TextBlock(
                                type="text",
                                text="<system-info>The tool call has been "
                                "interrupted by the user.</system-info>",
                            ),
                        ],
                        stream=True,
                        is_last=True,
                        is_interrupted=True,
                    ),
                    chunk,
                )

    async def test_register_async_func_return_sync_generator(self) -> None:
        """Test async function that returns sync generator"""
        self.toolkit.register_tool_function(async_func_return_sync_generator)
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="async_func_return_sync_generator",
                input={},
            ),
        )
        await self._verify_async_generator_wo_interruption(res)

    async def test_register_sync_generator_func(self) -> None:
        """Text sync generator function"""
        self.toolkit.register_tool_function(sync_generator_func)
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="sync_generator_func",
                input={},
            ),
        )
        await self._verify_async_generator_wo_interruption(res)

    async def test_create_tool_group(self) -> None:
        """Test tool group functionalities."""

        with self.assertRaises(ValueError):
            self.toolkit.register_tool_function(
                sync_func,
                group_name="my_group",
            )

        self.toolkit.create_tool_group(
            "my_group",
            "Browser use related tools.",
            active=False,
        )

        self.toolkit.register_tool_function(
            sync_func,
            group_name="my_group",
        )

        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [],
        )

        # Active the tool group
        self.toolkit.update_tool_groups(["my_group"], True)
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [self.sync_func_schema],
        )

        # Deactivate the tool group
        self.toolkit.update_tool_groups(["my_group"], False)
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [],
        )

        # Unregister the tool group
        self.toolkit.remove_tool_groups(["my_group"])
        self.assertDictEqual(
            self.toolkit.tools,
            {},
        )

    async def test_postprocess_func(self) -> None:
        """Test postprocess function."""
        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="123",
            name="sync_func",
            input={"arg1": 10, "arg2": ["test"]},
        )

        def postprocess_func(
            tool_use: ToolUseBlock,
            tool_response: ToolResponse,
        ) -> ToolResponse | None:
            """Postprocess function to modify tool response."""

            self.assertEqual(tool_use, tool_use_block)

            if tool_response.content:
                tool_response.content.append(
                    TextBlock(type="text", text="Processed"),
                )
            return tool_response

        self.toolkit.register_tool_function(
            sync_func,
            postprocess_func=postprocess_func,
        )

        res = await self.toolkit.call_tool_function(tool_use_block)

        async for chunk in res:
            self.assertEqual(
                chunk.content,
                [
                    TextBlock(type="text", text="arg1: 10, arg2: ['test']"),
                    TextBlock(type="text", text="Processed"),
                ],
            )

    async def test_async_postprocess_func(self) -> None:
        """Test async postprocess function."""
        tool_use_block = ToolUseBlock(
            type="tool_use",
            id="123",
            name="sync_func",
            input={"arg1": 10, "arg2": ["test"]},
        )

        async def async_postprocess_func(
            tool_use: ToolUseBlock,
            tool_response: ToolResponse,
        ) -> ToolResponse | None:
            """Postprocess function to modify tool response."""

            self.assertEqual(tool_use, tool_use_block)

            if tool_response.content:
                tool_response.content.append(
                    TextBlock(type="text", text="Processed"),
                )
            return tool_response

        self.toolkit.register_tool_function(
            sync_func,
            postprocess_func=async_postprocess_func,
        )

        res = await self.toolkit.call_tool_function(tool_use_block)

        async for chunk in res:
            self.assertEqual(
                chunk.content,
                [
                    TextBlock(type="text", text="arg1: 10, arg2: ['test']"),
                    TextBlock(type="text", text="Processed"),
                ],
            )

    async def test_partial_function(self) -> None:
        """Test the partial function registration."""

        def example_func(
            a: int,
            b: str,
            c: list[str],
            d: str = "abc",
        ) -> ToolResponse:
            """Example function for partial testing"""
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Received: a={a}, b={b}, c={c}, d={d}",
                    ),
                ],
            )

        partial_func = partial(example_func, 1, c=[1, 2, 3])

        self.toolkit.register_tool_function(partial_func)

        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                {
                    "type": "function",
                    "function": {
                        "name": "example_func",
                        "parameters": {
                            "properties": {
                                "b": {
                                    "type": "string",
                                },
                                "d": {
                                    "default": "abc",
                                    "type": "string",
                                },
                            },
                            "required": [
                                "b",
                            ],
                            "type": "object",
                        },
                        "description": "Example function for partial testing",
                    },
                },
            ],
        )

        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="example_func",
                input={"b": "test", "d": "xyz"},
            ),
        )

        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "Received: a=1, b=test, c=[1, 2, 3], d=xyz",
            )

    async def test_meta_tool(self) -> None:
        """Test the meta tool."""
        self.toolkit.register_tool_function(
            self.toolkit.reset_equipped_tools,
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                {
                    "type": "function",
                    "function": {
                        "name": "reset_equipped_tools",
                        "parameters": {
                            "properties": {},
                            "type": "object",
                        },
                        "description": (
                            "Choose appropriate tools to equip yourself "
                            "with, so that you can\n\n"
                            "finish your task. Each argument in this function "
                            "represents a group\n"
                            "of related tools, and the value indicates "
                            "whether to activate the\n"
                            "group or not. Besides, the tool response of "
                            "this function will\n"
                            "contain the precaution notes for using them, "
                            "which you\n"
                            "**MUST pay attention to and follow**. You can "
                            "also reuse this function\n"
                            "to check the notes of the tool groups.\n\n"
                            "Note this function will `reset` the tools, so "
                            "that the original tools\n"
                            "will be removed first."
                        ),
                    },
                },
            ],
        )
        self.toolkit.create_tool_group(
            "browser_use",
            "The browser-use related tools.",
            notes="""## About Browser-Use Tools
1. You must xxx
2. First click xxx
""",
        )
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                {
                    "type": "function",
                    "function": {
                        "name": "reset_equipped_tools",
                        "parameters": {
                            "properties": {
                                "browser_use": {
                                    "default": False,
                                    "description": "The browser-use related "
                                    "tools.",
                                    "type": "boolean",
                                },
                            },
                            "type": "object",
                        },
                        "description": (
                            "Choose appropriate tools to equip yourself "
                            "with, so that you can\n\n"
                            "finish your task. Each argument in this function "
                            "represents a group\n"
                            "of related tools, and the value indicates "
                            "whether to activate the\n"
                            "group or not. Besides, the tool response of "
                            "this function will\n"
                            "contain the precaution notes for using them, "
                            "which you\n"
                            "**MUST pay attention to and follow**. You can "
                            "also reuse this function\n"
                            "to check the notes of the tool groups.\n\n"
                            "Note this function will `reset` the tools, so "
                            "that the original tools\n"
                            "will be removed first."
                        ),
                    },
                },
            ],
        )
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="reset_equipped_tools",
                input={"browser_use": True},
            ),
        )

        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "Active tool groups successfully: ['browser_use']. "
                "You MUST follow these notes to use the tools:\n"
                "<notes>## About browser_use Tools\n"
                "## About Browser-Use Tools\n"
                "1. You must xxx\n"
                "2. First click xxx\n"
                "</notes>",
            )

    async def asyncTearDown(self) -> None:
        """Clean up after each test."""
        self.toolkit = None
