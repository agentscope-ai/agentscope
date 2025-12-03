# -*- coding: utf-8 -*-
"""Browser Agent"""
# pylint: disable=W0212

import re
import uuid
from typing import Optional, Any

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import (
    Msg,
    ToolUseBlock,
    TextBlock,
)
from agentscope.model import ChatModelBase
from agentscope.tool import Toolkit
from agentscope.token import TokenCounterBase, OpenAITokenCounter

_BROWSER_AGENT_DEFAULT_SYS_PROMPT = (
    "You are a helpful browser automation assistant. "
    "You can navigate websites, take screenshots, and interact with web pages."
    "Always describe what you see and plan your next steps clearly. "
    "When taking actions, explain what you're doing and why."
)
_BROWSER_AGENT_REASONING_PROMPT = (
    "You are browsing the current website. "
    "The snapshot (and screenshot) of the current webpage is (are) given "
    "below. Since you can only view the latest webpage, "
    "you must promptly summarize current status, record required data, "
    "and plan your next steps."
)


async def browser_agent_default_url_pre_reply(
    self: "BrowserAgent",  # pylint: disable=W0613
    *args: Any,  # pylint: disable=W0613
    **kwargs: Any,  # pylint: disable=W0613
) -> None:
    """Navigate to start URL if this is the first interaction"""
    if self.start_url and not self._has_initial_navigated:
        await self._navigate_to_start_url()
        self._has_initial_navigated = True


