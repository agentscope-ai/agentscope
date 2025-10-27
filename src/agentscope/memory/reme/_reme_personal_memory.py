# -*- coding: utf-8 -*-
"""Personal memory implementation using ReMe library.

This module provides a personal memory implementation that integrates
with the ReMe library to provide persistent personal memory storage and 
retrieval capabilities for AgentScope agents.
"""
from typing import Any

from ._reme_base_long_term_memory import ReMeBaseLongTermMemory
from ...message import Msg, TextBlock
from ...tool import ToolResponse


class ReMePersonalMemory(ReMeBaseLongTermMemory):
    """Personal memory implementation using ReMe library."""

    async def record_to_memory(
            self,
            thinking: str,
            content: list[str],
            **kwargs: Any,
    ) -> ToolResponse:
        """Use this function to record important information that you may
        need later. The target content should be specific and concise, e.g.
        who, when, where, do what, why, how, etc.

        Args:
            thinking (`str`):
                Your thinking and reasoning about what to record.
            content (`list[str]`):
                The content to remember, which is a list of strings.
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
            # Prepare messages for personal memory recording
            messages = []

            # Add thinking as a user message if provided
            if thinking:
                messages.append({
                    "role": "user",
                    "content": thinking,
                })

            # Add content items as user messages
            for item in content:
                messages.append({
                    "role": "user",
                    "content": item,
                })
                # Add a simple assistant acknowledgment
                messages.append({
                    "role": "assistant",
                    "content": f"I understand and will remember this information.",
                })

            # Call ReMe's summary_personal_memory flow
            result = await self.app.async_execute(
                name="summary_personal_memory",
                workspace_id=self.workspace_id,
                trajectories=[
                    {
                        "messages": messages,
                    },
                ],
                **kwargs,
            )

            # Extract metadata about stored memories if available
            metadata = result.get("metadata", {})
            memory_list = metadata.get("memory_list", [])

            if memory_list:
                summary_text = (
                    f"Successfully recorded {len(memory_list)} memory/memories "
                    f"to personal memory."
                )
            else:
                summary_text = "Memory recording completed."

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
                        text=f"Error recording memory: {str(e)}",
                    ),
                ],
            )

    async def retrieve_from_memory(
            self,
            keywords: list[str],
            limit: int = 5,
            **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve the memory based on the given keywords.

        Args:
            keywords (`list[str]`):
                The keywords to search for in the memory, which should be
                specific and concise, e.g. the person's name, the date, the
                location, etc.
            limit (`int`, optional):
                The maximum number of memories to retrieve per search.
                Default is 5.
            **kwargs (`Any`):
                Additional keyword arguments for the retrieval operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the retrieved memories as text.
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
            for keyword in keywords:
                result = await self.app.async_execute(
                    name="retrieve_personal_memory",
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
                combined_text = "No memories found for the given keywords."

            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=combined_text,
                    ),
                ],
            )

        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error retrieving memory: {str(e)}",
                    ),
                ],
            )

    async def record(
            self,
            msgs: list[Msg | None],
            **kwargs: Any,
    ) -> None:
        """Record the content to the long-term memory.

        This method converts AgentScope messages to ReMe's format and
        records them using the personal memory flow.

        Args:
            msgs (`list[Msg | None]`):
                The messages to record to memory.
            **kwargs (`Any`):
                Additional keyword arguments for the mem0 recording.
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

            # Call ReMe's summary_personal_memory flow
            await self.app.async_execute(
                name="summary_personal_memory",
                workspace_id=self.workspace_id,
                trajectories=[
                    {
                        "messages": messages,
                    },
                ],
                **kwargs,
            )

        except Exception as e:
            # Log the error but don't raise to maintain compatibility
            import warnings
            warnings.warn(f"Error recording messages to memory: {str(e)}")

    async def retrieve(
            self,
            msg: Msg | list[Msg] | None,
            limit: int = 5,
            **kwargs: Any,
    ) -> str:
        """Retrieve the content from the long-term memory.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message to search for in the memory, which should be
                specific and concise, e.g. the person's name, the date, the
                location, etc.
            limit (`int`, optional):
                The maximum number of memories to retrieve per search.
                Default is 5.
            **kwargs (`Any`):
                Additional keyword arguments.

        Returns:
            `str`:
                The retrieved memory as a string.
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
                name="retrieve_personal_memory",
                workspace_id=self.workspace_id,
                query=query,
                top_k=limit,
                **kwargs,
            )

            return result.get("answer", "")

        except Exception as e:
            import warnings
            warnings.warn(f"Error retrieving memory: {str(e)}")
            return ""
