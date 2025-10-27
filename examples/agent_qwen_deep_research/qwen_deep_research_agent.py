# -*- coding: utf-8 -*-
"""Qwen Deep Research Agent"""
# pylint: disable=line-too-long, too-many-branches, too-many-statements

import os
from typing import Any, Optional, Union, Sequence

import dashscope
from dashscope.api_entities.dashscope_response import GenerationResponse

from agentscope import logger
from agentscope.agent import AgentBase
from agentscope.memory import MemoryBase, InMemoryMemory
from agentscope.message import Msg


class QwenDeepResearchAgent(AgentBase):
    """
    Deep Research Agent based on Qwen-Deep-Research model

    This agent supports a two-step research process:
    1. Clarification: Analyzes the question and asks follow-up questions
    2. Deep research: Executes the complete research process

    Args:
        name (str):
            Agent name
        api_key (str, optional):
            DashScope API Key, defaults to environment variable
        memory (MemoryBase, optional):
            Memory component
        verbose (bool):
            Whether to display detailed process, defaults to True
    """

    def __init__(
        self,
        name: str,
        api_key: Optional[str] = None,
        memory: Optional[MemoryBase] = None,
        verbose: bool = False,
    ):
        """Initialize QwenDeepResearchAgent Agent"""

        super().__init__()

        self.name = name

        # Configure API Key
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "The DASHSCOPE_API_KEY environment variable is not set.",
            )

        self.model_name = "qwen-deep-research"
        self.verbose = verbose
        self.memory = memory or InMemoryMemory()

    async def reply(
        self,
        x: Optional[Union[Msg, Sequence[Msg]]] = None,
    ) -> Msg:
        """
        Process input message and return reply (asynchronous version)

        Args:
            x: Input message, can be a single Msg or a list of Msg

        Returns:
            Msg: Agent's reply message
        """

        # Process input message
        if x is None:
            logger.warning("Received empty message")
            return Msg(name=self.name, content="", role="assistant")

        # Convert to message list
        if isinstance(x, Msg):
            msgs = [x]
        else:
            msgs = list(x)

        # Add to memory
        for msg in msgs:
            await self.memory.add(msg)

        # Check if clarification is needed
        memory_list = await self.memory.get_memory()
        user_msgs = [m for m in memory_list if m.role == "user"]

        if len(user_msgs) == 1:
            # Step 1: Clarification
            logger.info("[%s] Starting clarification ...", self.name)
            content = await self._call_model(step_name="Clarification")

            response_msg = Msg(
                name=self.name,
                content=content,
                role="assistant",
                metadata={
                    "phase": "clarification",
                    "requires_user_response": True,
                },
            )
        else:
            # Step 2: Deep Research
            logger.info("[%s] Starting deep research ...", self.name)
            content = await self._call_model(step_name="Deep Research")

            response_msg = Msg(
                name=self.name,
                content=content,
                role="assistant",
                metadata={
                    "phase": "deep_research",
                    "requires_user_response": False,
                },
            )

        await self.memory.add(response_msg)

        return response_msg

    async def _call_model(self, step_name: str) -> str:
        """
        Call qwen-deep-research model

        Args:
            step_name: step name

        Returns:
            str: Model response content
        """

        if self.verbose:
            logger.info("\n%s", "=" * 50)
            logger.info("  %s", step_name)
            logger.info("%s", "=" * 50)

        memory_list = await self.memory.get_memory()
        messages = []
        for msg in memory_list:
            messages.append(
                {
                    "role": msg.role,
                    "content": msg.content,
                },
            )
        try:
            responses = await dashscope.AioGeneration.call(
                api_key=self.api_key,
                model=self.model_name,
                messages=messages,
                stream=True,
                request_timeout=1800,  # Seconds
            )
            return await self._process_responses(responses)
        except Exception as e:
            err_msg = f"An error occurred when calling the API: {e}"
            logger.error(err_msg)
            return err_msg

    async def _process_responses(
        self,
        responses: GenerationResponse,
    ) -> str:
        """
        Process model streaming responses (asynchronous version)

        Args:
            responses: Model response stream
            step_name: Step name

        Returns:
            str: Model response content
        """

        current_phase = None
        current_status = None
        phase_content = ""
        research_goal = ""
        keepalive_shown = False
        references = []

        async for response in responses:
            # Check response status
            if (
                hasattr(response, "status_code")
                and response.status_code != 200
            ):
                error_msg = f"HTTP status code: {response.status_code}"
                if hasattr(response, "code"):
                    error_msg += f", Error code: {response.code}"
                if hasattr(response, "message"):
                    error_msg += f", Error message: {response.message}"
                logger.error(error_msg)
                continue

            if hasattr(response, "output") and response.output:
                message = response.output.get("message", {})
                phase = message.get("phase")
                content = message.get("content", "")
                status = message.get("status")
                extra = message.get("extra", {})

                # Phase change detection
                if phase != current_phase:
                    if current_phase and phase_content and self.verbose:
                        logger.info("\n✓ %s phase completed", current_phase)

                    current_phase = phase
                    phase_content = ""
                    keepalive_shown = False

                    if phase and phase != "KeepAlive" and self.verbose:
                        logger.info("\n▶ Entering %s phase", phase)
                        if phase == "answer":
                            references = extra.get("deep_research", {}).get(
                                "references",
                                [],
                            )

                # Process WebResearch phase
                if phase == "WebResearch" and self.verbose:
                    research_goal = self._handle_web_research_phase(
                        status,
                        extra,
                        research_goal,
                    )

                if content:
                    phase_content += content

                    # Display content
                    if self.verbose:
                        print(content, end="", flush=True)

                # Display status changes
                if status:
                    if (
                        status != current_status
                        and status != "typing"
                        and self.verbose
                    ):
                        self._log_status(status)
                    current_status = status

                # Token usage statistics
                if status == "finished":
                    self._log_usage(response)
                    if self.verbose:
                        logger.info("\n✓ %s phase completed", current_phase)
                    if phase == "answer":
                        if len(references) > 0:
                            reference_links = []
                            list_links = []
                            for i, ref in enumerate(references):
                                title = ref["title"]
                                url = ref["url"]
                                reference_links.append(
                                    f'[{i + 1}]: {url} "{title}"',
                                )
                                list_links.append(f"{i + 1}. [{title}]({url})")
                            phase_content = (
                                phase_content
                                + "\n\n## References\n\n"
                                + "\n".join(list_links)
                                + "\n\n"
                                + "\n".join(reference_links)
                            )
                            break

                # Process KeepAlive
                if phase == "KeepAlive":
                    if not keepalive_shown and self.verbose:
                        logger.info("\n⏳ Preparing for the next phase...")
                        keepalive_shown = True
                    continue
        return phase_content

    def _handle_web_research_phase(
        self,
        status: str,
        extra: dict,
        research_goal: str,
    ) -> str:
        web_sites = []
        if extra.get("deep_research", {}).get("research"):
            research_info = extra["deep_research"]["research"]

            # handle research goal
            if status == "streamingQueries":
                if "researchGoal" in research_info:
                    goal = research_info["researchGoal"]
                    if goal:
                        research_goal += goal

            # handle web site search results
            elif status == "streamingWebResult":
                if research_goal != "":
                    logger.info("\n🎯 Research Goal: %s", research_goal)
                    research_goal = ""
                if "webSites" in research_info:
                    sites = research_info["webSites"]
                    if sites and sites != web_sites:
                        # web_sites.clear()
                        web_sites.extend(sites)
                        msg = (
                            f"\n🔍 Found {len(sites)} relevant websites:\n"
                            + "\n".join(
                                f"  {i + 1}. {site.get('title', 'No title')}\n"
                                f"     {site.get('url', 'No link')}"
                                for i, site in enumerate(sites)
                            )
                        )
                        logger.info(msg)
            # handle finished status
            elif status == "WebResultFinished":
                logger.info(
                    "\n✓ Web search completed, found %s reference sources",
                    len(web_sites),
                )

        return research_goal

    def _log_status(self, status: str) -> None:
        """log status information"""

        status_desc = {
            "streamingQueries": "Generating research goals and search queries "
            "(WebResearch phase)",
            "streamingWebResult": "Performing search, web page reading, and "
            "code execution (WebResearch phase)",
            "WebResultFinished": "Web search phase completed (WebResearch "
            "phase)",
        }

        if status in status_desc:
            logger.info("\n📊 %s", status_desc[status])

    def _log_usage(self, response: GenerationResponse) -> None:
        """log Token usage information"""

        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            if self.verbose:
                print("\n")
                logger.info(
                    "\n📈 Token usage - input: %s output: %s",
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive the given message(s) without generating a reply.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message(s) to be observed.
        """
        # Simply add the message(s) to memory without generating a reply
        if msg is not None:
            if isinstance(msg, Msg):
                await self.memory.add(msg)
            else:
                for m in msg:
                    await self.memory.add(m)

    async def handle_interrupt(self, *args: Any, **kwargs: Any) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else.

        Returns:
            Msg: The interrupt message.
        """
        # Return a message indicating the interruption
        # pylint: disable=unused-argument
        return Msg(
            name=self.name,
            content="Operation was interrupted.",
            role="assistant",
        )

    async def reset_memory(self) -> None:
        """reset memory"""
        await self.memory.clear()
