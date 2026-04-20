# -*- coding: utf-8 -*-
"""The toolkit class for tool calls in AgentScope."""
import asyncio
import inspect
import os
from collections import OrderedDict
from copy import deepcopy
from functools import wraps
from typing import (
    AsyncGenerator,
    Any,
    Type,
    Generator,
    Callable,
    Coroutine,
    TYPE_CHECKING,
)

import mcp
from pydantic import (
    BaseModel,
    Field,
    create_model,
)

from ._builtin import ResetTools
from ._base import ToolBase
from ._adapters import _FunctionTool
from ._response import ToolResponse, ToolChunk
from ._types import ToolGroup, AgentSkill, RegisteredTool
from .._utils._common import _json_loads_with_repair
from ..exception import DeveloperOrientedException
from ..mcp import (
    MCPClientBase,
    StatefulClientBase,
)
from ..message import (
    ToolCallBlock,
    TextBlock,
)
from .._logging import logger

if TYPE_CHECKING:
    from ..agent import AgentState
else:
    AgentState = "AgentState"


def _apply_middlewares(
    func: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> Callable[..., AsyncGenerator[ToolResponse, None]]:
    """Decorator that applies registered middlewares at runtime.

    This decorator reads the middleware list from the instance and constructs
    the middleware chain dynamically during each invocation.

    .. note:: Middlewares must be async generator functions that yield
     `ToolResponse` objects.
    """

    @wraps(func)
    async def wrapper(
        self: "Toolkit",
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        """Wrapper that applies middleware chain."""
        middlewares = getattr(self, "_middlewares", [])

        if not middlewares:
            # No middlewares, call the original function directly
            async for chunk in await func(self, tool_call):
                yield chunk
            return

        # Build the middleware chain from innermost to outermost
        async def base_handler(
            **kwargs: Any,
        ) -> AsyncGenerator[ToolResponse, None]:
            """Base handler that calls the original function."""
            return await func(self, **kwargs)

        # Wrap with each middleware in reverse order
        current_handler = base_handler
        for middleware in reversed(middlewares):

            def make_handler(mw: Callable, handler: Callable) -> Callable:
                """Create wrapped handler for middleware."""

                async def wrapped(
                    **kwargs: Any,
                ) -> AsyncGenerator[ToolResponse, None]:
                    """Handler that applies middleware."""
                    return mw(kwargs, handler)

                return wrapped

            current_handler = make_handler(middleware, current_handler)

        # Execute the middleware chain
        async for chunk in await current_handler(tool_call=tool_call):
            yield chunk

    return wrapper


# pylint: disable=line-too-long
DEFAULT_META_TOOL_RESPONSE_TEMPLATE = """{% if groups | length == 0 %}All tool groups are currently deactivated.{% else %}The currently activated tool group(s): {{ groups | map(attribute='name') | join(', ') }}.{% if groups | selectattr('instructions', 'ne', None) | list | length > 0 %}
<tool-instructions>
The tool instructions are a collection of suggestions, rules and notifications about how to use the tools in the activated groups.
{% for group in groups %}{% if group.instructions %}<group name="{{ group.name }}">{{ group.instructions }}</group>{% endif %}{% endfor %}
</tool-instructions>{% endif %}{% endif %}"""  # noqa: E501


class Toolkit:
    """Toolkit is the core module to register, manage and delete tool
    functions, MCP clients, Agent skills in AgentScope.

    About tool functions:

    - Register and parse JSON schemas from their docstrings automatically.
    - Group-wise tools management, and agentic tools activation/deactivation.
    - Extend the tool function JSON schema dynamically with Pydantic BaseModel.
    - Tool function execution with unified streaming interface.

    About MCP clients:

    - Register tool functions from MCP clients directly.
    - Client-level tool functions removal.

    About Agent skills:

    - Register agent skills from the given directory.
    - Provide prompt for the registered skills to the agent.
    """

    _DEFAULT_AGENT_SKILL_INSTRUCTION = (
        "# Agent Skills\n"
        "The agent skills are a collection of folds of instructions, scripts, "
        "and resources that you can load dynamically to improve performance "
        "on specialized tasks. Each agent skill has a `SKILL.md` file in its "
        "folder that describes how to use the skill. If you want to use a "
        "skill, you MUST read its `SKILL.md` file carefully."
    )

    _DEFAULT_AGENT_SKILL_TEMPLATE = """## {name}
{description}
Check "{dir}/SKILL.md" for how to use this skill"""

    def _get_meta_tool_schema(self) -> Type[BaseModel]:
        """Get the meta tool schema based on the current tool groups."""
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
        return create_model("_DynamicModel", **fields)

    def __init__(
        self,
        tools: list[ToolBase] | None = None,
        meta_tool_response_template: str = DEFAULT_META_TOOL_RESPONSE_TEMPLATE,
        mcp_tool_name: str = "mcp__{server}__{tool}",
        agent_skill_instruction: str | None = None,
        agent_skill_template: str | None = None,
    ) -> None:
        """Initialize the toolkit.

        Args:
            tools (`list[ToolProtocol] | None`, optional):
                The tool objects that implement the ToolProtocol interface.
            meta_tool_response_template (`str`, optional):
                The template for meta tool responses.
            mcp_tool_name (`str`, optional):
                The naming pattern for MCP tools.
            agent_skill_instruction (`str | None`, optional):
                The instruction for agent skills in the system prompt. If not
                provided, a default instruction will be used.
            agent_skill_template (`str | None`, optional):
                The template to present one agent skill in the system prompt,
                which should contain `{name}`, `{description}`, and `{dir}`
                placeholders. If not provided, a default template will be used.
        """
        super().__init__()

        self.tools: dict[str, RegisteredTool] = OrderedDict()
        if tools:
            for tool in tools:
                # TODO: handle the name conflict here
                self.tools[tool.name] = RegisteredTool(tool=tool)

        self.meta_tool_response_template = meta_tool_response_template

        self.mcp_tool_name = mcp_tool_name

        self.groups: dict[str, ToolGroup] = {}
        self.skills: dict[str, AgentSkill] = {}
        self._middlewares: list = []  # Store registered middlewares

        self._agent_skill_instruction = (
            agent_skill_instruction or self._DEFAULT_AGENT_SKILL_INSTRUCTION
        )
        self._agent_skill_template = (
            agent_skill_template or self._DEFAULT_AGENT_SKILL_TEMPLATE
        )

        self.builtin_meta_tool = RegisteredTool(
            tool=ResetTools(
                # An inference value for groups so that it can generate the
                # corresponding input schema.
                groups=self.groups,
                response_template=meta_tool_response_template,
            ),
        )

    def create_tool_group(
        self,
        group_name: str,
        description: str,
        instructions: str | None = None,
        tools: list[ToolBase] | None = None,
    ) -> None:
        """Create a tool group to organize tool functions

        Args:
            group_name (`str`):
                The name of the tool group.
            description (`str`):
                The description of the tool group.
            instructions (`str | None`, optional):
                The instructions about how to use the tool functions in this
                group.
            tools (`list[ToolProtocol] | None`, optional):
                The tool objects that implement the ToolProtocol interface to
                be added into this group. Note that these tools will also be
                added to the toolkit if they are not already in the toolkit.
        """
        if group_name in self.groups or group_name == "basic":
            raise ValueError(
                f"Tool group '{group_name}' is already registered in the "
                "toolkit.",
            )

        self.groups[group_name] = ToolGroup(
            name=group_name,
            description=description,
            instructions=instructions,
        )

        if isinstance(tools, list) and all(
            isinstance(_, ToolBase) for _ in tools
        ):
            for tool in tools:
                registered = RegisteredTool(
                    tool=tool,
                    group=group_name,
                )
                self.tools[tool.name] = registered

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

    def register_function(
        self,
        func: Callable,
        group: str = "basic",
        name: str | None = None,
        description: str | None = None,
        is_concurrency_safe: bool = True,
        is_read_only: bool = False,
    ) -> None:
        """Register a Python function as a tool in the toolkit.

        This method wraps a regular Python function into a FunctionTool and
        registers it to the toolkit.

        Args:
            func (`Callable`):
                A Python function to be registered as a tool.
            group (`str`, defaults to `"basic"`):
                The belonging group of the tool. Tools in "basic" group are
                always included in the JSON schema, while others are only
                included when their group is active.
            name (`str | None`, optional):
                Custom tool name. If None, uses the function name.
            description (`str | None`, optional):
                Custom tool description. If None, extracts from docstring.
            is_concurrency_safe (`bool`, defaults to `True`):
                Whether this tool is safe to call concurrently.
            is_read_only (`bool`, defaults to `False`):
                Whether this tool only reads data without side effects.
        """
        # Wrap the function into a FunctionTool
        tool = _FunctionTool(
            func,
            name=name,
            description=description,
            is_concurrency_safe=is_concurrency_safe,
            is_read_only=is_read_only,
        )

        # Check if the group exists
        groups = ["basic"] + list(self.groups.keys())
        if group not in groups:
            raise ValueError(
                f"Tool group '{group}' does not exist. Available groups: "
                f"{groups}. You can create a new group by calling "
                "`toolkit.create_tool_group()` method.",
            )

        # Register the tool
        registered = RegisteredTool(
            tool=tool,
            group=group,
        )
        self.tools[tool.name] = registered

    def unregister_tool(
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

    def get_function_schemas(
        self,
        groups: list[str] | None = None,
    ) -> list[dict]:
        """Get the function JSON schemas.

        .. note:: The preset keyword arguments is removed from the JSON
         schema, and the extended model is applied if it is set.

         Args:
             groups (`list[str] | None`, optional):
                A list of group names to filter the tool function. The "basic"
                group will always be included regardless of the filter. If not
                provided, only the "basic" group will be included.

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
        function_schemas = []

        for tool in self._get_available_tools(groups).values():
            function_schemas.append(tool.get_function_schema())

        return function_schemas

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
            if self.tools[func_name].tool.mcp_name in client_names:
                self.tools.pop(func_name)
                to_removed.append(func_name)

        logger.info(
            "Removed %d tool functions from %d MCP: %s",
            len(to_removed),
            len(client_names),
            ", ".join(to_removed),
        )

    # @trace_toolkit
    # @_apply_middlewares
    async def call_tool(
        self,
        tool_call: ToolCallBlock,
        state: AgentState,
    ) -> AsyncGenerator[ToolChunk | ToolResponse, None]:
        """Call the tool function, return the incremental tool result in
        a ToolChunk stream, and finally return the complete tool result in a
        ToolResponse object. **Note the accumulation process occurs within this
        function, so the tool functions only need to return/yield the
        ToolChunk objects in an incremental manner.**

        Args:
            tool_call (`ToolCallBlock`):
                A tool call block.
            state: AgentState:
                The current agent state, used to state injection.

        Yields:
            `ToolChunk | ToolResponse`:
                The incremental tool result in a ToolChunk stream, and finally
                the complete tool result in a ToolResponse object.
        """
        tool_response = ToolResponse(id=tool_call.id)

        # Check
        available_tools = self._get_available_tools(state.activated_groups)

        if tool_call.name not in available_tools:
            # Not activate
            if tool_call.name in self.tools:
                group_name = self.tools[tool_call.name].group
                chunk = ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "ToolGroupInactiveError: The tool "
                                f"'{tool_call.name}' in group '{group_name}' "
                                "is currently inactive. You should first "
                                "activate the group by calling the "
                                f"'{self.builtin_meta_tool.tool.name}' tool."
                            ),
                        ),
                    ],
                    state="error",
                )
                yield chunk
                yield tool_response.append_chunk(chunk)
                return

            # Not exist
            chunk = ToolChunk(
                content=[
                    TextBlock(
                        text=f"ToolNotFoundError: The tool named "
                        f"'{tool_call.name}' doesn't exist.",
                    ),
                ],
                state="error",
            )
            yield chunk
            yield tool_response.append_chunk(chunk)
            return

        # Obtain the tool function
        tool_func = available_tools[tool_call.name].tool

        # Prepare keyword arguments
        kwargs = _json_loads_with_repair(tool_call.input)

        # TODO: we should be build a mechanism to support state injection in
        # the future instead of hard coding here.
        if tool_call.name == self.builtin_meta_tool.tool.name:
            kwargs["state"] = state

        # Async function
        try:
            if inspect.iscoroutinefunction(tool_func.__call__):
                res = await tool_func(**kwargs)
            else:
                # When `tool_func.original_func` is Async generator function or
                # Sync function
                res = tool_func(**kwargs)

            if isinstance(res, ToolChunk):
                yield res
                tool_response.append_chunk(res)

            # If return an async generator
            elif isinstance(res, AsyncGenerator):
                async for chunk in res:
                    yield chunk
                    tool_response.append_chunk(chunk)

            # If return a sync generator
            elif isinstance(res, Generator):
                for chunk in res:
                    yield chunk
                    tool_response.append_chunk(chunk)

            else:
                raise DeveloperOrientedException(
                    "The tool function must return a ToolChunk object, or an "
                    "AsyncGenerator/Generator of ToolChunk objects, "
                    f"but got {type(res)}.",
                )

        except mcp.shared.exceptions.McpError as e:
            chunk = ToolChunk(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error occurred when calling MCP tool: {e}",
                    ),
                ],
                state="error",
            )
            yield chunk
            tool_response.append_chunk(chunk)

        except Exception as e:
            # Raise the developer-oriented exception
            if isinstance(e, DeveloperOrientedException):
                raise e from None

            # The exceptions should be handled by the agent
            chunk = ToolChunk(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: {e}",
                    ),
                ],
                state="error",
            )
            yield chunk
            tool_response.append_chunk(chunk)

        except asyncio.CancelledError:
            chunk = ToolChunk(
                content=[
                    TextBlock(
                        type="text",
                        text="<system-reminder>"
                        "The tool call has been interrupted "
                        "by the user."
                        "</system-reminder>",
                    ),
                ],
                state="interrupted",
            )
            yield chunk
            tool_response.append_chunk(chunk)

        finally:
            # Finally, yield the complete tool response
            yield tool_response

    async def register_mcp_client(
        self,
        mcp_client: MCPClientBase,
        group_name: str = "basic",
        enable_funcs: list[str] | None = None,
        disable_funcs: list[str] | None = None,
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
            tool_obj = await mcp_client.get_tool(
                name=mcp_tool.name,
            )

            # Register the tool function to the toolkit
            registered = RegisteredTool(
                tool=tool_obj,
                group=group_name,
            )

            self.tools[tool_obj.name] = registered

        logger.info(
            "Registered %d tool functions from MCP: %s.",
            len(tool_names),
            ", ".join(tool_names),
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

    def register_agent_skill(
        self,
        skill_dir: str,
    ) -> None:
        """Register agent skills from a given directory. This function will
        scan the directory, read metadata from the SKILL.md file, and add
        it to the skill related prompt. Developers can obtain the
        skills-related prompt by calling `toolkit.get_agent_skill_prompt()`.

        .. note:: This directory
         - Must include a SKILL.md file at the top level
         - The SKILL.md must have a YAML Front Matter including `name` and
            `description` fields
         - All files must specify a common root directory in their paths

        Args:
            skill_dir (`str`):
                The path to the skill directory.
        """
        import frontmatter

        # Check the skill directory
        if not os.path.isdir(skill_dir):
            raise ValueError(
                f"The skill directory '{skill_dir}' does not exist or is "
                "not a directory.",
            )

        # Check SKILL.md file
        path_skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(path_skill_md):
            raise ValueError(
                f"The skill directory '{skill_dir}' must include a "
                "SKILL.md file at the top level.",
            )

        # Check YAML Front Matter
        with open(path_skill_md, "r", encoding="utf-8") as f:
            post = frontmatter.load(f)

        name = post.get("name", None)
        description = post.get("description", None)

        if not name or not description:
            raise ValueError(
                f"The SKILL.md file in '{skill_dir}' must have a YAML Front "
                "Matter including `name` and `description` fields.",
            )

        name, description = str(name), str(description)
        if name in self.skills:
            raise ValueError(
                f"An agent skill with name '{name}' is already registered "
                "in the toolkit.",
            )

        self.skills[name] = AgentSkill(
            name=name,
            description=description,
            dir=skill_dir,
        )

        logger.info(
            "Registered agent skill '%s' from directory '%s'.",
            name,
            skill_dir,
        )

    def remove_agent_skill(self, name: str) -> None:
        """Remove an agent skill by its name.

        Args:
            name (`str`):
                The name of the agent skill to be removed.
        """
        if name in self.skills:
            self.skills.pop(name)
        else:
            logger.warning(
                "Agent skill '%s' not found in the toolkit, skipping removal.",
                name,
            )

    def get_agent_skill_prompt(self) -> str | None:
        """Get the prompt for all registered agent skills, which can be
        attached to the system prompt for the agent.

        The prompt is consisted of an overall instruction and the detailed
        descriptions of each skill, including its name, description, and
        directory.

        .. note:: If no skill is registered, None will be returned.

        Returns:
            `str | None`:
                The combined prompt for all registered agent skills, or None
                if no skill is registered.
        """
        if len(self.skills) == 0:
            return None

        skill_descriptions = [
            self._agent_skill_instruction,
        ] + [
            self._agent_skill_template.format(
                name=_["name"],
                description=_["description"],
                dir=_["dir"],
            )
            for _ in self.skills.values()
        ]
        return "\n".join(skill_descriptions)

    def register_middleware(
        self,
        middleware: Callable[
            ...,
            Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]]
            | AsyncGenerator[ToolResponse, None],
        ],
    ) -> None:
        """Register an onion-style middleware for the `call_tool_function`,
        which will wrap around the `call_tool_function` method, allowing
        pre-processing, post-processing, or even skipping the execution of
        the tool function.

        The middleware follows an onion model, where each registered
        middleware wraps around the previous one, forming layers. The
        middleware can:

        - Perform pre-processing before calling the tool function
        - Intercept and modify each ToolResponse chunk
        - Perform post-processing after the tool function completes
        - Skip the tool function execution entirely

        The middleware function should accept a ``kwargs`` dict as the first
        parameter and ``next_handler`` as the second parameter. The ``kwargs``
        dict currently contains:

        - ``tool_call`` (`ToolCallBlock`): The tool call request

        When calling ``next_handler``, pass ``**kwargs`` to unpack the dict.

        Example:
            .. code-block:: python

                # Simple direct consumption style (recommended)
                async def my_middleware(
                    kwargs: dict,
                    next_handler: Callable,
                ) -> AsyncGenerator[ToolResponse, None]:
                    # Access the tool call
                    tool_call = kwargs["tool_call"]

                    # Pre-processing
                    print(f"Calling tool: {tool_call['name']}")

                    # Call next handler with **kwargs
                    async for response in await next_handler(**kwargs):
                        # Intercept and modify response if needed
                        yield response

                    # Post-processing after tool completes
                    print(f"Tool {tool_call['name']} completed")

                toolkit.register_middleware(my_middleware)

            .. code-block:: python

                # Alternative: Skip execution based on conditions
                async def my_middleware(
                    kwargs: dict,
                    next_handler: Callable,
                ) -> AsyncGenerator[ToolResponse, None]:
                    tool_call = kwargs["tool_call"]

                    # Pre-processing
                    if not is_authorized(tool_call):
                        # Skip execution and return error directly
                        yield ToolResponse(
                            content=[
                                TextBlock(
                                    type="text",
                                    text="Unauthorized",
                                ),
                            ],
                        )
                        return

                    # Call next handler with **kwargs
                    async for response in await next_handler(**kwargs):
                        yield response

                toolkit.register_middleware(my_middleware)

        Args:
            middleware (`Callable[..., Coroutine[Any, Any, \
AsyncGenerator[ToolResponse, None]] | AsyncGenerator[ToolResponse, None]]`):
                The middleware function that accepts ``kwargs`` (dict) and
                ``next_handler`` (Callable), and returns a coroutine that
                yields AsyncGenerator of ToolResponse objects. The ``kwargs``
                dict currently includes ``tool_call`` (ToolCallBlock), and may
                include additional context in future versions.

        .. note:: The middleware chain is applied inside the
        `call_tool_function` via the `@apply_middlewares` decorator. This
        ensures that the `@trace_toolkit` decorator remains at the outermost
        layer for complete observability.
        """
        # Simply append the middleware to the list
        # The @apply_middlewares decorator will handle the execution
        self._middlewares.append(middleware)

    def _get_available_tools(
        self,
        groups: list[str] | None,
    ) -> dict[str, RegisteredTool]:
        """Return the currently available tools based on the given
        activated tool groups. Tools in the ``"basic"`` group are always
        included. When at least one tool group is registered, the built-in
        meta tool is also included.

        Args:
            groups (`list[str]`):
                The list of currently activated tool group names.

        Returns:
            `dict[str, RegisteredTool]`:
                The dictionary of available tool name and their corresponding
                RegisteredTool objects.
        """
        available_tools = {}
        if len(self.groups) > 0:
            available_tools[
                self.builtin_meta_tool.tool.name
            ] = self.builtin_meta_tool

        groups_filter = ["basic"] + (groups or [])
        for tool_name, tool in self.tools.items():
            if tool.group in groups_filter:
                available_tools[tool_name] = tool

        return available_tools
