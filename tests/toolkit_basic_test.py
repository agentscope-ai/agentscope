# -*- coding: utf-8 -*-
# pylint: disable=too-many-lines
# mypy: disable-error-code="index"
"""Test toolkit module in agentscope."""
import asyncio
import os
import tempfile
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


class ToolkitBasicTest(IsolatedAsyncioTestCase):
    """Basic unittests for the toolkit module."""

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
                "description": "A sync function for testing.\n"
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
                        "description": "A sync function for testing.\n"
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
                        "description": "A test function.\n"
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

    async def test_register_agent_skill_stores_absolute_path(self) -> None:
        """Verify agent skill directory is stored as absolute path."""
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            with open(
                os.path.join(tmp_dir, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(
                    "---\n"
                    "name: test-skill\n"
                    "description: test description\n"
                    "---\n",
                )

            relative_skill_dir = os.path.relpath(tmp_dir)
            self.toolkit.register_agent_skill(relative_skill_dir)
            self.assertEqual(
                self.toolkit.skills["test-skill"]["dir"],
                os.path.abspath(relative_skill_dir),
            )
            self.assertIn("read_skill_md", self.toolkit.tools)

    async def test_builtin_read_skill_md_tool(self) -> None:
        """Verify built-in SKILL.md reader can read a registered skill."""
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            with open(
                os.path.join(tmp_dir, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(
                    "---\n"
                    "name: test-skill\n"
                    "description: test description\n"
                    "---\n"
                    "This is the detailed skill instruction.\n",
                )

            self.toolkit.register_agent_skill(tmp_dir)
            response = await self.toolkit.call_tool_function(
                ToolUseBlock(
                    type="tool_use",
                    id="123",
                    name="read_skill_md",
                    input={"skill_name": "test-skill"},
                ),
            )

            chunks = [chunk async for chunk in response]
            self.assertEqual(len(chunks), 1)
            self.assertEqual(len(chunks[0].content), 1)
            self.assertIn(
                "This is the detailed skill instruction.",
                chunks[0].content[0]["text"],
            )

    async def test_builtin_read_skill_md_tool_unknown_skill(self) -> None:
        """Verify built-in SKILL.md reader handles unknown skill names."""
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            with open(
                os.path.join(tmp_dir, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(
                    "---\n"
                    "name: known-skill\n"
                    "description: test description\n"
                    "---\n",
                )

            self.toolkit.register_agent_skill(tmp_dir)
            response = await self.toolkit.call_tool_function(
                ToolUseBlock(
                    type="tool_use",
                    id="123",
                    name="read_skill_md",
                    input={"skill_name": "unknown-skill"},
                ),
            )

            chunks = [chunk async for chunk in response]
            self.assertEqual(len(chunks), 1)
            self.assertEqual(
                chunks[0].content[0]["text"],
                "Error: Skill 'unknown-skill' is not registered.",
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

        # Activate the tool group
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

    async def test_func_name_parameter(self) -> None:
        """Test func_name parameter for custom tool renaming."""
        # Test 1: Regular function with func_name
        self.toolkit.register_tool_function(
            sync_func,
            func_name="custom_sync_func",
        )
        self.assertIn("custom_sync_func", self.toolkit.tools)
        self.assertNotIn("sync_func", self.toolkit.tools)

        # Verify the JSON schema uses the custom name
        schemas = self.toolkit.get_json_schemas()
        self.assertEqual(schemas[0]["function"]["name"], "custom_sync_func")

        # Verify original_name is set when func_name is provided
        tool_obj = self.toolkit.tools["custom_sync_func"]
        self.assertEqual(tool_obj.original_name, "sync_func")

        # Test 2: Regular function without func_name (backward compatibility)
        def another_func(x: int) -> ToolResponse:
            """Another test function."""
            return ToolResponse(content=[TextBlock(type="text", text=str(x))])

        self.toolkit.register_tool_function(another_func)
        self.assertIn("another_func", self.toolkit.tools)
        tool_obj = self.toolkit.tools["another_func"]
        self.assertIsNone(tool_obj.original_name)

        # Test 3: Partial function with func_name
        partial_func = partial(sync_func, arg1=10)
        self.toolkit.register_tool_function(
            partial_func,
            func_name="custom_partial_func",
        )
        self.assertIn("custom_partial_func", self.toolkit.tools)
        tool_obj = self.toolkit.tools["custom_partial_func"]
        self.assertEqual(tool_obj.original_name, "sync_func")

        # Test 4: func_name with namesake_strategy="rename"
        self.toolkit.register_tool_function(
            sync_func,
            func_name="custom_sync_func",  # Already exists
            namesake_strategy="rename",
        )
        # Should create a new name with random suffix
        renamed_tools = [
            name
            for name in self.toolkit.tools
            if name.startswith("custom_sync_func_")
        ]
        self.assertEqual(len(renamed_tools), 1)
        renamed_name = renamed_tools[0]
        tool_obj = self.toolkit.tools[renamed_name]
        # original_name should be "sync_func" (the true original function name)
        # because original_name records the actual function name, not the
        # func_name
        self.assertEqual(tool_obj.original_name, "sync_func")

        # Test 5: func_name with namesake_strategy="rename" but no func_name
        # (should use original function name as original_name)
        def test_func() -> ToolResponse:
            """Test function."""
            return ToolResponse(content=[TextBlock(type="text", text="test")])

        self.toolkit.register_tool_function(test_func)
        self.toolkit.register_tool_function(
            test_func,
            namesake_strategy="rename",
        )
        # Find the renamed tool
        renamed_test_tools = [
            name
            for name in self.toolkit.tools
            if name.startswith("test_func_")
        ]
        self.assertEqual(len(renamed_test_tools), 1)
        renamed_test_name = renamed_test_tools[0]
        tool_obj = self.toolkit.tools[renamed_test_name]
        # original_name should be "test_func" (the original function name)
        self.assertEqual(tool_obj.original_name, "test_func")

        # Test 6: Verify tool can be called with custom name
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="123",
                name="custom_sync_func",
                input={"arg1": 42},
            ),
        )
        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "arg1: 42, arg2: None",
            )

    async def asyncTearDown(self) -> None:
        """Clean up after each test."""
        self.toolkit = None
