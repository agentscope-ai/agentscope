# -*- coding: utf-8 -*-
"""Tool memory implementation using ReMe library.

This module provides a tool memory implementation that integrates
with the ReMe library to record tool execution results and retrieve
tool usage guidelines.

"""
from typing import Any

from ._reme_long_term_memory_base import ReMeLongTermMemoryBase
from ..._logging import logger
from ...message import Msg, TextBlock
from ...tool import ToolResponse


class ReMeToolLongTermMemory(ReMeLongTermMemoryBase):
    """Tool memory implementation using ReMe library.

    Tool memory records tool execution results and generates usage
    guidelines from the execution history.

    """

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        **kwargs: Any,
    ) -> ToolResponse:
        """Record tool execution results to build tool usage patterns.

        Record tool execution results to build a knowledge base of tool
        usage patterns.

        Use this function after successfully using tools to capture
        execution details, results, and performance metrics. Over time,
        this builds comprehensive usage guidelines and best practices
        for each tool.

        When to record:

        - After successfully executing any tool
        - After tool failures (to learn what doesn't work)
        - When discovering effective parameter combinations
        - After noteworthy tool usage patterns

        What to record: Each tool execution should include complete
        execution details.

        Args:
            thinking (`str`):
                Your reasoning about why this tool execution is worth
                recording. Mention what worked well, what could be
                improved, or lessons learned.
            content (`list[str]`):
                List of JSON strings, each representing a tool execution.
                Each JSON must have these fields:
                - create_time: Timestamp in format "YYYY-MM-DD HH:MM:SS"
                - tool_name: Name of the tool executed
                - input: Input parameters as a dict
                - output: Tool's output as a string
                - token_cost: Token cost (integer)
                - success: Whether execution succeeded (boolean)
                - time_cost: Execution time in seconds (float)

                Example: '{"create_time": "2024-01-01 10:00:00",
                "tool_name": "search", "input": {"query": "Python"},
                "output": "Found 10 results", "token_cost": 100,
                "success": true, "time_cost": 1.2}'
            **kwargs (`Any`):
                Additional keyword arguments for the recording operation.

        Returns:
            `ToolResponse`:
                Confirmation message with number of executions recorded
                and guidelines generated.
        """
        logger.info(
            "[ReMeToolMemory] Entering record_to_memory - "
            "thinking: %s, content: %s, kwargs: %s",
            thinking,
            content,
            kwargs,
        )

        if not self._app_started:
            raise RuntimeError(
                "ReMeApp context not started. "
                "Please use 'async with' to initialize the app.",
            )

        try:
            import json

            # Parse each content item as a tool_call_result
            tool_call_results = []
            tool_names_set = set()

            for item in content:
                try:
                    # Parse JSON string to dict
                    tool_call_result = json.loads(item)
                    tool_call_results.append(tool_call_result)

                    # Track tool names for summary
                    if "tool_name" in tool_call_result:
                        tool_names_set.add(tool_call_result["tool_name"])

                except json.JSONDecodeError as e:
                    # Skip invalid JSON items
                    import warnings

                    warnings.warn(
                        f"Failed to parse tool call result JSON: {item}. "
                        f"Error: {str(e)}",
                    )
                    continue

            if not tool_call_results:
                return ToolResponse(
                    content=[
                        TextBlock(
                            type="text",
                            text="No valid tool call results to record.",
                        ),
                    ],
                )

            # First, add the tool call results
            await self.app.async_execute(
                name="add_tool_call_result",
                workspace_id=self.workspace_id,
                tool_call_results=tool_call_results,
                **kwargs,
            )

            # Then, summarize the tool memory for the affected tools
            if tool_names_set:
                tool_names_list = list(tool_names_set)
                await self.app.async_execute(
                    name="summary_tool_memory",
                    workspace_id=self.workspace_id,
                    tool_names=tool_names_list,
                    **kwargs,
                )

            num_results = len(tool_call_results)
            summary_text = (
                f"Successfully recorded {num_results} tool execution "
                f"result{'s' if num_results > 1 else ''} and generated "
                f"usage guidelines."
            )

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=summary_text,
                    ),
                ],
            )

        except Exception as e:
            logger.exception("Error recording tool memory: %s", str(e))
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error recording tool memory: {str(e)}",
                    ),
                ],
            )

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        limit: int = 5,
        **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve usage guidelines and best practices for tools.

        Retrieve usage guidelines and best practices for specific tools.

        .. note:: You should call this function BEFORE using a tool,
         especially if you're uncertain about its proper usage or want to
         follow established best practices. This retrieves synthesized
         guidelines based on past tool executions.

        Use this when:

        - About to use a tool and want to know the best practices
        - Uncertain about tool parameters or usage patterns
        - Want to learn from past successful/failed tool executions
        - User asks "how should I use this tool?" or "what's the best
          way to..."
        - Need to understand tool performance characteristics or
          limitations

        Benefits of retrieving first:

        - Learn from accumulated tool usage experience
        - Avoid common mistakes and pitfalls
        - Use optimal parameter combinations
        - Understand tool performance and cost characteristics
        - Follow established best practices

        Args:
            keywords (`list[str]`):
                List of tool names to retrieve guidelines for. Use the
                exact tool names. Examples: ["search"],
                ["database_query", "cache_get"], ["api_call"].
            limit (`int`, optional):
                The maximum number of memories to retrieve per search, i.e.,
                the number of memories to retrieve for each keyword. Defaults
                to 5.
            **kwargs (`Any`):
                Additional keyword arguments for the retrieval operation.

        Returns:
            `ToolResponse`:
                Retrieved usage guidelines and best practices for the
                specified tools. If no guidelines exist yet, you'll
                receive a message indicating that.
        """
        logger.info(
            "[ReMeToolMemory] Entering retrieve_from_memory - "
            "keywords: %s, kwargs: %s",
            keywords,
            kwargs,
        )

        if not self._app_started:
            raise RuntimeError(
                "ReMeApp context not started. "
                "Please use 'async with' to initialize the app.",
            )

        try:
            # Join all tool names with comma
            tool_names = ",".join(keywords)

            # Retrieve tool guidelines for all tools at once
            result = await self.app.async_execute(
                name="retrieve_tool_memory",
                workspace_id=self.workspace_id,
                tool_names=tool_names,
                top_k=limit,
                **kwargs,
            )

            # Extract the answer from the result
            answer = result.get("answer", "")
            if answer:
                combined_text = answer
            else:
                combined_text = f"No tool guidelines found for: {tool_names}"

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=combined_text,
                    ),
                ],
            )

        except Exception as e:
            logger.exception("Error retrieving tool memory: %s", str(e))
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error retrieving tool memory: {str(e)}",
                    ),
                ],
            )

    def _extract_content_from_messages(self, msg_list: list[Msg]) -> list[str]:
        """Extract content strings from messages.

        Args:
            msg_list (`list[Msg]`):
                List of messages to extract content from.

        Returns:
            `list[str]`:
                List of extracted content strings.
        """
        content_list = []
        for msg in msg_list:
            if isinstance(msg.content, str):
                content_list.append(msg.content)
            elif isinstance(msg.content, list):
                content_list.extend(
                    self._extract_text_from_blocks(msg.content),
                )
        return content_list

    def _extract_text_from_blocks(self, blocks: list) -> list[str]:
        """Extract text from content blocks.

        Args:
            blocks (`list`):
                List of content blocks.

        Returns:
            `list[str]`:
                List of extracted text strings.
        """
        texts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return texts

    def _parse_tool_call_results(
        self,
        content_list: list[str],
    ) -> tuple[list[dict], set[str]]:
        """Parse JSON content strings into tool call results.

        Args:
            content_list (`list[str]`):
                List of JSON strings to parse.

        Returns:
            `tuple[list[dict], set[str]]`:
                Tuple of (tool_call_results, tool_names_set).
        """
        import json
        import warnings

        tool_call_results = []
        tool_names_set = set()

        for item in content_list:
            try:
                tool_call_result = json.loads(item)
                tool_call_results.append(tool_call_result)
                if "tool_name" in tool_call_result:
                    tool_names_set.add(tool_call_result["tool_name"])
            except json.JSONDecodeError as e:
                warnings.warn(
                    f"Failed to parse tool call result JSON: {item}. "
                    f"Error: {str(e)}",
                )

        return tool_call_results, tool_names_set

    async def record(
        self,
        msgs: list[Msg | None],
        **kwargs: Any,
    ) -> None:
        """Record the content to the tool memory.

        This method extracts content from messages and treats them as
        JSON strings representing tool_call_results, similar to
        record_to_memory.

        Args:
            msgs (`list[Msg | None]`):
                The messages to record to memory. Each message's content
                should be a JSON string or list of JSON strings
                representing tool_call_results.
            **kwargs (`Any`):
                Additional keyword arguments for the recording.
        """
        if isinstance(msgs, Msg):
            msgs = [msgs]

        # Filter out None
        msg_list = [_ for _ in msgs if _]
        if not msg_list:
            return

        if not all(isinstance(_, Msg) for _ in msg_list):
            raise TypeError(
                "The input messages must be a list of Msg objects.",
            )

        if not self._app_started:
            raise RuntimeError(
                "ReMeApp context not started. "
                "Please use 'async with' to initialize the app.",
            )

        try:
            # Extract content from messages and parse as tool_call_results
            content_list = self._extract_content_from_messages(msg_list)
            if not content_list:
                return

            # Parse each content item as a tool_call_result
            tool_call_results, tool_names_set = self._parse_tool_call_results(
                content_list,
            )
            if not tool_call_results:
                return

            # First, add the tool call results
            await self.app.async_execute(
                name="add_tool_call_result",
                workspace_id=self.workspace_id,
                tool_call_results=tool_call_results,
                **kwargs,
            )

            # Then, summarize the tool memory for the affected tools
            if tool_names_set:
                tool_names_list = list(tool_names_set)
                await self.app.async_execute(
                    name="summary_tool_memory",
                    workspace_id=self.workspace_id,
                    tool_names=tool_names_list,
                    **kwargs,
                )

        except Exception as e:
            # Log the error but don't raise to maintain compatibility
            logger.exception(
                "Error recording tool messages to memory: %s",
                str(e),
            )
            import warnings

            warnings.warn(
                f"Error recording tool messages to memory: {str(e)}",
            )

    def _extract_tool_names_from_message(self, msg: Msg) -> str:
        """Extract tool names from a message.

        Args:
            msg (`Msg`):
                Message to extract tool names from.

        Returns:
            `str`:
                Extracted tool names as a string.
        """
        if isinstance(msg.content, str):
            return msg.content

        if isinstance(msg.content, list):
            content_parts = []
            for block in msg.content:
                if isinstance(block, dict) and "text" in block:
                    content_parts.append(block["text"])
            return " ".join(content_parts)

        return ""

    def _format_retrieve_result(self, result: Any) -> str:
        """Format the retrieve result into a string.

        Args:
            result (`Any`):
                Result from the retrieve operation.

        Returns:
            `str`:
                Formatted result string.
        """
        if isinstance(result, dict) and "answer" in result:
            return result["answer"]
        if isinstance(result, str):
            return result
        return str(result)

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """Retrieve tool guidelines from memory.

        Retrieve tool guidelines from memory based on message content.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message containing tool names or queries to
                retrieve guidelines for.
            limit (`int`, optional):
                The maximum number of memories to retrieve per search, i.e.,
                the number of memories to retrieve for the message. If the
                message is a list of messages, the limit applies to each
                message. If the message is a single message, the limit is the
                total number of memories to retrieve for that message. Defaults
                to 5.
            **kwargs (`Any`):
                Additional keyword arguments.

        Returns:
            `str`:
                The retrieved tool guidelines as a string.
        """
        if msg is None:
            return ""

        if isinstance(msg, Msg):
            msg = [msg]

        if not isinstance(msg, list) or not all(
            isinstance(_, Msg) for _ in msg
        ):
            raise TypeError(
                "The input message must be a Msg or a list of Msg objects.",
            )

        if not self._app_started:
            raise RuntimeError(
                "ReMeApp context not started. "
                "Please use 'async with' to initialize the app.",
            )

        try:
            # Extract tool names from the last message
            last_msg = msg[-1]
            tool_names = self._extract_tool_names_from_message(last_msg)

            if not tool_names:
                return ""

            # Retrieve tool guidelines
            result = await self.app.async_execute(
                name="retrieve_tool_memory",
                workspace_id=self.workspace_id,
                tool_names=tool_names,
                top_k=limit,
                **kwargs,
            )

            return self._format_retrieve_result(result)

        except Exception as e:
            logger.exception("Error retrieving tool guidelines: %s", str(e))
            import warnings

            warnings.warn(f"Error retrieving tool guidelines: {str(e)}")
            return ""
