# -*- coding: utf-8 -*-
"""
This file is the core of the meta tool system.

It contains the CategoryManager class, which manages the tools within a
specific category. It also contains the MetaManager class, which is the
top-level class that manages the category managers.
"""

import json
import os
from typing import (
    Any,
    Dict,
    Literal,
    Optional,
    Union,
)
from agentscope.model import ChatModelBase
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemoryMemory, MemoryBase
from agentscope.message import Msg, TextBlock
from agentscope.tool import Toolkit, ToolResponse
from agentscope.tool._registered_tool_function import RegisteredToolFunction
from agentscope.tool._toolkit import ToolGroup
from meta_config_models import MetaToolConfig, CategoryConfig

# Constants
MAX_ITERATIONS = 5


class CategoryManager:
    """Level 2 Category Manager - Manages tools within a specific category."""

    def __init__(
            self,
            category_name: str,
            category_description: str,
            model: ChatModelBase,
            formatter: FormatterBase,
            tool_usage_notes: str = "",
            memory: MemoryBase = InMemoryMemory(),
    ):
        """Initialize the Category Manager

        Args:
            category_name (`str`):
                The unique name identifier for this category manager.
            category_description (`str`):
                A comprehensive description of what this category manages
                and its functional scope.
            model (`ChatModelBase`):
                The chat model used for internal tool selection, tool result
                evaluation, and summary generation within this category.
            formatter (`FormatterBase`):
                The formatter used to format the messages into the required
                format of the model API provider.
            tool_usage_notes (`str`, optional):
                Special usage notes and considerations for tools in this
                category that will be included in the system prompts.
            memory (`MemoryBase`, optional):
                The memory instance to be used by this category manager.
                Defaults to InMemoryMemory() if not provided.
        """
        self.category_name = category_name
        self.category_description = category_description
        self.model = model
        self.tool_usage_notes = tool_usage_notes
        self.memory = memory
        self.formatter = formatter
        # internal level 1 tools
        self.internal_toolkit = Toolkit()

    def _generate_category_json_schema(self) -> dict:
        """Generate JSON schema for this category manager as a meta-tool.

        Returns:
            `dict`:
                A JSON schema in OpenAI function calling format that defines
                this category manager as a callable tool function with
                'objective' and 'exact_input' parameters.
        """
        return {
            "type": "function",
            "function": {
                "name": self.category_name,
                "description": (
                    f"{self.category_description} This category automatically "
                    "selects and operates the most appropriate tool based on "
                    "your objective and input."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": (
                                "A clear and well-defined description of the "
                                "goal you wish to accomplish using tools in "
                                "this category. Be explicit about your "
                                "intended outcome to ensure accurate tool "
                                "selection and execution."
                            ),
                        },
                        "exact_input": {
                            "type": "string",
                            "description": (
                                "The precise, detailed, and complete input "
                                "or query to be processed by the selected "
                                "tool. Ensure all relevant data, context, "
                                "and execution details are fully provided to "
                                "enable accurate tool operation."
                            ),
                        },
                    },
                    "required": ["objective", "exact_input"],
                },
            },
        }

    @property
    def json_schema(self) -> dict:
        """Return the JSON schema for this category manager.

        Returns:
            `dict`:
                The JSON schema that defines this category manager as a
                callable tool function for external agents.
        """
        return self._generate_category_json_schema()

    def generate_internal_tool_json_schema(self) -> dict:
        """Generate JSON schema for the internal tools of this category
        manager.

        Returns:
            `dict`:
                A list of JSON schemas for all tools contained within this
                category's internal toolkit.
        """
        return self.internal_toolkit.get_json_schemas()

    def _get_prompt(
            self,
            prompt_type: Literal[
                "tool_selection",
                "tool_result_evaluation",
                "max_iteration_summary",
            ],
    ) -> str:
        """Generate system prompt for tool selection by reading from file.

        Args:
            prompt_type (`Literal["tool_selection", "tool_result_evaluation", \
            "max_iteration_summary"]`):
                The type of prompt to generate. Each type corresponds to a
                different phase of the execution workflow:
                - "tool_selection": For initial tool selection
                - "tool_result_evaluation": For evaluating tool results
                - "max_iteration_summary": For generating final summaries

        Returns:
            `str`:
                The formatted system prompt.
        """
        # Get current directory
        current_dir = os.path.dirname(os.path.realpath(__file__))

        # Read prompt template from file
        prompt_file_path = os.path.join(
            current_dir,
            "meta_tool_prompts",
            f"prompt_{prompt_type}.md",
        )

        with open(prompt_file_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()

        formatted_prompt = prompt_template.format_map(
            {
                "category_name": self.category_name,
            },
        )

        # Add tool usage notes if they exist
        if self.tool_usage_notes and self.tool_usage_notes.strip():
            formatted_prompt += f"""

## Special Tool Usage Considerations for {self.category_name}
{self.tool_usage_notes.strip()}

Please keep these considerations in mind when generating tool calls."""
        return formatted_prompt

    def add_internal_func_obj(
            self,
            func_obj: RegisteredToolFunction = None,
            tool_group: ToolGroup = None,
    ) -> None:
        """Add an internal tool function object to the category manager.

        Args:
            func_obj (`RegisteredToolFunction`):
                The registered tool function object to be added to this
                category's internal toolkit.
            tool_group (`ToolGroup`, optional):
                The tool group information from the global toolkit. If
                provided and the function's group doesn't exist in the
                internal toolkit, the group will be created inside the
                category manager, maintaining consistency with the outside.

        Note:
            This method directly adds the tool function object to the internal
            toolkit and preserves the original group structure from the
            global toolkit.
        """
        self.internal_toolkit.tools[func_obj.name] = func_obj

        # Classify internal_toolkit according to the original groups of the
        # toolkit if needed
        if func_obj.group not in self.internal_toolkit.groups and tool_group:
            self.internal_toolkit.groups[func_obj.group] = tool_group

    async def execute_category_task(
            self,
            objective: str,
            exact_input: str,
    ) -> ToolResponse:
        """Execute a task within this category using intelligent tool
        selection.

        This is the core method that implements the multi-round
        reasoning-acting
        loop for category-level task execution. It performs tool selection,
        execution, evaluation, and result synthesis automatically.

        Returns:
            `ToolResponse`:
                A response containing the execution results in JSON format
                with fields:
                - "all_execution_results": Detailed history of tool executions
                - "summary": Comprehensive summary of accomplishments
                - "category": Category name for tracking

        Note:
            The method implements a maximum of MAX_ITERATIONS iterations for
            the
            reasoning-acting loop.
        """
        try:
            # 1. Check tool availability
            if not self.internal_toolkit.tools:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"'{self.category_name}' has no available "
                                f"tools"
                            ),
                        ),
                    ],
                    metadata={
                        "success": False,
                    },
                )

            # 2. First round: tool selection (using tool_selection prompt)
            response = await self._llm_select_tools(objective, exact_input)

            reasoning = response.get("reasoning", "")
            tool_calls = response.get("tool_calls", [])

            # 3. Check if there are tool calls
            if not tool_calls:
                # No tool calls - did not select any tool due to constraint
                # analysis
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"Based on the constraint analysis, the "
                                f"{self.category_name} category selects not "
                                f"to perform any tool calls. \n\n Reason: "
                                f"{reasoning}"
                            ),
                        ),
                    ],
                    metadata={
                        "success": True,
                    },
                )

            await self.memory.clear()

            # Add user request to memory
            await self.memory.add(
                Msg(
                    name="user",
                    content=f"Task: {objective}\nInput: {exact_input}",
                    role="user",
                ),
            )

            # Add initial reasoning to memory
            await self.memory.add(
                Msg(
                    name="assistant",
                    content=reasoning,
                    role="assistant",
                ),
            )

            all_execution_results = []
            max_iterations = MAX_ITERATIONS
            iteration = 0

            # 5. Execution loop
            while iteration < max_iterations:
                iteration += 1

                # Execute current round of tool calls
                current_results = await self._execute_tool_calls(tool_calls)
                all_execution_results.extend(current_results)

                # Evaluate whether to continue (using tool_result_evaluation
                # prompt)
                evaluation_response = await self._evaluate_tool_results(
                    objective,
                    exact_input,
                )
                evaluation_text = evaluation_response.get("reasoning", "")
                new_tool_calls = evaluation_response.get("tool_calls", [])

                # Add evaluation result to memory
                if evaluation_text:
                    await self.memory.add(
                        Msg(
                            name="assistant",
                            content=evaluation_text,
                            role="assistant",
                        ),
                    )

                # Key judgment: no new tool calls = task completed
                if not new_tool_calls:
                    final_output = {
                        "all_execution_results": all_execution_results,
                        "summary": evaluation_text,
                        "category": self.category_name,
                    }
                    final_output_str = json.dumps(
                        final_output,
                        indent=4,
                        ensure_ascii=False,
                    )

                    return ToolResponse(
                        content=[
                            TextBlock(
                                type="text",
                                text=final_output_str,
                            ),
                        ],
                        metadata={
                            "success": True,
                        },
                    )

                # Continue to next round
                tool_calls = new_tool_calls

            # Reached maximum iterations
            max_iter_summary = await self._generate_max_iteration_summary()
            final_output = {
                "all_execution_results": all_execution_results,
                "summary": max_iter_summary,
                "category": self.category_name,
            }
            final_output_str = json.dumps(
                final_output,
                indent=4,
                ensure_ascii=False,
            )

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=final_output_str,
                    ),
                ],
                metadata={
                    "success": True,
                },
            )

        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"{self.category_name} execution error: {str(e)}",
                    ),
                ],
                metadata={
                    "success": False,
                },
            )
        finally:
            await self.memory.clear()

    async def _llm_select_tools(
            self,
            objective: str,
            exact_input: str,
    ) -> dict:
        """Perform initial tool selection using LLM reasoning.

        Uses the tool_selection prompt template to guide the LLM in selecting
        the most appropriate tools from the internal toolkit based on the
        objective and input parameters.

        Returns:
            `dict`:
                A dictionary containing:
                - "reasoning": The LLM's reasoning for tool selection
                - "tool_calls": List of selected tool calls in ToolUseBlock
                  format

        Note:
            If no tools are selected due to constraint analysis or missing
            requirements, the tool_calls list will be empty and reasoning
            will contain the explanation.
        """
        try:
            # 1. Build prompt
            system_prompt = self._get_prompt("tool_selection")
            user_content = (
                f"Task objective: {objective}\nInput data: {exact_input}"
            )

            # 2. Format messages
            messages = await self.formatter.format(
                msgs=[
                    Msg("system", system_prompt, "system"),
                    Msg("user", user_content, "user"),
                ],
            )

            # 3. Call model
            tools = self.internal_toolkit.get_json_schemas()
            res = await self.model(messages, tools=tools)

            # 4. Handle response
            msg = await self._handle_model_response(res)
            return self._parse_tool_selection_response(msg)

        except Exception as e:
            return {
                "reasoning": f"Tool selection failed: {str(e)}",
                "tool_calls": [],
            }

    async def _handle_model_response(self, res: Any) -> Msg:
        """Handle model response for both streaming and non-streaming."""
        msg = None
        try:
            if self.model.stream:
                # Streaming response: res is AsyncGenerator[ChatResponse]
                msg = Msg(self.category_name, [], "assistant")
                async for content_chunk in res:
                    msg.content = content_chunk.content
            else:
                # Non-streaming response: res is ChatResponse
                msg = Msg(
                    self.category_name,
                    list(res.content),
                    "assistant",
                )
            return msg
        except Exception as parse_error:
            raise ValueError(
                f"Response parsing failed: {str(parse_error)}",
            ) from parse_error

    def _parse_tool_selection_response(self, msg: Msg) -> dict:
        """Parse tool selection response and extract reasoning and
        tool calls."""
        try:
            reasoning = ""
            tool_calls = []

            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            reasoning += block.get("text", "")
                        elif block.get("type") == "tool_use":
                            tool_calls.append(block)

            if hasattr(msg, "get_content_blocks"):
                tool_calls = msg.get_content_blocks("tool_use")

            return {
                "reasoning": reasoning,
                "tool_calls": tool_calls,
            }

        except Exception as parse_error:
            return {
                "reasoning": (f"Response parsing failed: {str(parse_error)}"),
                "tool_calls": [],
            }

    async def _evaluate_tool_results(
            self,
            objective: str,  # pylint: disable=unused-argument
            exact_input: str,  # pylint: disable=unused-argument
    ) -> dict:
        """Evaluate tool execution results and determine next actions.

        Uses the tool_result_evaluation prompt template and complete memory
        history to assess whether the current tool execution results have
        successfully fulfilled the task or if additional tool calls are needed.

        Returns:
            `dict`:
                A dictionary containing:
                - "reasoning": The LLM's evaluation and reasoning
                - "tool_calls": List of additional tool calls if needed, or
                  empty list if task is complete

        Note:
            This method uses the complete memory history (including previous
            tool results) to make informed decisions about task completion
            and next steps.
        """
        try:
            # 1. Build evaluation prompt
            system_prompt = self._get_prompt("tool_result_evaluation")

            # 2. Use complete memory history for evaluation
            messages = await self.formatter.format(
                msgs=[
                    Msg("system", system_prompt, "system"),
                    *await self.memory.get_memory(),
                ],
            )

            # 3. Call model for evaluation
            tools = self.internal_toolkit.get_json_schemas()
            res = await self.model(messages, tools=tools)

            # 4. Handle response
            msg = await self._handle_model_response(res)
            return self._parse_tool_selection_response(msg)

        except Exception as e:
            return {
                "reasoning": f"Evaluation failed: {str(e)}",
                "tool_calls": [],
            }

    async def _generate_max_iteration_summary(self) -> str:
        """Generate intelligent summary when reaching maximum iterations.

        Uses the max_iteration_summary prompt template and complete
        memory history to generate a comprehensive summary of what was
        accomplished and what remains incomplete when the maximum iteration
        limit is reached.

        Returns:
            `str`:
                A comprehensive summary of the execution history, including
                successful tool executions, their outputs, and any incomplete
                aspects of the original objective.

        Note:
            This method is called when the category manager reaches the
            maximum number of iterations (5) without completing the task.
            It provides a failsafe to ensure users receive meaningful
            information even in complex or incomplete scenarios.
        """
        try:
            system_prompt = self._get_prompt("max_iteration_summary")

            messages = await self.formatter.format(
                msgs=[
                    Msg("system", system_prompt, "system"),
                    *await self.memory.get_memory(),
                ],
            )

            res = await self.model(messages)  # No need for tools parameter

            msg = None
            try:
                if self.model.stream:
                    # Streaming response
                    msg = Msg(self.category_name, [], "assistant")
                    async for content_chunk in res:
                        msg.content = content_chunk.content
                else:
                    # Non-streaming response
                    msg = Msg(
                        self.category_name,
                        list(res.content),
                        "assistant",
                    )

                # Extract text content
                summary_text = ""
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if (
                                isinstance(block, dict)
                                and block.get("type") == "text"
                        ):
                            summary_text += block.get("text", "")

                # Use get_text_content method (if available)
                if hasattr(msg, "get_text_content"):
                    summary_text = msg.get_text_content()

                return summary_text or (
                    f"Reached maximum iterations ({MAX_ITERATIONS} times). "
                    "Summary generation succeeded but content is empty."
                )

            except Exception as parse_error:
                return f"Summary parsing failed: {str(parse_error)}"

        except Exception as e:
            return (
                f"Reached maximum iterations ({MAX_ITERATIONS} times). "
                f"Summary generation failed: {str(e)}"
            )

    async def _execute_tool_calls(self, tool_calls: list) -> list:
        """Execute a list of tool calls and return structured results.

        Executes each tool call sequentially, captures results, and records
        them in memory for subsequent evaluation.


        Returns:
            `list`:
                A list of execution result dictionaries, each containing:
                - 'tool_name': Name of the executed tool
                - 'tool_args': Arguments passed to the tool
                - 'result': Execution result or error message
                - 'status': 'SUCCESS' or 'ERROR'

        Note:
            All results are automatically added to the category's memory
            for use in subsequent evaluation steps. Invalid tool call
            formats are handled gracefully and reported as errors.
        """
        results = []

        for tool_call in tool_calls:
            try:
                # Ensure tool_call is in correct ToolUseBlock format
                if not isinstance(tool_call, dict) or "name" not in tool_call:
                    results.append(
                        {
                            "tool_name": "unknown",
                            "tool_args": {},
                            "result": f"Invalid tool call format: {tool_call}",
                            "status": "ERROR",
                        },
                    )
                    continue

                tool_res = await self.internal_toolkit.call_tool_function(
                    tool_call,
                )

                result_chunks = []
                async for chunk in tool_res:
                    result_chunks.append(chunk)

                # Get final result
                final_result = result_chunks[-1] if result_chunks else None

                # Extract content from ToolResponse
                if final_result and hasattr(final_result, "content"):
                    result_text = ""
                    for content_block in final_result.content:
                        if (
                                isinstance(content_block, dict)
                                and content_block.get("type") == "text"
                        ):
                            result_text += content_block.get("text", "")

                    result_content = (
                            result_text
                            or "Execution successful but no text output"
                    )
                else:
                    result_content = "No result"

                # Record result
                results.append(
                    {
                        "tool_name": tool_call["name"],
                        "tool_args": tool_call.get("input", {}),
                        "result": result_content,
                        "status": "SUCCESS" if final_result else "ERROR",
                    },
                )

                # Add to memory (simplified format)
                await self.memory.add(
                    Msg(
                        name="tool_result",
                        content=(
                            f"Executed {tool_call['name']}: {result_content}"
                        ),
                        role="system",
                    ),
                )

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                results.append(
                    {
                        "tool_name": tool_call.get("name", "unknown"),
                        "tool_args": tool_call.get("input", {}),
                        "result": error_msg,
                        "status": "ERROR",
                    },
                )

                await self.memory.add(
                    Msg(
                        name="tool_result",
                        content=(
                            f"Tool {tool_call.get('name', 'unknown')} failed: "
                            f"{error_msg}"
                        ),
                        role="system",
                    ),
                )

        return results


