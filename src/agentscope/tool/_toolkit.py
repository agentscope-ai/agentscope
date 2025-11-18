# -*- coding: utf-8 -*-
"""The toolkit class for tool calls in agentscope."""

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from typing import (
    AsyncGenerator,
    Literal,
    Any,
    Type,
    Generator,
    Callable,
    Awaitable,
)

import shortuuid
from pydantic import (
    BaseModel,
    Field,
    create_model,
)

from ._async_wrapper import (
    _async_generator_wrapper,
    _object_wrapper,
    _sync_generator_wrapper,
)
from ._registered_tool_function import RegisteredToolFunction
from ._response import ToolResponse
from .._utils._common import _parse_tool_function
from ..mcp import (
    MCPToolFunction,
    MCPClientBase,
    StatefulClientBase,
)
from ..message import (
    ToolUseBlock,
    TextBlock,
)
from ..module import StateModule
from ..types import (
    JSONSerializableObject,
    ToolFunction,
)
from ..tracing._trace import trace_toolkit
from .._logging import logger


@dataclass
class ToolGroup:
    """The tool group class"""

    name: str
    """The group name, which will be used in the reset function as the group
    identifier."""
    active: bool
    """If the tool group is active, meaning the tool functions in this group
    is included in the JSON schema"""
    description: str
    """The description of the tool group to tell the agent what the tool
    group is about."""
    notes: str | None = None
    """The using notes of the tool group, to remind the agent how to use"""


