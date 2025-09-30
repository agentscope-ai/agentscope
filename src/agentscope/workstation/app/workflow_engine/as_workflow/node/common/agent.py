# -*- coding: utf-8 -*-
"""agent class"""
from typing import Optional, Union, Sequence

from agentscope.agent import AgentBase
from agentscope.service import (
    ServiceToolkit,
    ServiceResponse,
    ServiceExecStatus,
)
from agentscope.message import Msg
from agentscope.exception import ResponseParsingError
from agentscope.parsers import RegexTaggedContentParser


INSTRUCTION_PROMPT = """## What You Should Do:
1. First, analyze the current situation, and determine your goal.
2. Then, check if your goal is already achieved. If so, try to generate a response. Otherwise, think about how to achieve it with the help of provided tool functions.
3. Respond in the required format.

## Note:
1. Fully understand the tool functions and their arguments before using them.
2. You should decide if you need to use the tool functions, if not then return an empty list in "function" field.
3. Make sure the types and values of the arguments you provided to the tool functions are correct.
4. Don't take things for granted. For example, where you are, what's the time now, etc. You can try to use the tool functions to get information.
5. If the function execution fails, you should analyze the error and try to solve it.
"""  # noqa


class WorkflowAgent(AgentBase):
    """An agent class that implements the ReAct algorithm. More details refer
    to https://arxiv.org/abs/2210.03629.

    This is an example implementation of ReAct algorithm in AgentScope.
    We follow the idea within the paper, but the detailed prompt engineering
    maybe different. Developers are encouraged to modify the prompt to fit
    their own needs.

    Note:
        1. We use the "thought" field in the response to support
        Chain-of-Thought, which means the tool functions cannot use "thought"
        as their argument name.
        2. The function name "finish" is also a reserved name when using this
        agent, which will be used to end the reasoning-acting loop.
    """

    def __init__(
        self,
        name: str,
        service_toolkit: ServiceToolkit = ServiceToolkit(),
        sys_prompt: str = "You're a helpful assistant.",
        max_iters: int = 3,
        verbose: bool = True,
        use_memory: bool = True,
    ) -> None:
        """Initialize the ReAct agent with the given name, model config name
        and tools.

        Args:
            name (`str`):
                The name of the agent.
            sys_prompt (`str`):
                The system prompt of the agent.
            service_toolkit (`ServiceToolkit`):
                A `ServiceToolkit` object that contains the tool functions.
            max_iters (`int`, defaults to `10`):
                The maximum number of iterations of the reasoning-acting loops.
            verbose (`bool`, defaults to `True`):
                Whether to print the detailed information during reasoning and
                acting steps. If `False`, only the content in speak field will
                be print out.
        """
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            use_memory=use_memory,
        )
        self.use_memory = use_memory
        self.service_toolkit = service_toolkit
        self.service_toolkit.add(self.finish)

        self.verbose = verbose
        self.max_iters = max_iters

        if not sys_prompt.endswith("\n"):
            sys_prompt = sys_prompt + "\n"

        self.sys_prompt = "\n".join(
            [
                # The brief intro of the role and target
                sys_prompt,
                # The instruction prompt for tools
                self.service_toolkit.tools_instruction,
                # The detailed instruction prompt for the agent
                INSTRUCTION_PROMPT,
            ],
        )

        # Put sys prompt into memory
        self.memory.add(Msg("system", self.sys_prompt, role="system"))

        # Initialize a parser object to formulate the response from the model
        self.parser = RegexTaggedContentParser(
            format_instruction="""Respond with specific tags as outlined below:
<thought>{what you thought}</thought>
<function>{the function name you want to call}</function>
<{argument name}>{argument value}</{argument name}>
<{argument name}>{argument value}</{argument name}>
...""",  # noqa
            try_parse_json=True,
            required_keys=["thought", "function"],
        )

    @property
    def sys_prompt(self) -> str:
        """Get the system prompt."""
        return self._sys_prompt

    @sys_prompt.setter
    def sys_prompt(self, value: str) -> None:
        """Set the system prompt."""
        self._sys_prompt = value

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """The reply method of the agent."""
        if self.use_memory:
            self.memory.add(x)

        for _ in range(self.max_iters):
            # Step 1: Reasoning: decide what function to call
            function_call = self._reasoning(x)

            if function_call is None:
                # Meet parsing error, skip acting to reason the parsing error,
                # which has been stored in memory
                continue

            # Return the response directly if calling `finish` function.
            # If the argument "response" doesn't exist, we leave the error
            # handling in the acting step.
            if (
                function_call["function"] == "finish"
                and "response" in function_call
            ):
                return Msg(
                    self.name,
                    function_call["response"],
                    "assistant",
                    # echo=not self.verbose,
                )

            # Step 2: Acting: execute the function accordingly
            self._acting(function_call)

        # When exceeding the max iterations
        hint_msg = Msg(
            "system",
            "You have failed to generate response within the maximum "
            "iterations. Now respond directly.",
            role="system",
            # echo=self.verbose,
        )

        # Generate a reply by summarizing the current situation
        prompt = self.model.format(
            self.memory.get_memory(),
            None if self.use_memory else x,
            hint_msg,
        )
        res = self.model(prompt)
        # self.speak(res.stream or res.text)
        res_msg = Msg(self.name, res.text, "assistant")
        return res_msg

    def _reasoning(
        self,
        x: Optional[Union[Msg, Sequence[Msg]]],
    ) -> Union[dict, None]:
        """The reasoning process of the agent.

        Returns:
            `Union[dict, None]`:
                Return `None` if meet parsing error, otherwise return the
                parsed function call dictionary.
        """
        # Assemble the prompt
        prompt = self.model.format(
            self.memory.get_memory(),
            None if self.use_memory else x,
            # Hint LLM how to respond without putting hint message into memory
            Msg(
                "system",
                self.parser.format_instruction,
                role="system",
                # echo=self.verbose,
            ),
        )

        # Get the response from the model and print it out
        raw_response = self.model(prompt)
        # if self.verbose:
        #     self.speak(raw_response.stream or raw_response.text)
        if self.use_memory:
            self.memory.add(
                Msg(self.name, raw_response.text, role="assistant"),
            )

        # Try to parse the response into function calling commands
        try:
            res = self.parser.parse(raw_response)
            return res.parsed

        except ResponseParsingError as e:
            # When failed to parse the response, return the error message to
            # the llm
            if self.use_memory:
                self.memory.add(
                    Msg(
                        "system",
                        str(e),
                        "system",
                        # echo=self.verbose
                    ),
                )
            return None

    def _acting(self, function_call: dict) -> None:
        """The acting process of the agent."""

        # Assemble the function call into the format that the toolkit requires
        function_name = function_call["function"]
        arguments = {
            k: v
            for k, v in function_call.items()
            if k not in ["function", "thought"]
        }

        formatted_function_call = [
            {
                "name": function_name,
                "arguments": arguments,
            },
        ]

        # The execution message, may be execution output or error information
        msg_execution = self.service_toolkit.parse_and_call_func(
            formatted_function_call,
        )
        # if self.verbose:
        #     self.speak(msg_execution)
        if self.use_memory:
            self.memory.add(msg_execution)

    @staticmethod
    def finish(response: str) -> ServiceResponse:
        """Finish reasoning and generate a response to the user.

        Note:
            The function won't be executed, actually.

        Args:
            response (`str`):
                The response to the user.
        """
        return ServiceResponse(
            status=ServiceExecStatus.SUCCESS,
            content=response,
        )
