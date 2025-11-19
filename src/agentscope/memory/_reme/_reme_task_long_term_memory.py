# -*- coding: utf-8 -*-
"""Task memory implementation using ReMe library.

This module provides a task memory implementation that integrates
with the ReMe library to learn from execution trajectories and
retrieve relevant task experiences.

"""
from typing import Any

from ._reme_long_term_memory_base import ReMeLongTermMemoryBase
from ..._logging import logger
from ...message import Msg, TextBlock
from ...tool import ToolResponse


class ReMeTaskLongTermMemory(ReMeLongTermMemoryBase):
    """Task memory implementation using ReMe library.

    Task memory learns from execution trajectories and provides
    retrieval of relevant task experiences.

    """

    async def record_to_memory(
        self,
        thinking: str,
        content: list[str],
        **kwargs: Any,
    ) -> ToolResponse:
        """Record task execution experiences and learnings.

        Record task execution experiences and learnings to long-term
        memory.

        Use this function to save valuable task-related knowledge that
        can help with future similar tasks. This enables learning from
        experience and improving over time.

        When to record:

        - After solving technical problems or completing tasks
        - When discovering useful techniques or approaches
        - After implementing solutions with specific steps
        - When learning best practices or important lessons

        What to record: Be detailed and actionable. Include:

        - Task description and context
        - Step-by-step execution details
        - Specific techniques and methods used
        - Results, outcomes, and effectiveness
        - Lessons learned and considerations

        Args:
            thinking (`str`):
                Your reasoning about why this task experience is valuable
                and what makes it worth remembering for future reference.
            content (`list[str]`):
                List of specific task insights to remember. Each string
                should be a clear, actionable piece of information.
                Examples: ["Add indexes on WHERE clause columns to speed
                up queries", "Use EXPLAIN ANALYZE to identify missing
                indexes"].
            **kwargs (`Any`):
                Additional keyword arguments. Can include 'score' (float)
                to indicate the quality/success of this approach
                (default: 1.0).

        Returns:
            `ToolResponse`:
                Confirmation message indicating successful memory
                recording.
        """
        logger.info(
            "[ReMeTaskMemory] Entering record_to_memory - "
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
            # Prepare messages for task memory recording
            messages = []

            # Add thinking as a user message if provided
            if thinking:
                messages.append(
                    {
                        "role": "user",
                        "content": thinking,
                    },
                )

            # Add content items as user-assistant pairs
            for item in content:
                messages.append(
                    {
                        "role": "user",
                        "content": item,
                    },
                )
                # Add a simple assistant acknowledgment
                messages.append(
                    {
                        "role": "assistant",
                        "content": "Task information recorded.",
                    },
                )

            result = await self.app.async_execute(
                name="summary_task_memory",
                workspace_id=self.workspace_id,
                trajectories=[
                    {
                        "messages": messages,
                        "score": kwargs.pop("score", 1.0),
                    },
                ],
                **kwargs,
            )

            # Extract metadata if available
            summary_text = (
                f"Successfully recorded {len(content)} task memory/memories."
            )

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=summary_text,
                    ),
                ],
                metadata={"result": result},
            )

        except Exception as e:
            logger.exception("Error recording task memory: %s", str(e))
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error recording task memory: {str(e)}",
                    ),
                ],
            )

    async def retrieve_from_memory(
        self,
        keywords: list[str],
        limit: int = 5,
        **kwargs: Any,
    ) -> ToolResponse:
        """Search and retrieve relevant task experiences.

        Search and retrieve relevant task experiences from long-term
        memory.

        IMPORTANT: You should call this function BEFORE attempting to
        solve problems or answer technical questions. This ensures you
        leverage experiences and proven solutions rather than
        starting from scratch.

        Use this when:
        - Asked to solve a technical problem or implement a solution
        - Asked for recommendations, best practices, or approaches
        - Asked "what do you know about...?" or "have you seen this
          before?"
        - Dealing with tasks that may be similar to experiences
        - Need to recall specific techniques or methods

        Benefits of retrieving first:
        - Learn from past successes and mistakes
        - Provide more accurate, battle-tested solutions
        - Avoid reinventing the wheel
        - Give consistent, informed recommendations

        Args:
            keywords (`list[str]`):
                Keywords describing the task or problem domain. Be
                specific and use technical terms. Examples:
                ["database optimization", "slow queries"], ["API design",
                "rate limiting"], ["code refactoring", "Python"].
            limit (`int`, optional):
                The maximum number of memories to retrieve per search, i.e.,
                the number of memories to retrieve for each keyword. Defaults
                to 5.
            **kwargs (`Any`):
                Additional keyword arguments. Can include 'top_k' (int)
                to specify number of experiences to retrieve
                (default: 3).

        Returns:
            `ToolResponse`:
                Retrieved task experiences and learnings. If no relevant
                experiences found, you'll receive a message indicating
                that.
        """
        logger.info(
            "[ReMeTaskMemory] Entering retrieve_from_memory - "
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
            results = []

            # Search for each keyword
            for keyword in keywords:
                result = await self.app.async_execute(
                    name="retrieve_task_memory",
                    workspace_id=self.workspace_id,
                    query=keyword,
                    top_k=limit,
                    **kwargs,
                )

                # Extract the answer from the result
                answer = result.get("answer", "")
                if answer:
                    results.append(f"Keyword '{keyword}':\n{answer}")

            # Combine all results
            if results:
                combined_text = "\n\n".join(results)
            else:
                combined_text = (
                    "No task experiences found for the given keywords."
                )

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=combined_text,
                    ),
                ],
            )

        except Exception as e:
            logger.exception("Error retrieving task memory: %s", str(e))
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error retrieving task memory: {str(e)}",
                    ),
                ],
            )

    async def record(
        self,
        msgs: list[Msg | None],
        **kwargs: Any,
    ) -> None:
        """Record the content to the task memory.

        This method converts AgentScope messages to ReMe's format and
        records them as a task execution trajectory.

        Args:
            msgs (`list[Msg | None]`):
                The messages to record to memory.
            **kwargs (`Any`):
                Additional keyword arguments for the recording.
                Can include 'score' (float) for trajectory scoring
                (default: 1.0).
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
            # Convert AgentScope messages to ReMe format
            messages = []
            for msg in msg_list:
                # Extract content as string
                if isinstance(msg.content, str):
                    content_str = msg.content
                elif isinstance(msg.content, list):
                    # Join content blocks into a single string
                    content_parts = []
                    for block in msg.content:
                        if isinstance(block, dict) and "text" in block:
                            content_parts.append(block["text"])
                        elif isinstance(block, dict) and "thinking" in block:
                            content_parts.append(block["thinking"])
                    content_str = "\n".join(content_parts)
                else:
                    content_str = str(msg.content)

                messages.append(
                    {
                        "role": msg.role,
                        "content": content_str,
                    },
                )

            # Extract score from kwargs if provided, default to 1.0
            score = kwargs.pop("score", 1.0)

            await self.app.async_execute(
                name="summary_task_memory",
                workspace_id=self.workspace_id,
                trajectories=[
                    {
                        "messages": messages,
                        "score": score,
                    },
                ],
                **kwargs,
            )

        except Exception as e:
            # Log the error but don't raise to maintain compatibility
            logger.exception(
                "Error recording messages to task memory: %s",
                str(e),
            )
            import warnings

            warnings.warn(
                f"Error recording messages to task memory: {str(e)}",
            )

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """Retrieve relevant task experiences from memory.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message to search for relevant task experiences.
            limit (`int`, optional):
                The maximum number of memories to retrieve per search, i.e.,
                the number of memories to retrieve for the message. If the
                message is a list of messages, the limit applies to each
                message. If the message is a single message, the limit is the
                total number of memories to retrieve for that message. Defaults
                to 3.
            **kwargs (`Any`):
                Additional keyword arguments.

        Returns:
            `str`:
                The retrieved task experiences as a string.
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
            # Only use the last message's content for retrieval
            last_msg = msg[-1]
            query = ""

            if isinstance(last_msg.content, str):
                query = last_msg.content
            elif isinstance(last_msg.content, list):
                # Extract text from content blocks
                content_parts = []
                for block in last_msg.content:
                    if isinstance(block, dict) and "text" in block:
                        content_parts.append(block["text"])
                    elif isinstance(block, dict) and "thinking" in block:
                        content_parts.append(block["thinking"])
                query = "\n".join(content_parts)

            if not query:
                return ""

            # Retrieve using the query from the last message
            result = await self.app.async_execute(
                name="retrieve_task_memory",
                workspace_id=self.workspace_id,
                query=query,
                top_k=limit,
                **kwargs,
            )

            return result.get("answer", "")

        except Exception as e:
            logger.exception("Error retrieving task memory: %s", str(e))
            import warnings

            warnings.warn(f"Error retrieving task memory: {str(e)}")
            return ""