async def browser_agent_summarize_mem_pre_reasoning(
    self: "BrowserAgent",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Summarize memory if too long"""
    mem_len = await self.memory.size()
    if mem_len > self.max_memory_length:
        await self._memory_summarizing()


async def browser_agent_observe_pre_reasoning(
    self: "BrowserAgent",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Get a snapshot in text before reasoning"""
    snapshot_msg = await self._get_snapshot_in_text()
    await self.memory.add(snapshot_msg)


async def browser_agent_remove_observation_post_reasoning(
    self: "BrowserAgent",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """Remove the snapshot msg after reasoning"""
    mem_len = await self.memory.size()
    if mem_len >= 2:
        await self.memory.delete(mem_len - 2)


async def browser_agent_post_acting_clean_content(
    self: "BrowserAgent",  # pylint: disable=W0613
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Hook func for cleaning the messy return after action.
    Observation will be done before reasoning steps.
    """
    mem_msgs = await self.memory.get_memory()
    mem_length = await self.memory.size()
    if len(mem_msgs) == 0:
        return
    last_output_msg = mem_msgs[-1]
    for i, b in enumerate(last_output_msg.content):
        if b["type"] == "tool_result":
            for j, return_json in enumerate(b.get("output", [])):
                if isinstance(return_json, dict) and "text" in return_json:
                    last_output_msg.content[i]["output"][j][
                        "output"
                    ] = self._filter_execution_text(return_json["text"])
    await self.memory.delete(mem_length - 1)
    await self.memory.add(last_output_msg)


class BrowserAgent(ReActAgent):
    """
    Browser Agent that extends ReActAgent with browser-specific capabilities.

    The agent leverages MCP (Model Context Protocol) servers to access browser
    tools with Playwright, enabling sophisticated web automation tasks.

    Example:
        .. code-block:: python

            agent = BrowserAgent(
                name="web_navigator",
                model=my_chat_model,
                formatter=my_formatter,
                memory=my_memory,
                toolkit=browser_toolkit,
                start_url="https://example.com"
            )

            response = await agent.reply("Search for Python tutorials")
    """

    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        memory: MemoryBase,
        toolkit: Toolkit,
        sys_prompt: str = _BROWSER_AGENT_DEFAULT_SYS_PROMPT,
        max_iters: int = 50,
        start_url: Optional[str] = "https://www.google.com",
        reasoning_prompt: str = _BROWSER_AGENT_REASONING_PROMPT,
        token_counter: TokenCounterBase = OpenAITokenCounter("gpt-4o"),
        max_mem_length: int = 20,
    ) -> None:
        """Initialize the Browser Agent.

        Args:
            name (str):
                The unique identifier name for the agent instance.
            model (ChatModelBase):
                The chat model used for generating responses and reasoning.
            formatter (FormatterBase):
                The formatter used to convert messages into the required format
                 for the model API.
            memory (MemoryBase):
                The memory component used to store and retrieve dialogue
                history.
            toolkit (Toolkit):
                A toolkit object containing the browser tool functions and
                utilities.
            sys_prompt (str, optional):
                The system prompt that defines the agent's behavior and
                personality.
                Defaults to _BROWSER_AGENT_DEFAULT_SYS_PROMPT.
            max_iters (int, optional):
                The maximum number of reasoning-acting loop iterations.
                Defaults to 50.
            start_url (Optional[str], optional):
                The initial URL to navigate to when the agent starts.
                Defaults to "https://www.google.com".
            reasoning_prompt (str, optional):
                The prompt used during the reasoning phase to guide
                decision-making.
                Defaults to _BROWSER_AGENT_REASONING_PROMPT.

        Returns:
            None
        """
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            toolkit=toolkit,
            max_iters=max_iters,
        )

        self.start_url = start_url
        self._has_initial_navigated = False
        self.reasoning_prompt = reasoning_prompt
        self.max_memory_length = max_mem_length
        self.token_estimator = token_counter

        self.register_instance_hook(
            "pre_reply",
            "browser_agent_default_url_pre_reply",
            browser_agent_default_url_pre_reply,
        )

        self.register_instance_hook(
            "pre_reasoning",
            "browser_agent_summarize_mem_pre_reasoning",
            browser_agent_summarize_mem_pre_reasoning,
        )

        self.register_instance_hook(
            "pre_reasoning",
            "browser_agent_observe_pre_reasoning",
            browser_agent_observe_pre_reasoning,
        )

        self.register_instance_hook(
            "post_reasoning",
            "browser_agent_remove_observation_post_reasoning",
            browser_agent_remove_observation_post_reasoning,
        )

        self.register_instance_hook(
            "post_acting",
            "browser_agent_post_acting_clean_content",
            browser_agent_post_acting_clean_content,
        )

    async def _navigate_to_start_url(self) -> None:
        """
        Navigate to the specified start URL using the browser_navigate tool.

        This method is automatically called during the first interaction to
        navigate to the configured start URL. It executes the browser
        navigation tool and processes the response to ensure the
        initial page is loaded.

        Returns:
            None
        """
        tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),
            type="tool_use",
            name="browser_navigate",
            input={"url": self.start_url},
        )

        # Execute the navigation tool
        await self.toolkit.call_tool_function(tool_call)

    async def _get_snapshot_in_text(self) -> Msg:
        """Capture a text-based snapshot of the current webpage content.

        This method uses the browser_snapshot tool to retrieve the current
        webpage content in text format, which is used during the reasoning
        phase to provide context about the current browser state.

        Returns:
            str: A text representation of the current webpage content,
                including elements, structure, and visible text.

        Note:
            This method is called automatically during the reasoning phase and
            provides essential context for decision-making about next actions.
        """
        snapshot_tool_call = ToolUseBlock(
            type="tool_use",
            id=str(uuid.uuid4()),  # Generate a unique ID for the tool call
            name="browser_snapshot",
            input={},  # No parameters required for this tool
        )
        snapshot_response = await self.toolkit.call_tool_function(
            snapshot_tool_call,
        )
        snapshot_str = ""
        async for chunk in snapshot_response:
            snapshot_str = chunk.content[0]["text"]

        msg_observe = Msg(
            "user",
            content=[
                TextBlock(
                    type="text",
                    text=self.reasoning_prompt + "\n" + snapshot_str,
                ),
            ],
            role="user",
        )

        return msg_observe

    async def _memory_summarizing(self) -> None:
        """Summarize the current memory content to prevent context overflow.

        This method is called periodically to condense the conversation history
        by generating a summary of progress and maintaining only essential
        information. It preserves the initial user question and creates a
        concise summary of what has been accomplished and what remains to be
        done.

        Returns:
            None

        Note:
            This method is automatically called every 10 iterations to manage
            memory usage and maintain context relevance. The summarization
            helps prevent token limit issues while preserving important task
            context.
        """
        # Extract the initial user question
        initial_question = None
        memory_msgs = await self.memory.get_memory()
        for msg in memory_msgs:
            if msg.role == "user":
                initial_question = msg.content
                break

        # Generate a summary of the current progress
        hint_msg = Msg(
            "user",
            (
                "Summarize the current progress and outline the next steps "
                "for this task. Your summary should include:\n"
                "1. What has been completed so far.\n"
                "2. What key information has been found.\n"
                "3. What remains to be done.\n"
                "Ensure that your summary is clear, concise, and t"
                "hat no tasks are repeated or skipped."
            ),
            role="user",
        )

        # Format the prompt for the model
        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs,
                hint_msg,
            ],
        )

        # Call the model to generate the summary
        res = await self.model(prompt)

        # Handle response
        summary_text = ""
        if self.model.stream:
            async for content_chunk in res:
                summary_text = content_chunk.content[0]["text"]
        else:
            summary_text = res.content[0]["text"]

        # Update the memory with the summarized content
        summarized_memory = []
        if initial_question:
            summarized_memory.append(
                Msg("user", initial_question, role="user"),
            )
        summarized_memory.append(
            Msg(self.name, summary_text, role="assistant"),
        )

        # Clear and reload memory
        await self.memory.clear()
        for msg in summarized_memory:
            await self.memory.add(msg)

    @staticmethod
    def _filter_execution_text(
        text: str,
        keep_page_state: bool = False,
    ) -> str:
        """
        Filter and clean browser tool execution output to remove verbose
        content.

        This utility method removes unnecessary verbose content from browser
        tool responses, including JavaScript code blocks, console messages,
        and YAML content that can overwhelm the context window without
        providing useful information.

        Args:
            text (str):
                The raw execution text from browser tools that
                needs to be filtered.
            keep_page_state (bool, optional):
                Whether to preserve page state information
                including URL and YAML content. Defaults to False.

        Returns:
            str: The filtered execution text.
        """
        if not keep_page_state:
            # Remove Page Snapshot and YAML content
            text = re.sub(r"- Page URL.*", "", text, flags=re.DOTALL)
            text = re.sub(r"```yaml.*?```", "", text, flags=re.DOTALL)
        # Remove JavaScript code blocks
        text = re.sub(r"```js.*?```", "", text, flags=re.DOTALL)
        # Remove console messages section that can be very verbose
        # (between "### New console messages" and "### Page state")
        text = re.sub(
            r"### New console messages.*?(?=### Page state)",
            "",
            text,
            flags=re.DOTALL,
        )
        # Trim leading/trailing whitespace
        return text.strip()
