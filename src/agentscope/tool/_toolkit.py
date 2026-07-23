# -*- coding: utf-8 -*-
"""The toolkit class for tool calls in AgentScope."""
import asyncio
import inspect
from collections import OrderedDict
from typing import (
    AsyncGenerator,
    Type,
    Generator,
    Sequence,
)

import mcp
from jinja2 import Template
from pydantic import (
    BaseModel,
    Field,
    create_model,
)

from ._builtin import ResetTools, SkillViewer
from ._base import ToolBase
from ._response import ToolResponse, ToolChunk
from ..skill import SkillLoaderBase, Skill
from ._types import RegisteredTool
from .._utils._common import _json_loads_with_repair
from ..exception import (
    DeveloperOrientedException,
    ToolNotFoundError,
    ToolGroupInactiveError,
)
from ..mcp import MCPClient
from ..message import (
    ToolCallBlock,
    TextBlock,
    ToolResultState,
)
from ._tool_group import ToolGroup
from .._logging import logger
from ..state import AgentState


# pylint: disable=line-too-long
DEFAULT_META_TOOL_RESPONSE_TEMPLATE = """{% if groups | length == 0 %}All tool groups are currently deactivated.{% else %}The currently activated tool group(s): {{ groups | map(attribute='name') | join(', ') }}.{% if groups | selectattr('instructions', 'ne', None) | list | length > 0 %}
<tool-instructions>
The tool instructions are a collection of suggestions, rules and notifications about how to use the tools in the activated groups.
{% for group in groups %}{% if group.instructions %}<group name="{{ group.name }}">{{ group.instructions }}</group>{% endif %}{% endfor %}
</tool-instructions>{% endif %}{% endif %}"""  # noqa: E501


