# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""Task memory implementation using ReMe library.

This module provides a task memory implementation that integrates
with the ReMe library to learn from execution trajectories and
retrieve relevant task experiences.
"""
from typing import Any

from loguru import logger

from ._reme_base_long_term_memory import ReMeBaseLongTermMemory
from ...message import Msg, TextBlock
from ...tool import ToolResponse


class ReMeTaskMemory(ReMeBaseLongTermMemory):
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
        """Use this function to record important task execution information
        that you may need later. The target content should be specific and
        concise, e.g. task description, execution steps, results, etc.

        Args:
            thinking (`str`):
                Your thinking and reasoning about what to record.
            content (`list[str]`):
                The content to remember, which is a list of strings representing
                task execution information.
            **kwargs (`Any`):
                Additional keyword arguments for the recording operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the result of the memory recording.
        """
        if not self._app_started:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Error: ReMeApp context not started. "
                        "Please use 'async with' to initialize the app.",
                    ),
                ],
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
            logger.exception(f"Error recording task memory: {str(e)}")
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
        **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve the memory based on the given keywords.

        Args:
            keywords (`list[str]`):
                The keywords to search for in the memory, which should be
                specific and concise, e.g. task name, execution context, etc.
            **kwargs (`Any`):
                Additional keyword arguments for the retrieval operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the retrieved task experiences.
        """
        if not self._app_started:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Error: ReMeApp context not started. "
                        "Please use 'async with' to initialize the app.",
                    ),
                ],
            )

        try:
            results = []

            # Search for each keyword
            top_k = kwargs.get("top_k", 5)
            for keyword in keywords:
                result = await self.app.async_execute(
                    name="retrieve_task_memory",
                    workspace_id=self.workspace_id,
                    query=keyword,
                    top_k=top_k,
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
            logger.exception(f"Error retrieving task memory: {str(e)}")
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
                Can include 'score' (float) for trajectory scoring (default: 1.0).
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
                f"Error recording messages to task memory: {str(e)}",
            )
            import warnings

            warnings.warn(f"Error recording messages to task memory: {str(e)}")

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        **kwargs: Any,
    ) -> str:
        """Retrieve relevant task experiences from memory.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message to search for relevant task experiences.
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
            # Extract top_k from kwargs if available, default to 5
            top_k = kwargs.get("top_k", 5)
            result = await self.app.async_execute(
                name="retrieve_task_memory",
                workspace_id=self.workspace_id,
                query=query,
                top_k=top_k,
                **kwargs,
            )

            return result.get("answer", "")

        except Exception as e:
            logger.exception(f"Error retrieving task memory: {str(e)}")
            import warnings

            warnings.warn(f"Error retrieving task memory: {str(e)}")
            return ""
