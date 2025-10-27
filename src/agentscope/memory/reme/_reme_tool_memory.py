# -*- coding: utf-8 -*-
"""Tool memory implementation using ReMe library.

This module provides a tool memory implementation that integrates
with the ReMe library to record tool execution results and retrieve
tool usage guidelines.
"""
from typing import Any

from ._reme_base_long_term_memory import ReMeBaseLongTermMemory
from ...message import Msg, TextBlock
from ...tool import ToolResponse


class ReMeToolMemory(ReMeBaseLongTermMemory):
    """Tool memory implementation using ReMe library.
    
    Tool memory records tool execution results and provides
    retrieval of tool usage guidelines and best practices.
    """

    async def add_tool_call_result(
            self,
            tool_call_results: list[dict[str, Any]],
            **kwargs: Any,
    ) -> ToolResponse:
        """Add tool call results to memory.
        
        Each tool call result should contain:
        - create_time: Timestamp of the execution
        - tool_name: Name of the tool
        - input: Input parameters as a dictionary
        - output: Output result as a string
        - token_cost: Token cost (optional)
        - success: Whether the call was successful
        - time_cost: Time cost in seconds (optional)

        Args:
            tool_call_results (`list[dict[str, Any]]`):
                List of tool call results to record.
            **kwargs (`Any`):
                Additional keyword arguments for the recording operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the result of the recording.
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
            # Call ReMe's add_tool_call_result flow
            result = await self.app.async_execute(
                name="add_tool_call_result",
                workspace_id=self.workspace_id,
                tool_call_results=tool_call_results,
                **kwargs,
            )

            summary_text = f"Successfully recorded {len(tool_call_results)} tool call result(s)."

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
                        text=f"Error adding tool call result: {str(e)}",
                    ),
                ],
            )

    async def summary_tool_memory(
            self,
            tool_names: str | list[str],
            **kwargs: Any,
    ) -> ToolResponse:
        """Generate usage guidelines from tool execution history.

        Args:
            tool_names (`str | list[str]`):
                Name(s) of the tool(s) to summarize.
            **kwargs (`Any`):
                Additional keyword arguments for the summary operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the generated guidelines.
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
            # Convert list to comma-separated string if needed
            if isinstance(tool_names, list):
                tool_names = ",".join(tool_names)

            result = await self.app.async_execute(
                name="summary_tool_memory",
                workspace_id=self.workspace_id,
                tool_names=tool_names,
                **kwargs,
            )

            # Extract the answer from the result
            answer = result.get("answer", "")

            if answer:
                combined_text = f"Tool Usage Guidelines:\n{answer}"
            else:
                combined_text = "Tool memory summary completed."

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
                        text=f"Error summarizing tool memory: {str(e)}",
                    ),
                ],
            )

    async def retrieve_tool_memory(
            self,
            tool_names: str | list[str],
            **kwargs: Any,
    ) -> ToolResponse:
        """Retrieve tool usage guidelines before use.

        Args:
            tool_names (`str | list[str]`):
                Name(s) of the tool(s) to retrieve guidelines for.
            **kwargs (`Any`):
                Additional keyword arguments for the retrieval operation.

        Returns:
            `ToolResponse`:
                A ToolResponse containing the tool usage guidelines.
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
            # Convert list to comma-separated string if needed
            if isinstance(tool_names, list):
                tool_names = ",".join(tool_names)

            result = await self.app.async_execute(
                name="retrieve_tool_memory",
                workspace_id=self.workspace_id,
                tool_names=tool_names,
                **kwargs,
            )

            # Extract the answer from the result
            answer = result.get("answer", "")

            if answer:
                combined_text = f"Tool Guidelines for '{tool_names}':\n{answer}"
            else:
                combined_text = "No tool guidelines found."

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
                        text=f"Error retrieving tool memory: {str(e)}",
                    ),
                ],
            )

    async def record(
            self,
            msgs: list[Msg | None],
            **kwargs: Any,
    ) -> None:
        """Record tool-related messages to memory.
        
        Note: This is a simplified implementation. For proper tool memory
        recording, use add_tool_call_result() with structured tool data.

        Args:
            msgs (`list[Msg | None]`):
                The messages to record to memory.
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

        # Tool memory recording requires structured data
        # This is a placeholder implementation
        import warnings
        warnings.warn(
            "Tool memory recording from messages is not directly supported. "
            "Please use add_tool_call_result() with structured tool data."
        )

    async def retrieve(
            self,
            msg: Msg | list[Msg] | None,
            **kwargs: Any,
    ) -> str:
        """Retrieve tool guidelines based on message content.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message to extract tool names from.
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
            # This is a simplified implementation
            last_msg = msg[-1]

            # Try to extract tool name from content
            # In practice, you may want to parse this more carefully
            if isinstance(last_msg.content, str):
                content = last_msg.content
            else:
                return ""

            # For now, just use the content as tool name
            # In production, you'd want better extraction logic
            result = await self.app.async_execute(
                name="retrieve_tool_memory",
                workspace_id=self.workspace_id,
                tool_names=content,
                **kwargs,
            )

            return result.get("answer", "")

        except Exception as e:
            import warnings
            warnings.warn(f"Error retrieving tool memory: {str(e)}")
            return ""
