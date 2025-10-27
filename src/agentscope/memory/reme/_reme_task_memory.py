# -*- coding: utf-8 -*-
"""Task memory implementation using ReMe library.

This module provides a task memory implementation that integrates
with the ReMe library to learn from execution trajectories and
retrieve relevant task experiences.
"""
from typing import Any

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
            trajectories: list[dict[str, Any]],
            **kwargs: Any,
    ) -> ToolResponse:
        """Record task execution trajectories to memory.
        
        Each trajectory should contain:
        - messages: List of message dictionaries with 'role' and 'content'
        - score: Optional float score for the trajectory (default 1.0)

        Args:
            trajectories (`list[dict[str, Any]]`):
                List of execution trajectories to record. Each trajectory
                should have 'messages' and optionally 'score'.
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
            # Call ReMe's summary_task_memory flow
            result = await self.app.async_execute(
                name="summary_task_memory",
                workspace_id=self.workspace_id,
                trajectories=trajectories,
                **kwargs,
            )

            # Extract metadata if available
            # metadata = result.get("metadata", {})

            summary_text = f"Successfully recorded {len(trajectories)} trajectory/trajectories to task memory."

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
            query: str,
            top_k: int = 5,
            **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve relevant task experiences from memory.

        Args:
            query (`str`):
                The query to search for relevant task experiences.
            top_k (`int`, optional):
                The maximum number of experiences to retrieve.
                Default is 5.
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
            result = await self.app.async_execute(
                name="retrieve_task_memory",
                workspace_id=self.workspace_id,
                query=query,
                top_k=top_k,
                **kwargs,
            )

            # Extract the answer from the result
            answer = result.get("answer", "")

            if answer:
                combined_text = f"Task Experiences for '{query}':\n{answer}"
            else:
                combined_text = "No relevant task experiences found."

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=combined_text,
                    ),
                ],
                metadata={"result": result},
            )

        except Exception as e:
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
            score: float = 1.0,
            **kwargs: Any,
    ) -> None:
        """Record the content to the task memory.

        This method converts AgentScope messages to ReMe's format and
        records them as a task execution trajectory.

        Args:
            msgs (`list[Msg | None]`):
                The messages to record to memory.
            score (`float`, optional):
                The score for this trajectory. Default is 1.0.
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

                messages.append({
                    "role": msg.role,
                    "content": content_str,
                })

            # Call ReMe's summary_task_memory flow
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
            import warnings
            warnings.warn(f"Error recording messages to task memory: {str(e)}")

    async def retrieve(
            self,
            msg: Msg | list[Msg] | None,
            top_k: int = 5,
            **kwargs: Any,
    ) -> str:
        """Retrieve relevant task experiences from memory.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message to search for relevant task experiences.
            top_k (`int`, optional):
                The maximum number of experiences to retrieve.
                Default is 5.
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
                top_k=top_k,
                **kwargs,
            )

            return result.get("answer", "")

        except Exception as e:
            import warnings
            warnings.warn(f"Error retrieving task memory: {str(e)}")
            return ""
