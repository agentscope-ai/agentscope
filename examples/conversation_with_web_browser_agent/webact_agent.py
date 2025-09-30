# -*- coding: utf-8 -*-
# pylint: disable=C0301
"""The web act agent class, whose implementation refers to
the ReAct (https://arxiv.org/abs/2210.03629) algorithm and
WebVoyager (https://arxiv.org/abs/2401.13919)."""

from typing import Optional, Union, Sequence

from agentscope.agent import AgentBase
from agentscope.exception import ResponseParsingError
from agentscope.manager import FileManager
from agentscope.message import Msg
from agentscope.parsers import RegexTaggedContentParser
from agentscope.service import (
    ServiceToolkit,
    ServiceResponse,
    ServiceExecStatus,
    WebBrowser,
)

_SYSTEM_PROMPT = """You're an AI assistant named {name}.

## Your Target
Help the user to solve problems with the given web browsing commands.

## Key Guidelines You MUST follow
1 To input text, NO need to click textbox first, directly type content. After typing, the system automatically hits `ENTER` key. Sometimes you should click the search button to apply search filters. Try to use simple language when searching.
2 You must Distinguish between textbox and search button, don't type content into the button! If no textbox is found, you may need to click the search button first before the textbox is displayed.
3 Execute only one action per iteration.
4 STRICTLY Avoid repeating the same action if the webpage remains unchanged. You may have selected the wrong web element or numerical label. Continuous use of the Wait is also NOT allowed. Continuous use of the Scroll with no change of webpage is NOT allowed.
5 When a complex Task involves multiple questions or steps, select "ANSWER" only at the very end, after addressing all of these questions (steps). Flexibly combine your own abilities with the information in the web page. Double check the formatting requirements in the task when ANSWER.
6 When the user's task is a simple request, you should select "ANSWER" to complete the task.
7 When you are not sure whether you are making progress, you can select "ANSWER" and ask the user for more information.

## Web Browsing Guidelines
1 Don't interact with useless web elements like Login, Sign-in, donation that appear in Webpages. Pay attention to Key Web Elements like search textbox and menu.
2 Focus on the numerical labels in the TOP LEFT corner of each rectangle (element). Ensure you don't mix them up with other numbers (e.g. Calendar) on the page.
3 Focus on the date in task, you must look for results that match the date. It may be necessary to find the correct year, month and day at calendar.
4 Pay attention to the filter and sort functions on the page, which, combined with scroll, can help you solve conditions like 'highest', 'cheapest', 'lowest', 'earliest', etc. Try your best to find the answer that best fits the task.
5 You can use "Goto" if you need to goto a specific webpage.

"""  # noqa

_HINT_PROMPT = """You're browsing the URL "{url}". Please analyze the attached screenshot and give the thought and action.

{format_instruction}"""  # noqa


class WebActAgent(AgentBase):
    """An agent that performs reasoning and acting iteratively based on the
    web browser state at that time. The implementation refers to the ReAct
    (https://arxiv.org/abs/2210.03629) algorithm and
    WebVoyager (https://arxiv.org/abs/2401.13919).

    Note:
        This agent requires a vision model to handle the web page screenshot.
    """

    def __init__(
        self,
        name: str,
        model_config_name: str,
        max_iters: int = 10,
        verbose: bool = True,
    ) -> None:
        super().__init__(name=name, model_config_name=model_config_name)

        # Init the browser
        self.browser = WebBrowser()

        self.browser.action_visit_url("https://www.bing.com")

        # Init the service toolkit with the browser commands. Since they don't
        # require developers to specify parameters, we directly place them into
        # the toolkit.
        self.toolkit = ServiceToolkit()
        for func in self.browser.get_action_functions().values():
            self.toolkit.add(func)

        # Add `finish` function to the toolkit to allow agent to end
        # the reasoning-acting loop
        self.toolkit.add(self.finish)

        # Prepare system prompt, which includes the instruction for the tools
        self.sys_prompt = (
            _SYSTEM_PROMPT.format_map({"name": name})
            + self.toolkit.tools_instruction
        )
        self.memory.add(
            Msg(
                name="system",
                content=self.sys_prompt,
                role="system",
            ),
        )

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

        self.max_iters = max_iters
        self.verbose = verbose

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        """The reply method of the agent."""
        self.memory.add(x)

        for _ in range(self.max_iters):
            # Step 1: Reasoning: decide what function to call
            function_call = self._reasoning()

            if function_call is None:
                # Meet parsing error, skip acting to reason the parsing error,
                # which has been stored in memory
                continue

            # Return the response directly if calling `answer` function.
            # If "response" doesn't exist, we leave the error handling in the
            # acting step.
            if (
                function_call["function"] == "finish"
                and "response" in function_call
            ):
                return Msg(
                    self.name,
                    function_call["response"],
                    "assistant",
                )

            # Step 2: Acting: execute the function accordingly
            self._acting(function_call)

        # When exceeding the max iterations
        hint_msg = Msg(
            "system",
            "You have failed to generate response within the maximum "
            "iterations. Now respond directly by summarizing the current "
            "situation.",
            role="system",
            echo=self.verbose,
        )

        # Generate a reply by summarizing the current situation
        prompt = self.model.format(self.memory.get_memory(), hint_msg)
        res = self.model(prompt)
        self.speak(res.stream or res.text)
        res_msg = Msg(self.name, res.text, "assistant")
        return res_msg

    def _reasoning(self) -> Union[dict, None]:
        """The reasoning process of the agent.

        Returns:
            `Union[dict, None]`:
                Return `None` if meet parsing error, otherwise return the
                parsed function call dictionary.
        """

        # Mark the current interactive elements in the web page
        self.browser.set_interactive_marks()

        # After marking, take a screenshot and save it locally
        path_img = FileManager.get_instance().save_image(
            self.browser.page_screenshot,
        )

        # Assemble the prompt
        prompt = self.model.format(
            self.memory.get_memory(),
            # The observation message won't be stored in memory to avoid too
            # many images in prompt
            Msg(
                "user",
                _HINT_PROMPT.format(
                    url=self.browser.url,
                    format_instruction=self.parser.format_instruction,
                ),
                role="user",
                url=path_img,
                echo=self.verbose,
            ),
        )

        # Get the response from the model and print it out
        raw_response = self.model(prompt)
        self.speak(raw_response.stream or raw_response.text)
        self.memory.add(Msg(self.name, raw_response.text, role="assistant"))

        # Try to parse the response into function calling commands
        try:
            res = self.parser.parse(raw_response)
            return res.parsed
        except ResponseParsingError as e:
            # When failed to parse the response, return the error message to
            # the llm
            self.memory.add(Msg("system", str(e), "system"))
            return None

    def _acting(self, function_call: dict) -> None:
        """The acting process of the agent."""

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

        msg_res = self.toolkit.parse_and_call_func(
            formatted_function_call,
        )
        self.speak(msg_res)
        self.memory.add(msg_res)

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
