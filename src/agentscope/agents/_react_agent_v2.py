# -*- coding: utf-8 -*-
"""The ReAct Agent V2, which use the tools API of the LLM service providers
rather than assembling the prompt manually."""
from typing import Union, Optional, Tuple, Type, Any

from pydantic import BaseModel, ValidationError

from ._agent_base import AgentBase
from ..manager import ModelManager
from ..memory import TemporaryMemory
from ..message import Msg, ToolUseBlock, TextBlock, ContentBlock
from ..model import (
    OpenAIChatWrapper,
    DashScopeChatWrapper,
    AnthropicChatWrapper,
)
from ..service import ServiceToolkit, ServiceResponse, ServiceExecStatus


class ReActAgentV2(AgentBase):
    """The ReAct Agent V2, which use the tools API of the LLM service providers
    rather than assembling the prompt manually.

    The currently supported LLM providers include:
    - Anthropic
    - DashScope
    - OpenAI
    """

    _finish_function: str = "generate_response"
    """The function name used to finish replying and return a response"""

    def __init__(
        self,
        name: str,
        model_config_name: str,
        service_toolkit: ServiceToolkit,
        sys_prompt: str = "You're a helpful assistant named {name}.",
        max_iters: int = 10,
        verbose: bool = True,
        exit_reply_without_tool_calls: bool = True,
    ) -> None:
        """Initial the ReAct agent with the given name, model config name and
        tools.

        Args:
            name (`str`):
                The name of the agent.
            model_config_name (`str`):
                The name of the model config, which is used to load model from
                configuration.
            service_toolkit (`ServiceToolkit`):
                The service toolkit object that contains the tool functions.
            sys_prompt (`str`):
                The system prompt of the agent.
            max_iters (`int`, defaults to `10`):
                The maximum number of iterations of the reasoning-acting loops.
            verbose (`bool`, defaults to `True`):
                Whether to print the detailed information during reasoning and
                acting steps. If `False`, only the content in speak field will
                be print out.
            exit_reply_without_tool_calls (`bool`, defaults to `True`):
                Whether to exit the reply function when no tool calls are
                generated. If `True`, the agent is allowed to generate a
                response without calling the `generate_response` function.
        """
        super().__init__()
        self.name = name
        self.memory = TemporaryMemory()

        self.sys_prompt: str = sys_prompt.format(name=self.name)

        self.model = ModelManager.get_instance().get_model_by_config_name(
            model_config_name,
        )

        assert self.model.model_type in [
            OpenAIChatWrapper.model_type,
            DashScopeChatWrapper.model_type,
            AnthropicChatWrapper.model_type,
        ], (
            f"The model type {self.model.model_type} is not supported yet by "
            f"the ReActAgentV2. Only {OpenAIChatWrapper.model_type}, "
            f"{DashScopeChatWrapper.model_type} and "
            f"{AnthropicChatWrapper.model_type} are supported."
        )

        self.service_toolkit = service_toolkit
        self.service_toolkit.add(
            self.generate_response,
            include_var_keyword=False,
        )

        self.verbose = verbose
        self.max_iters = max_iters
        self.exit_reply_without_tool_calls = exit_reply_without_tool_calls

        # Used to store the structured output in the current reply
        self._current_structured_output = None
        self._current_structured_model: Union[Type[BaseModel], None] = None

        # Clear the structured output after each reply
        self.register_hook(
            "post_reply",
            "as_clear_structured_output",
            self._clear_structured_output_hook,
        )

    async def reply(
        self,
        x: Optional[Union[Msg, list[Msg]]] = None,
        structured_model: Optional[Type[BaseModel]] = None,
    ) -> Msg:
        """The reply method of the agent.

        Args:
            x (`Optional[Union[Msg, list[Msg]]]`):
                The input message(s) to the agent.
            structured_model (`Optional[Type[BaseModel]]`,
             defaults to `None`):
                A Pydantic model class that defines the structured output. If
                provided, the schema of the structured output will be merged
                with the `generate_response` function, and the argument
                `exit_reply_without_tool_calls` will be invalid. The agent
                must call the `generate_response` function to end the reply
                with a structured output.

        Returns:
            `Msg`:
                The response message from the agent.
        """
        # Merge the schema of the structured output with the finish function
        if structured_model:
            self.service_toolkit.extend_function_schema(
                self._finish_function,
                structured_model,
            )

        self.memory.add(x)

        force_call_finish_func = False
        for _ in range(self.max_iters):
            # Reasoning to generate tool calls
            tool_calls, msg_reasoning = self._reasoning(force_call_finish_func)

            # Exit when 1) no tool calls generated, and
            # 2) exit_reply_without_tool_calls is True
            # 3) structured output is not required
            if (
                tool_calls is None
                and self.exit_reply_without_tool_calls
                and structured_model is None
            ):
                self.memory.add(msg_reasoning)
                return msg_reasoning

            # If exit_reply_without_tool_calls is False, or structured output
            # is required, we drop the last reasoning message and
            # force the LLM to call the finish function
            if tool_calls is None:
                force_call_finish_func = True
                continue

            self.memory.add(msg_reasoning)

            # Acting based on the tool calls
            msg_response = await self._acting(tool_calls)
            if msg_response:
                return msg_response

        # Generate a response when exceeding the maximum iterations
        return self._summarizing()

    async def _reasoning(
        self,
        force_call_finish_func: bool = False,
    ) -> Tuple[Union[list[ToolUseBlock], None], Msg]:
        """The reasoning process of the agent.

        Args:
            force_call_finish_func (`bool`, defaults to `False`):
                Force to call the finish function, which is set to `True` when
                the last reasoning message fails to generate tool calls and
                `exit_reply_without_tool_calls` is `False`.

        Returns:
            `Tuple[Union[list[ToolUseBlock], None], Msg]`:
                Return the tool calls (`None` if empty) and reasoning message.
        """
        prompt = self.model.format(
            Msg(
                "system",
                self.sys_prompt,
                role="system",
            ),
            self.memory.get_memory(),
            # TODO: Support multi-agent mode in the future
            multi_agent_mode=False,
        )

        raw_response = self.model(
            prompt,
            tools=self.model.format_tools_json_schemas(
                self.service_toolkit.json_schemas,
            ),
            tool_choice=self._finish_function
            if force_call_finish_func
            else None,
        )

        if self.verbose:
            verbose_content_blocks = []
            if raw_response.text:
                verbose_content_blocks.append(TextBlock(type="text", text=raw_response.text))
            if raw_response.tool_calls:
                verbose_content_blocks.extend(raw_response.tool_calls)
            verbose_msg = Msg(self.name, verbose_content_blocks, role="assistant")
            await self.print(verbose_msg)

        # Prepare the content for the msg
        content: list[ContentBlock] = []
        if raw_response.text:
            content.append(TextBlock(type="text", text=raw_response.text))
        if raw_response.tool_calls:
            content.extend(raw_response.tool_calls)

        msg_reasoning = Msg(
            self.name,
            content,
            role="assistant",
        )

        return raw_response.tool_calls, msg_reasoning

    async def _acting(self, tool_calls: list[ToolUseBlock]) -> Union[None, Msg]:
        """The acting process of the agent, which takes a tool use block as
        input, execute the function and return a message if the
        `generate_response`
        function is called.

        Args:
            tool_calls (`ToolUseBlock`):
                The tool use block to be executed.

        Returns:
            `Union[None, Msg]`:
                Return `None` if the function is not `generate_response`,
                otherwise return a message to the user.
        """
        msg_response: Union[None, Msg] = None
        for tool_call in tool_calls:
            # Execute the function
            msg_execution = self.service_toolkit.parse_and_call_func(
                tool_call,
                tools_api_mode=True,
            )

            # Print and remember the execution result
            if self.verbose:
                await self.print(msg_execution)
            self.memory.add(msg_execution)

            # When calling finish function, return a message if no structured
            # output is required or have met the structured output
            if (
                tool_call["name"] == self._finish_function
                and self._current_structured_model is None
                or self._current_structured_output is not None
            ):
                # This message won't be record in the memory for duplicate
                # meaning with the tool result block
                msg_response = Msg(
                    self.name,
                    str(tool_call["input"]["response"]),
                    "assistant",
                    metadata=self._current_structured_output,
                )
                await self.print(msg_response)

        return msg_response

    def _summarizing(self) -> Msg:
        """Generate a response when the agent fails to solve the problem in
        the maximum iterations."""
        hint_msg = Msg(
            "user",
            "You have failed to generate response within the maximum "
            "iterations. Now respond directly by summarizing the current "
            "situation.",
            role="user"
        )

        # Generate a reply by summarizing the current situation
        prompt = self.model.format(
            self.memory.get_memory(),
            hint_msg,
        )
        res = self.model(prompt)
        verbose_msg = Msg(
            self.name,
            [TextBlock(type="text", text=res.text)],  # 确保 content 是内容块列表
            role="assistant",
        )
        self.print(verbose_msg)
        res_msg = Msg(self.name, res.text, "assistant")
        return res_msg

    def generate_response(
        self,
        response: str,  # pylint: disable=unused-argument
        **kwargs: Any,
    ) -> ServiceResponse:
        """Generate a response. You must call this function to interact with
        others (e.g., users).

        Args:
            response (`str`):
                The response to the user.
        """
        if self._current_structured_model:
            try:
                self._current_structured_model.model_validate(kwargs)
                self._current_structured_output = kwargs
            except ValidationError as e:
                return ServiceResponse(
                    status=ServiceExecStatus.ERROR,
                    content=f"Validation error: {e.errors()}",
                )

        return ServiceResponse(
            status=ServiceExecStatus.SUCCESS,
            content="Success",
        )

    def _clear_structured_output_hook(  # type: ignore
        self,  # pylint: disable=unused-argument
        *args,
        **kwargs,
    ) -> None:
        """Clear the structured output."""
        self._current_structured_output = None
        self._current_structured_model = None
        # Remove the structured output schema from the finish function
        self.service_toolkit.restore_function_schema(self._finish_function)
