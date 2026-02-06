# -*- coding: utf-8 -*-
# mypy: disable-error-code="index"
"""Test meta tool in toolkit module in agentscope."""
from unittest import IsolatedAsyncioTestCase

from agentscope.message import ToolUseBlock, TextBlock
from agentscope.tool import ToolResponse, Toolkit


def tool_function_1() -> ToolResponse:
    """Test tool function 1."""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="1",
            ),
        ],
    )


def tool_function_2() -> ToolResponse:
    """Test tool function 2."""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="2",
            ),
        ],
    )


class ToolkitMetaToolTest(IsolatedAsyncioTestCase):
    """Unittest for the toolkit module."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment before each test."""
        self.toolkit = Toolkit()

        self.function_1_schema = {
            "type": "function",
            "function": {
                "name": "tool_function_1",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
                "description": "Test tool function 1.",
            },
        }

        self.function_2_schema = {
            "type": "function",
            "function": {
                "name": "tool_function_2",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
                "description": "Test tool function 2.",
            },
        }

        self.meta_tool_schema = {
            "type": "function",
            "function": {
                "name": "reset_equipped_tools",
                "parameters": {
                    "properties": {},
                    "type": "object",
                },
                "description": """This function allows you to activate or \
deactivate tool groups
dynamically based on your current task requirements.
**Important: Each call sets the absolute final state of ALL tool
groups, not incremental changes**. Any group not explicitly set to True
will be deactivated, regardless of its previous state.

**Best practice**: Actively manage your tool groups——activate only
what you need for the current task, and promptly deactivate groups as
soon as they are no longer needed to conserve context space.

The function will return the usage instructions for the activated tool
groups, which you **MUST pay attention to and follow**. You can also
reuse this function to check the notes of the tool groups.""",
            },
        }

    async def test_meta_tool(self) -> None:
        """Test the meta tool."""
        self.toolkit.register_tool_function(
            self.toolkit.reset_equipped_tools,
        )

        # Test if the meta tool is registered correctly
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [self.meta_tool_schema],
        )

        # Test creating a tool group and using the meta tool
        self.toolkit.create_tool_group(
            "browser_use",
            "The browser-use related tools.",
            notes="""1. You must xxx
2. First click xxx
""",
        )
        self.toolkit.register_tool_function(
            tool_function_1,
            group_name="browser_use",
        )

        self.meta_tool_schema["function"]["parameters"]["properties"] = {
            "browser_use": {
                "type": "boolean",
                "description": "The browser-use related tools.",
                "default": False,
            },
        }

        # Test if the arguments are updated correctly
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                self.meta_tool_schema,
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

        # Test if the tool response is correct
        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "Now tool groups 'browser_use' are activated. "
                "You MUST follow these notes to use these tools:\n"
                "<notes>## About Tool Group 'browser_use'\n"
                "1. You must xxx\n"
                "2. First click xxx\n"
                "</notes>",
            )

        # Test if the tool group is activated correctly, i.e. the tool
        # function 1 is available
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                self.meta_tool_schema,
                {
                    "type": "function",
                    "function": {
                        "name": "tool_function_1",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                        "description": "Test tool function 1.",
                    },
                },
            ],
        )

        # Create another tool group and register tool function 2
        self.toolkit.create_tool_group(
            "file_use",
            "The file-use related tools.",
        )
        self.toolkit.register_tool_function(
            tool_function_2,
            group_name="file_use",
        )

        # Test if the meta tool schema is updated correctly
        self.meta_tool_schema["function"]["parameters"]["properties"] = {
            "browser_use": {
                "type": "boolean",
                "description": "The browser-use related tools.",
                "default": False,
            },
            "file_use": {
                "type": "boolean",
                "description": "The file-use related tools.",
                "default": False,
            },
        }

        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                self.meta_tool_schema,
                self.function_1_schema,
            ],
        )

        # Activate the file-use tool group only
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="124",
                name="reset_equipped_tools",
                input={"file_use": True},
            ),
        )

        # Test if the tool response is correct
        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "Now tool groups 'file_use' are activated.",
            )

        # Test if only tool function 2 is available now
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                self.meta_tool_schema,
                self.function_2_schema,
            ],
        )

        # Test if all tool groups are deactivated
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="125",
                name="reset_equipped_tools",
                input={},
            ),
        )

        # Test if the tool response is correct
        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "All tool groups are now deactivated currently.",
            )

        # Test if no tool function is available now
        self.assertListEqual(
            self.toolkit.get_json_schemas(),
            [
                self.meta_tool_schema,
            ],
        )

        # Test calling the inactive tool function
        res = await self.toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="126",
                name="tool_function_1",
                input={},
            ),
        )

        async for chunk in res:
            self.assertEqual(
                chunk.content[0]["text"],
                "FunctionInactiveError: The function 'tool_function_1' "
                "is in the inactive group 'browser_use'. "
                "Activate the tool group by calling 'reset_equipped_tools' "
                "first to use this tool.",
            )

    async def asyncTearDown(self) -> None:
        """Clean up after each test."""
        self.toolkit = None