class Toolkit(StateModule):
    """The class that supports both function- and group-level tool management.

    Use the following methods to manage the tool functions:

    - `register_tool_function`
    - `remove_tool_function`

    For group-level management:

    - `create_tool_group`
    - `update_tool_groups`
    - `remove_tool_groups`

    MCP related methods:

    - `register_mcp_server`
    - `remove_mcp_servers`

    To run the tool functions or get the data from the activated tools:

    - `call_tool_function`
    - `get_json_schemas`
    - `get_tool_group_notes`
    """

    def __init__(self) -> None:
        """Initialize the toolkit."""
        super().__init__()

        self.tools: dict[str, RegisteredToolFunction] = {}
        self.groups: dict[str, ToolGroup] = {}

    def create_tool_group(
        self,
        group_name: str,
        description: str,
        active: bool = False,
        notes: str | None = None,
    ) -> None:
        """Create a tool group to organize tool functions

        Args:
            group_name (`str`):
                The name of the tool group.
            description (`str`):
                The description of the tool group.
            active (`bool`, defaults to `False`):
                If the group is active, meaning the tool functions in this
                group are included in the JSON schema.
            notes (`str | None`, optional):
                The notes used to remind the agent how to use the tool
                functions properly, which can be combined into the system
                prompt.
        """
        if group_name in self.groups or group_name == "basic":
            raise ValueError(
                f"Tool group '{group_name}' is already registered in the "
                "toolkit.",
            )

        self.groups[group_name] = ToolGroup(
            name=group_name,
            description=description,
            notes=notes,
            active=active,
        )

    def update_tool_groups(self, group_names: list[str], active: bool) -> None:
        """Update the activation status of the given tool groups.

        Args:
            group_names (`list[str]`):
                The list of tool group names to be updated.
            active (`bool`):
                If the tool groups should be activated or deactivated.
        """

        for group_name in group_names:
            if group_name == "basic":
                logger.warning(
                    "The 'basic' tool group is always active, skipping it.",
                )

            if group_name in self.groups:
                self.groups[group_name].active = active

    def remove_tool_groups(self, group_names: str | list[str]) -> None:
        """Remove tool functions from the toolkit by their group names.

        Args:
            group_names (`str | list[str]`):
                The group names to be removed from the toolkit.
        """
        if isinstance(group_names, str):
            group_names = [group_names]

        if not isinstance(group_names, list) or not all(
            isinstance(_, str) for _ in group_names
        ):
            raise TypeError(
                f"The group_names must be a list of strings, "
                f"but got {type(group_names)}.",
            )

        if "basic" in group_names:
            raise ValueError(
                "Cannot remove the default 'basic' tool group.",
            )

        for group_name in group_names:
            self.groups.pop(group_name, None)

        # Remove the tool functions in the given groups
        tool_names = deepcopy(list(self.tools.keys()))
        for tool_name in tool_names:
            if self.tools[tool_name].group in group_names:
                self.tools.pop(tool_name)

    # pylint: disable=too-many-branches, too-many-statements
    def register_tool_function(
        self,
        tool_func: ToolFunction,
        group_name: str | Literal["basic"] = "basic",
        preset_kwargs: dict[str, JSONSerializableObject] | None = None,
        func_description: str | None = None,
        json_schema: dict | None = None,
        include_long_description: bool = True,
        include_var_positional: bool = False,
        include_var_keyword: bool = False,
        postprocess_func: (
            Callable[
                [ToolUseBlock, ToolResponse],
                ToolResponse | None,
            ]
            | Callable[
                [ToolUseBlock, ToolResponse],
                Awaitable[ToolResponse | None],
            ]
        )
        | None = None,
        namesake_strategy: Literal[
            "override",
            "skip",
            "raise",
            "rename",
        ] = "raise",
    ) -> None:
        """Register a tool function to the toolkit.

        Args:
            tool_func (`ToolFunction`):
                The tool function, which can be async or sync, streaming or
                not-streaming, but the response must be a `ToolResponse`
                object.
            group_name (`str | Literal["basic"]`, defaults to `"basic"`):
                The belonging group of the tool function. Tools in "basic"
                group is always included in the JSON schema, while the others
                are only included when their group is active.
            preset_kwargs (`dict[str, JSONSerializableObject] | None`, \
            optional):
                Preset arguments by the user, which will not be included in
                the JSON schema, nor exposed to the agent.
            func_description (`str | None`, optional):
                The function description. If not provided, the description
                will be extracted from the docstring automatically.
            json_schema (`dict | None`, optional):
                Manually provided JSON schema for the tool function, which
                should be `{"type": "function", "function": {"name":
                "function_name": "xx", "description": "xx",
                "parameters": {...}}}`
            include_long_description (`bool`, defaults to `True`):
                When extracting function description from the docstring, if
                the long description will be included.
            include_var_positional (`bool`, defaults to `False`):
                Whether to include the variable positional arguments (`*args`)
                in the function schema.
            include_var_keyword (`bool`, defaults to `False`):
                Whether to include the variable keyword arguments (`**kwargs`)
                in the function schema.
            postprocess_func (`(Callable[[ToolUseBlock, ToolResponse], \
            ToolResponse | None] | Callable[[ToolUseBlock, ToolResponse], \
            Awaitable[ToolResponse | None]]) | None`, optional):
                A post-processing function that will be called after the tool
                function is executed, taking the tool call block and tool
                response as arguments. The function can be either sync or
                async. If it returns `None`, the tool result will be
                returned as is. If it returns a `ToolResponse`,
                the returned block will be used as the final tool result.
            namesake_strategy (`Literal['raise', 'override', 'skip', \
            'rename']`, defaults to `'raise'`):
                The strategy to handle the tool function name conflict:
                - 'raise': raise a ValueError (default behavior).
                - 'override': override the existing tool function with the new
                  one.
                - 'skip': skip the registration of the new tool function.
                - 'rename': rename the new tool function by appending a random
                  suffix to make it unique.
        """
        # Arguments checking
        if group_name not in self.groups and group_name != "basic":
            raise ValueError(
                f"Tool group '{group_name}' not found.",
            )

        # Check the manually provided JSON schema if provided
        if json_schema:
            assert (
                isinstance(json_schema, dict)
                and "type" in json_schema
                and json_schema["type"] == "function"
                and "function" in json_schema
                and isinstance(json_schema["function"], dict)
            ), "Invalid JSON schema for the tool function."

        # Handle MCP tool function and regular function respectively
        mcp_name = None
        if isinstance(tool_func, MCPToolFunction):
            func_name = tool_func.name
            original_func = tool_func.__call__
            json_schema = json_schema or tool_func.json_schema
            mcp_name = tool_func.mcp_name

        elif isinstance(tool_func, partial):
            # partial function
            kwargs = tool_func.keywords
            # Turn args into keyword arguments
            if tool_func.args:
                param_names = list(
                    inspect.signature(tool_func.func).parameters.keys(),
                )
                for i, arg in enumerate(tool_func.args):
                    if i < len(param_names):
                        kwargs[param_names[i]] = arg

            preset_kwargs = {
                **kwargs,
                **(preset_kwargs or {}),
            }

            func_name = tool_func.func.__name__
            original_func = tool_func.func
            json_schema = json_schema or _parse_tool_function(
                tool_func.func,
                include_long_description=include_long_description,
                include_var_positional=include_var_positional,
                include_var_keyword=include_var_keyword,
            )

        else:
            # normal function
            func_name = tool_func.__name__
            original_func = tool_func
            json_schema = json_schema or _parse_tool_function(
                tool_func,
                include_long_description=include_long_description,
                include_var_positional=include_var_positional,
                include_var_keyword=include_var_keyword,
            )

        # Override the description if provided
        if func_description:
            json_schema["function"]["description"] = func_description

        # Remove the preset kwargs from the JSON schema
        for arg_name in preset_kwargs or {}:
            if arg_name in json_schema["function"]["parameters"]["properties"]:
                json_schema["function"]["parameters"]["properties"].pop(
                    arg_name,
                )

        if "required" in json_schema["function"]["parameters"]:
            for arg_name in preset_kwargs or {}:
                if (
                    arg_name
                    in json_schema["function"]["parameters"]["required"]
                ):
                    json_schema["function"]["parameters"]["required"].remove(
                        arg_name,
                    )

            # Remove the required field if it is empty
            if len(json_schema["function"]["parameters"]["required"]) == 0:
                json_schema["function"]["parameters"].pop("required", None)

        func_obj = RegisteredToolFunction(
            name=func_name,
            group=group_name,
            source="function",
            original_func=original_func,
            json_schema=json_schema,
            preset_kwargs=preset_kwargs or {},
            extended_model=None,
            mcp_name=mcp_name,
            postprocess_func=postprocess_func,
        )

        if func_name in self.tools:
            if namesake_strategy == "raise":
                raise ValueError(
                    f"A function with name '{func_name}' is already "
                    f"registered in the toolkit.",
                )

            if namesake_strategy == "skip":
                logger.warning(
                    "A function with name '%s' is already "
                    "registered in the toolkit. Skipping registration.",
                    func_name,
                )

            elif namesake_strategy == "override":
                logger.warning(
                    "A function with name '%s' is already registered "
                    "in the toolkit. Overriding with the new function.",
                    func_name,
                )
                self.tools[func_name] = func_obj

            elif namesake_strategy == "rename":
                new_func_name = func_name
                for _ in range(100):
                    suffix = shortuuid.uuid()[:5]
                    new_func_name = f"{func_name}_{suffix}"
                    if new_func_name not in self.tools:
                        break

                # Raise error if failed to find a unique name
                if new_func_name in self.tools:
                    raise RuntimeError(
                        f"Failed to register tool function '{func_name}' with "
                        "a unique name after 100 attempts.",
                    )
                logger.warning(
                    "A function with name '%s' is already "
                    "registered in the toolkit. Renaming the new function to "
                    "'%s'.",
                    func_name,
                    new_func_name,
                )

                # Replace the function name with the new one
                func_obj.name = new_func_name
                func_obj.json_schema["function"]["name"] = new_func_name

                self.tools[new_func_name] = func_obj

            else:
                raise ValueError(
                    f"Invalid namesake_strategy: {namesake_strategy}. "
                    "Supported strategies are 'raise', 'override', 'skip', "
                    "and 'rename'.",
                )

        else:
            self.tools[func_name] = func_obj

    def remove_tool_function(
        self,
        tool_name: str,
        allow_not_exist: bool = True,
    ) -> None:
        """Remove tool function from the toolkit by its name.

        Args:
            tool_name (`str`):
                The name of the tool function to be removed.
            allow_not_exist (`bool`):
                Allow the tool function to not exist when removing.
        """

        if tool_name not in self.tools and not allow_not_exist:
            raise ValueError(
                f"Tool function '{tool_name}' does not exist in the "
                "toolkit.",
            )

        self.tools.pop(tool_name, None)

    def get_json_schemas(
        self,
    ) -> list[dict]:
        """Get the JSON schemas from the tool functions that belong to the
        active groups.

        .. note:: The preset keyword arguments is removed from the JSON
         schema, and the extended model is applied if it is set.

        Example:
            .. code-block:: JSON
                :caption: Example of tool function JSON schemas

                [
                    {
                        "type": "function",
                        "function": {
                            "name": "google_search",
                            "description": "Search on Google.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query."
                                    }
                                },
                                "required": ["query"]
                            }
                        }
                    },
                    ...
                ]

        Returns:
            `list[dict]`:
                A list of function JSON schemas.
        """
        # If meta tool is set here, update its extended model here
        if "reset_equipped_tools" in self.tools:
            fields = {}
            for group_name, group in self.groups.items():
                if group_name == "basic":
                    continue
                fields[group_name] = (
                    bool,
                    Field(
                        default=False,
                        description=group.description,
                    ),
                )
            extended_model = create_model("_DynamicModel", **fields)
            self.set_extended_model(
                "reset_equipped_tools",
                extended_model,
            )

        return [
            tool.extended_json_schema
            for tool in self.tools.values()
            if tool.group == "basic" or self.groups[tool.group].active
        ]

    def set_extended_model(
        self,
        func_name: str,
        model: Type[BaseModel] | None,
    ) -> None:
        """Set the extended model for a tool function, so that the original
        JSON schema will be extended.

        Args:
            func_name (`str`):
                The name of the tool function.
            model (`Union[Type[BaseModel], None]`):
                The extended model to be set.
        """
        if model is not None and not issubclass(model, BaseModel):
            raise TypeError(
                "The extended model must be a child class of pydantic "
                f"BaseModel, but got {type(model)}.",
            )

        if func_name in self.tools:
            self.tools[func_name].extended_model = model

        else:
            raise ValueError(
                f"Tool function '{func_name}' not found in the toolkit.",
            )

    async def remove_mcp_clients(
        self,
        client_names: list[str],
    ) -> None:
        """Remove tool functions from the MCP clients by their names.

        Args:
            client_names (`list[str]`):
                The names of the MCP client, which used to initialize the
                client instance.
        """
        if isinstance(client_names, str):
            client_names = [client_names]

        if isinstance(client_names, list) and not all(
            isinstance(_, str) for _ in client_names
        ):
            raise TypeError(
                f"The client_names must be a list of strings, "
                f"but got {type(client_names)}.",
            )

        to_removed = []
        func_names = deepcopy(list(self.tools.keys()))
        for func_name in func_names:
            if self.tools[func_name].mcp_name in client_names:
                self.tools.pop(func_name)
                to_removed.append(func_name)

        logger.info(
            "Removed %d tool functions from %d MCP: %s",
            len(to_removed),
            len(client_names),
            ", ".join(to_removed),
        )

    @trace_toolkit
    async def call_tool_function(
        self,
        tool_call: ToolUseBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        """Execute the tool function by the `ToolUseBlock` and return the
        tool response chunk in unified streaming mode, i.e. an async
        generator of `ToolResponse` objects.

        .. note:: The tool response chunk is **accumulated**.

        Args:
            tool_call (`ToolUseBlock`):
                A tool call block.

        Yields:
            `ToolResponse`:
                The tool response chunk, in accumulative manner.
        """

        # Check
        if tool_call["name"] not in self.tools:
            return _object_wrapper(
                ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text="FunctionNotFoundError: Cannot find the "
                            f"function named {tool_call['name']}",
                        ),
                    ],
                ),
                None,
            )

        # Prepare function and keyword arguments
        tool_func = self.tools[tool_call["name"]]
        kwargs = {
            **tool_func.preset_kwargs,
            **(tool_call.get("input", {}) or {}),
        }

        # Prepare postprocess function
        if tool_func.postprocess_func:
            # Type: partial wraps the postprocess_func with tool_call bound,
            # reducing it from (ToolUseBlock, ToolResponse) to (ToolResponse)
            partial_postprocess_func: (
                Callable[[ToolResponse], ToolResponse | None]
                | Callable[[ToolResponse], Awaitable[ToolResponse | None]]
            ) | None = partial(
                tool_func.postprocess_func,
                tool_call,
            )
        else:
            partial_postprocess_func = None

        # Async function
        try:
            if inspect.iscoroutinefunction(tool_func.original_func):
                try:
                    res = await tool_func.original_func(**kwargs)
                except asyncio.CancelledError:
                    res = ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text="<system-info>"
                                "The tool call has been interrupted "
                                "by the user."
                                "</system-info>",
                            ),
                        ],
                        stream=True,
                        is_last=True,
                        is_interrupted=True,
                    )

            else:
                # When `tool_func.original_func` is Async generator function or
                # Sync function
                res = tool_func.original_func(**kwargs)

        except Exception as e:
            res = ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: {e}",
                    ),
                ],
            )

        # Handle different return type

        # If return an async generator
        if isinstance(res, AsyncGenerator):
            return _async_generator_wrapper(res, partial_postprocess_func)

        # If return a sync generator
        if isinstance(res, Generator):
            return _sync_generator_wrapper(res, partial_postprocess_func)

        if isinstance(res, ToolResponse):
            return _object_wrapper(res, partial_postprocess_func)

        raise TypeError(
            "The tool function must return a ToolResponse object, or an "
            "AsyncGenerator/Generator of ToolResponse objects, "
            f"but got {type(res)}.",
        )

    async def register_mcp_client(
        self,
        mcp_client: MCPClientBase,
        group_name: str = "basic",
        enable_funcs: list[str] | None = None,
        disable_funcs: list[str] | None = None,
        preset_kwargs_mapping: dict[str, dict[str, Any]] | None = None,
        postprocess_func: (
            Callable[
                [ToolUseBlock, ToolResponse],
                ToolResponse | None,
            ]
            | Callable[
                [ToolUseBlock, ToolResponse],
                Awaitable[ToolResponse | None],
            ]
        )
        | None = None,
        namesake_strategy: Literal[
            "override",
            "skip",
            "raise",
            "rename",
        ] = "raise",
    ) -> None:
        """Register tool functions from an MCP client.

        Args:
            mcp_client (`MCPClientBase`):
                The MCP client instance to connect to the MCP server.
            group_name (`str`, defaults to `"basic"`):
                The group name that the tool functions will be added to.
            enable_funcs (`list[str] | None`, optional):
                The functions to be added into the toolkit. If `None`, all
                tool functions within the MCP servers will be added.
            disable_funcs (`list[str] | None`, optional):
                The functions that will be filtered out. If `None`, no
                tool functions will be filtered out.
            preset_kwargs_mapping: (`Optional[dict[str, dict[str, Any]]]`, \
            defaults to `None`):
                The preset keyword arguments mapping, whose keys are the tool
                function names and values are the preset keyword arguments.
            postprocess_func (`(Callable[[ToolUseBlock, ToolResponse], \
            ToolResponse | None] | Callable[[ToolUseBlock, ToolResponse], \
            Awaitable[ToolResponse | None]]) | None`, optional):
                A post-processing function that will be called after the tool
                function is executed, taking the tool call block and tool
                response as arguments. The function can be either sync or
                async. If it returns `None`, the tool result will be
                returned as is. If it returns a `ToolResponse`,
                the returned block will be used as the final tool result.
            namesake_strategy (`Literal['raise', 'override', 'skip', \
            'rename']`, defaults to `'raise'`):
                The strategy to handle the tool function name conflict:
                - 'raise': raise a ValueError (default behavior).
                - 'override': override the existing tool function with the new
                  one.
                - 'skip': skip the registration of the new tool function.
                - 'rename': rename the new tool function by appending a random
                  suffix to make it unique.
        """
        if (
            isinstance(mcp_client, StatefulClientBase)
            and not mcp_client.is_connected
        ):
            raise RuntimeError(
                "The MCP client is not connected to the server. Use the "
                "`connect()` method first.",
            )

        # Check arguments for enable_funcs and disabled_funcs
        if enable_funcs is not None and disable_funcs is not None:
            assert isinstance(enable_funcs, list) and all(
                isinstance(_, str) for _ in enable_funcs
            ), (
                "Enable functions should be a list of strings, but got "
                f"{enable_funcs}."
            )

            assert isinstance(disable_funcs, list) and all(
                isinstance(_, str) for _ in disable_funcs
            ), (
                "Disable functions should be a list of strings, but got "
                f"{disable_funcs}."
            )
            intersection = set(enable_funcs).intersection(
                set(disable_funcs),
            )
            assert len(intersection) == 0, (
                f"The functions in enable_funcs and disable_funcs "
                f"should not overlap, but got {intersection}."
            )

        if not (
            preset_kwargs_mapping is None
            or isinstance(preset_kwargs_mapping, dict)
        ):
            raise TypeError(
                f"The preset_kwargs_mapping must be a dictionary or None, "
                f"but got {type(preset_kwargs_mapping)}.",
            )

        tool_names = []
        for mcp_tool in await mcp_client.list_tools():
            # Skip the functions that are not in the enable_funcs if
            # enable_funcs is not None
            if enable_funcs is not None and mcp_tool.name not in enable_funcs:
                continue

            # Skip the disabled functions
            if disable_funcs is not None and mcp_tool.name in disable_funcs:
                continue

            tool_names.append(mcp_tool.name)

            # Obtain callable function object
            func_obj = await mcp_client.get_callable_function(
                func_name=mcp_tool.name,
                wrap_tool_result=True,
            )

            # Prepare preset kwargs
            preset_kwargs = None
            if preset_kwargs_mapping is not None:
                preset_kwargs = preset_kwargs_mapping.get(mcp_tool.name, {})

            self.register_tool_function(
                tool_func=func_obj,
                group_name=group_name,
                preset_kwargs=preset_kwargs,
                postprocess_func=postprocess_func,
                namesake_strategy=namesake_strategy,
            )

        logger.info(
            "Registered %d tool functions from MCP: %s.",
            len(tool_names),
            ", ".join(tool_names),
        )

    def state_dict(self) -> dict[str, Any]:
        """Get the state dictionary of the toolkit.

        Returns:
            `dict[str, Any]`:
                A dictionary containing the active tool group names.
        """
        return {
            "active_groups": [
                name for name, group in self.groups.items() if group.active
            ],
        }

    def load_state_dict(
        self,
        state_dict: dict[str, Any],
        strict: bool = True,
    ) -> None:
        """Load the state dictionary into the toolkit.

        Args:
            state_dict (`dict`):
                The state dictionary to load, which should have "active_groups"
                key and its value must be a list of group names.
            strict (`bool`, defaults to `True`):
                If `True`, raises an error if any key in the module is not
                found in the state_dict. If `False`, skips missing keys.
        """
        if (
            not isinstance(state_dict, dict)
            or "active_groups" not in state_dict
            or not isinstance(state_dict["active_groups"], list)
        ):
            raise ValueError(
                "The state_dict for toolkit must be a dictionary with "
                "active_groups key and its value must be a list, "
                f"but got {type(state_dict)}.",
            )

        if strict and list(state_dict.keys()) != ["active_groups"]:
            raise ValueError(
                "Get additional keys in the state_dict: "
                f'{list(state_dict.keys())}, but only "active_groups" '
                "is expected.",
            )

        for group_name, group in self.groups.items():
            if group_name in state_dict["active_groups"]:
                group.active = True
            else:
                group.active = False

    def get_activated_notes(self) -> str:
        """Get the notes from the active tool groups, which can be used to
        construct the system prompt for the agent.

        Returns:
            `str`:
                The combined notes from the active tool groups.
        """
        collected_notes = []
        for group_name, group in self.groups.items():
            if group.active and group.notes:
                collected_notes.append(
                    "\n".join(
                        [f"## About {group_name} Tools", group.notes],
                    ),
                )
        return "\n".join(collected_notes)

    def reset_equipped_tools(self, **kwargs: Any) -> ToolResponse:
        """Choose appropriate tools to equip yourself with, so that you can
        finish your task. Each argument in this function represents a group
        of related tools, and the value indicates whether to activate the
        group or not. Besides, the tool response of this function will
        contain the precaution notes for using them, which you
        **MUST pay attention to and follow**. You can also reuse this function
        to check the notes of the tool groups.

        Note this function will `reset` the tools, so that the original tools
        will be removed first.
        """

        to_activate = []
        for key, value in kwargs.items():
            if not isinstance(value, bool):
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=f"Invalid arguments: the argument {key} "
                            f"should be a bool value, but got {type(value)}.",
                        ),
                    ],
                )

            if value:
                to_activate.append(key)

        self.update_tool_groups(to_activate, active=True)

        notes = self.get_activated_notes()

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Active tool groups successfully: {to_activate}. "
                    "You MUST follow these notes to use the tools:\n"
                    f"<notes>{notes}</notes>",
                ),
            ],
        )

    def clear(self) -> None:
        """Clear the toolkit, removing all tool functions and groups."""
        self.tools.clear()
        self.groups.clear()

    def _validate_tool_function(self, func_name: str) -> None:
        """Check if the tool function already registered in the toolkit. If
        so, raise a ValueError."""
        if func_name in self.tools:
            raise ValueError(
                f"A function with name '{func_name}' is already registered "
                "in the toolkit.",
            )