class MetaManager(Toolkit):
    """Level 3 Meta Manager - Manages Level 2 Category Managers.

    The MetaManager extends the Toolkit class to provide hierarchical tool
    management. It manages CategoryManager instances and exposes them as
    callable tools to external agents, while hiding the internal tool
    complexity.
    """

    def __init__(
            self,
            model: Optional[ChatModelBase] = None,
            meta_tool_config: Optional[Union[Dict, MetaToolConfig]] = None,
            global_toolkit: Optional[Toolkit] = None,
            formatter: Optional[FormatterBase] = None,
            memory: MemoryBase = InMemoryMemory(),
    ) -> None:
        """Initialize the Meta Manager.

        Args:
            model (`ChatModelBase`, optional):
                The chat model to be used for all category managers.
                If provided along with meta_tool_config and global_toolkit,
                the meta manager will be automatically initialized.
            meta_tool_config (`Union[Dict, MetaToolConfig]`, optional):
                Dictionary or MetaToolConfig instance containing category
                configurations loaded from Meta_tool_config.json or similar structure.
                If a dictionary is provided, it will be validated using Pydantic.
            global_toolkit (`Toolkit`, optional):
                The global toolkit containing all available tools to be
                distributed among categories.
            formatter (`FormatterBase`, optional):
                The formatter to be used for all category managers.
                Required if auto-initialization is desired (when model,
                meta_tool_config, and global_toolkit are all provided).
            memory (`MemoryBase`, optional):
                The memory instance to be used by all category managers.
                Defaults to InMemoryMemory() if not provided.
        """
        # self.toolkit manages the external interface of category manager.
        # The internal routing is by self.category_managers
        super().__init__()
        self.category_managers: Dict[str, CategoryManager] = {}

        # Auto-initialize if all required parameters are provided
        if model is not None and meta_tool_config is not None and global_toolkit is not None:
            if formatter is None:
                raise ValueError(
                    "formatter parameter is required when auto-initializing MetaManager. "
                    "Please provide a formatter that matches your model type."
                )

            # Validate and convert config
            validated_config = self._validate_config(meta_tool_config)
            self._initialize_from_config(model, validated_config, global_toolkit, formatter, memory)

    def _validate_config(
            self,
            config: Union[Dict, MetaToolConfig]
    ) -> Union[Dict, MetaToolConfig]:
        """Validate configuration using Pydantic models.

        Args:
            config (`Union[Dict, MetaToolConfig]`):
                Configuration to validate. If already a MetaToolConfig instance,
                it's considered validated and returned as-is.

        Returns:
            `Union[Dict, MetaToolConfig]`: Validated configuration.

        Raises:
            ValueError: If configuration is invalid.
        """

        # If already validated (MetaToolConfig instance), return as-is
        if isinstance(config, MetaToolConfig):
            return config
        if isinstance(config, dict):
            if MetaToolConfig is not None:
                # Pydantic available, validate the config
                try:
                    return MetaToolConfig.from_dict(config)
                except Exception as e:
                    raise ValueError(f"Configuration validation failed: {e}") from e
            else:
                # Pydantic not available, return dict as-is with warning
                print("Warning: Pydantic not available. Configuration validation skipped.")
                return config

        # Invalid type
        raise ValueError(
            f"Invalid configuration type: {type(config)}. "
            f"Expected dict or MetaToolConfig-like object with to_dict() method."
        )

    def _initialize_from_config(
            self,
            model: ChatModelBase,
            meta_tool_config: Union[Dict, MetaToolConfig],
            global_toolkit: Toolkit,
            formatter: FormatterBase,
            memory: MemoryBase,
    ) -> None:
        """Initialize category managers from configuration.

        Args:
            model (`ChatModelBase`):
                The chat model to be used for all category managers.
            meta_tool_config (`Union[Dict, MetaToolConfig]`):
                Dictionary or MetaToolConfig instance containing category configurations.
            global_toolkit (`Toolkit`):
                The global toolkit containing all available tools.
            formatter (`FormatterBase`):
                The formatter to be used for all category managers.
            memory (`MemoryBase`):
                The memory instance to be used by all category managers.
        """
        # Convert MetaToolConfig to dict if needed
        if isinstance(meta_tool_config, MetaToolConfig):
            config_dict = meta_tool_config.to_dict()
        elif isinstance(meta_tool_config, dict):
            config_dict = meta_tool_config
        else:
            raise ValueError(
                f"meta_tool_config must be a dict or MetaToolConfig instance, "
                f"got {type(meta_tool_config)}"
            )

        # Iterate through categories
        for category_name, category_config in config_dict.items():
            category_manager = CategoryManager(
                category_name=category_name,
                category_description=category_config["description"],
                model=model,
                formatter=formatter,
                tool_usage_notes=category_config.get("tool_usage_notes", ""),
                memory=memory,
            )

            # Add tools to the category manager
            for tool_name in category_config["tools"]:
                if tool_name not in global_toolkit.tools:
                    print(f"Tool {tool_name} not found in global toolkit")
                    continue

                tool_func_obj = global_toolkit.tools[tool_name]
                if tool_func_obj.group == "basic":
                    category_manager.add_internal_func_obj(
                        func_obj=tool_func_obj,
                    )
                else:
                    category_manager.add_internal_func_obj(
                        func_obj=tool_func_obj,
                        tool_group=global_toolkit.groups[tool_func_obj.group],
                    )

            # Only add category manager if it has tools
            if not category_manager.internal_toolkit.tools:
                print(f"Category {category_name} has no tools, skip it")
                continue

            self.add_category_manager(category_manager=category_manager)

    def add_category_manager(self, category_manager: CategoryManager) -> None:
        """Add a category manager to the meta manager.

        Registers a CategoryManager instance as a callable tool function
        in the meta manager's toolkit. The category manager becomes accessible
        to external agents as a standard tool with objective and exact_input
        parameters.

        Args:
            category_manager (`CategoryManager`):
                The category manager instance to be added. Must have a unique
                category name that doesn't conflict with existing managers.

        Raises:
            ValueError: If a category manager with the same name already
                exists.

        Note:
            The method creates a named wrapper function that forwards calls
            to the category manager's execute_category_task method. This
            ensures proper function naming for tool registration while
            maintaining the execution logic within the category manager.
        """
        category_name = category_manager.category_name
        if category_name in self.category_managers:
            raise ValueError(f"Category {category_name} already exists")

        self.category_managers[category_name] = category_manager

        # category_schema is the external schema exposed to the agent and does
        # not contain any internal tools information
        category_schema = category_manager.json_schema

        # Create a renamable function copy
        async def named_executor(
                objective: str,
                exact_input: str,
        ) -> ToolResponse:
            """
            Wrapper function that forwards calls to the corresponding
            category_manager.
            """
            return await category_manager.execute_category_task(
                objective,
                exact_input,
            )

        # Set function name as category_name
        named_executor.__name__ = category_name
        named_executor.__doc__ = f"{category_manager.category_description}"

        # Key: Register the execution function of CategoryManager as a tool
        self.register_tool_function(
            named_executor,
            json_schema=category_schema,
        )