DEFAULT_SKILL_INSTRUCTION = """<agent-skills>
Skills are a collection of instructions, scripts, and resources to extend your capabilities.

**IMPORTANT**: Skills are NOT tools, and you cannot call a skill directly. To use a skill, you MUST use the `{{ skill_viewer }}` tool to read the skill's full instructions, and then follow those instructions to use the tools and resources provided by the skill.

# Available Skills:{% for skill in skills %}
<skill>
<name>{{ skill.name }}</name>
<description>{{ skill.description }}</description>
<dir>{{ skill.dir }}</dir>
</skill>{% endfor %}
</agent-skills>
"""  # noqa: E501


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

    def __init__(
        self,
        tools: list[ToolBase] | None = None,
        skills_or_loaders: Sequence[str | Skill | SkillLoaderBase]
        | None = None,
        mcps: list[MCPClient] | None = None,
        tool_groups: list[ToolGroup] | None = None,
        meta_tool_response_template: str = DEFAULT_META_TOOL_RESPONSE_TEMPLATE,
        skill_instruction_template: str = DEFAULT_SKILL_INSTRUCTION,
    ) -> None:
        """Initialize the toolkit.

        Args:
            tools (`list[ToolBase] | None`, optional):
                The tool objects that belong to the "basic" tool group.
            skills_or_loaders (`list[str | Skill | SkillLoaderBase] | None`, \
            optional):
                The agent skill directories to be registered in the "basic"
                tool group.
            mcps (`list[MCPClient] | None`, optional):
                The mcp clients to be registered in the "basic" tool group.
            tool_groups (`list[ToolGroup] | None`, optional):
                The tool groups to be registered.
            meta_tool_response_template (`str`, optional):
                The template for meta tool responses.
            skill_instruction_template (`str`):
                A Jinja2 template for generating the agent skill instruction.
        """

        if tool_groups is not None and any(
            _.name == "basic" for _ in tool_groups
        ):
            raise ValueError(
                "The 'basic' tool group is reserved for the default tool "
                "group. Don't include 'basic' in the tool_groups argument "
                "when you also provide tools, skills or mcps in the "
                "constructor.",
            )

        self.tool_groups = [
            ToolGroup(
                name="basic",
                tools=tools or [],
                skills_or_loaders=skills_or_loaders or [],
                mcps=mcps or [],
            ),
        ] + (tool_groups or [])

        # Check name conflict for tool groups
        if len(set(_.name for _ in self.tool_groups)) != len(
            self.tool_groups,
        ):
            raise ValueError(
                "Tool groups must not contain duplicate tool groups.",
            )

        # The stateful MCP clients should be initialized already
        for group in self.tool_groups:
            for client in group.mcps:
                if client.is_stateful and not client.is_connected:
                    raise ValueError(
                        f"The MCP client '{client.name}' is stateful, but "
                        f"not connected.",
                    )

        self.meta_tool_response_template = meta_tool_response_template
        self.skill_instruction_template = skill_instruction_template

        self.builtin_meta_tool = RegisteredTool(
            tool=ResetTools(
                # An inference value for groups so that it can generate the
                # corresponding input schema.
                groups=self.tool_groups,
                response_template=meta_tool_response_template,
            ),
        )

        self.builtin_skill_viewer = RegisteredTool(
            tool=SkillViewer(
                get_skills_method=self._get_available_skills,
            ),
        )

    async def get_tool_schemas(
        self,
        groups: list[str] | None = None,
    ) -> list[dict]:
        """Get the JSON schemas of the currently available tool functions
        based on the given activated tool groups.

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

        # Get all available tools
        tools_dict = await self._get_available_tools(groups)
        for tool in tools_dict.values():
            function_schemas.append(tool.get_tool_schema())

        return function_schemas

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
            state (`AgentState`):
                The current agent state, used to state injection.

        Yields:
            `ToolChunk | ToolResponse`:
                The incremental tool result in a ToolChunk stream, and finally
                the complete tool result in a ToolResponse object.
        """
        tool_response = ToolResponse(id=tool_call.id)

        # Check
        available_tools = await self._get_available_tools(
            state.tool_context.activated_groups,
        )

        if tool_call.name not in available_tools:
            all_tools = await self._get_available_tools(
                groups=[_.name for _ in self.tool_groups],
            )
            # Not activate
            if tool_call.name in all_tools:
                group_name = all_tools[tool_call.name].group
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
                    state=ToolResultState.ERROR,
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
                state=ToolResultState.ERROR,
            )
            yield chunk
            yield tool_response.append_chunk(chunk)
            return

        # Obtain the tool function
        tool_func = available_tools[tool_call.name].tool

        # Async function
        try:
            # Prepare keyword arguments
            kwargs = _json_loads_with_repair(tool_call.input)

            # State injection
            if (
                tool_func.is_state_injected
                and not tool_func.is_mcp
                and not tool_func.is_external_tool
            ):
                kwargs["_agent_state"] = state

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
                state=ToolResultState.ERROR,
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
                        text=str(e),
                    ),
                ],
                state=ToolResultState.ERROR,
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
                state=ToolResultState.INTERRUPTED,
            )
            yield chunk
            tool_response.append_chunk(chunk)

        finally:
            # Finally, yield the complete tool response
            yield tool_response

    async def _get_available_skills(
        self,
        groups: list[str] | None = None,
    ) -> dict[str, Skill]:
        """A unified method to collect all skills from the registered skill
        loaders.

        Args:
            groups (`list[str] | None`, optional):
                A list of group names to filter the skill loaders. The "basic"
                group will always be included regardless of the filter. If not
                provided, only the "basic" group will be included.

        Returns:
            `dict[str, Skill]`:
                A dictionary of skill name and their corresponding Skill
                objects.
        """
        groups_filter = ["basic"] + (groups or [])

        skills = OrderedDict()
        for group in self.tool_groups:
            if group.name not in groups_filter:
                continue

            for skill in await group.list_skills():
                if skill.name in skills:
                    logger.warning(
                        "Duplicate skill name '%s' found in group '%s', "
                        "overwriting it.",
                        skill.name,
                        group.name,
                    )
                skills[skill.name] = skill

        return skills

    async def get_skill_instructions(
        self,
        activated_groups: list[str] | None = None,
    ) -> str | None:
        """Get the prompt for all registered agent skills, which can be
        attached to the system prompt for the agent.

        The prompt is consisted of an overall instruction and the detailed
        descriptions of each skill, including its name, description, and
        directory.

        .. note:: If no skill is registered, None will be returned.

        Args:
            activated_groups (`list[str] | None`, optional):
                The currently activated tool groups. If omitted, all groups
                will be included for backwards compatibility.

        Returns:
            `str | None`:
                The combined prompt for registered agent skills, or None
                if no skill is registered.
        """
        if activated_groups is None:
            activated_groups = [_.name for _ in self.tool_groups]

        skills = await self._get_available_skills(
            activated_groups,
        )

        # If no skills were collected, return None
        if len(skills) == 0:
            return None

        # Generate the skill instruction prompt with the template
        template = Template(self.skill_instruction_template)

        return template.render(
            skills=skills.values(),
            skill_viewer=self.builtin_skill_viewer.tool.name,
        )

    async def _get_available_tools(
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

        # Built-in skill viewers
        skills = await self._get_available_skills(groups)
        if len(skills):
            available_tools[
                self.builtin_skill_viewer.tool.name
            ] = self.builtin_skill_viewer

        # Builtin meta tool is only included when there is at least one tool
        # group
        if (
            len(self.tool_groups) == 1
            and self.tool_groups[0].name != "basic"
            or len(self.tool_groups) > 1
        ):
            available_tools[
                self.builtin_meta_tool.tool.name
            ] = self.builtin_meta_tool

        # The tools in the activated groups and the "basic" group are included
        groups_filter = ["basic"] + (groups or [])
        for group in self.tool_groups:
            if group.name not in groups_filter:
                continue

            cache_tools = []
            # Python tools
            for tool in group.tools:
                cache_tools.append(tool)

            # MCP tools
            for client in group.mcps:
                tools = await client.list_tools()
                cache_tools.extend(tools)

            # Append cached tools into the available tools and solve the name
            # conflict
            for tool in cache_tools:
                if tool.name in available_tools:
                    logger.warning(
                        "Duplicate tool name '%s' found in group '%s', "
                        "overwriting it.",
                        tool.name,
                        group.name,
                    )
                available_tools[tool.name] = RegisteredTool(
                    tool=tool,
                    group=group.name,
                )

        return available_tools

    async def check_tool_available(
        self,
        tool_name: str,
        activated_groups: list[str],
    ) -> ToolBase:
        """Check if the tool is available now. If not, raise the
        agent-oriented exception.

        Args:
            tool_name (`str`):
                The name of the tool to be checked.
            activated_groups (`list[str]`):
                The currently activated tool groups.

        Returns:
            `ToolBase`:
                If the tool is available, return the corresponding ToolBase
                object. Otherwise, raise the agent-oriented exception with the
                error message.
        """
        tools = await self._get_available_tools(activated_groups)
        if tool_name not in tools:
            raise ToolNotFoundError(
                f"ToolNotFoundError: The tool named '{tool_name}' doesn't "
                f"exist.",
            )

        group_name = tools[tool_name].group
        if group_name != "basic" and group_name not in activated_groups:
            raise ToolGroupInactiveError(
                f"ToolGroupInactiveError: The tool '{tool_name}' in group "
                f"'{group_name}' is currently inactive. "
                f"You should first activate the group by calling the "
                f"'{self.builtin_meta_tool.tool.name}' tool.",
            )

        return tools[tool_name].tool

    async def get_tool(self, name: str) -> ToolBase | None:
        """Get tool instance by its name.

        Args:
            name (`str`):
                The name of the tool to be checked.

        Returns:
            `ToolBase | None`:
                The tool instance, or `None` if no tool is found.
        """
        tools = await self._get_available_tools(
            [_.name for _ in self.tool_groups],
        )
        registered_tool = tools.get(name, None)
        if registered_tool is None:
            return None
        return registered_tool.tool

    def _get_meta_tool_schema(self) -> Type[BaseModel]:
        """Get the meta tool schema based on the current tool groups."""
        fields = {}
        for group in self.tool_groups:
            if group.name == "basic":
                continue
            fields[group.name] = (
                bool,
                Field(
                    default=False,
                    description=group.description,
                ),
            )
        return create_model("_DynamicModel", **fields)

    def clear(self) -> None:
        """Clear the registered tools, skills and MCPs."""
        self.tool_groups.clear()

    async def add_tool(
        self,
        tool: ToolBase | list[ToolBase],
        group_name: str = "basic",
    ) -> None:
        """Add tool to the toolkit on-the-fly.

        Args:
            tool (`ToolBase | list[ToolBase]`):
                The tool to be added.
            group_name (`str`):
                The group name of the tool to be added.
        """

        new_tools = tool if isinstance(tool, list) else [tool]

        for group in self.tool_groups:
            if group.name == group_name:
                existing_tools = {_.name for _ in group.tools}
                for new_tool in new_tools:
                    if new_tool.name in existing_tools:
                        logger.warning(
                            "Duplicate tool name '%s' found in group '%s', "
                            "overwriting it.",
                            new_tool.name,
                            group.name,
                        )
                        # override the existing tool
                        group.tools = [
                            t for t in group.tools if t.name != new_tool.name
                        ] + [new_tool]
                    else:
                        group.tools.append(new_tool)
                        existing_tools.add(new_tool.name)

                return

        raise ValueError(
            f"Cannot find group '{group_name}' in toolkit, only "
            f"{[_.name for _ in self.tool_groups]} are available.",
        )

    async def remove_tool(self, tool_name: str | list[str]) -> None:
        """Remove tool from the toolkit on-the-fly.

        Args:
            tool_name (`str | list[str]`):
                The name of the tool to be removed.
        """
        if isinstance(tool_name, str):
            tool_name = [tool_name]

        for group in self.tool_groups:
            group.tools = [
                tool for tool in group.tools if tool.name not in tool_name
            ]
