# -*- coding: utf-8 -*-
"""The truncated formatter base class, which allows to truncate the input
messages."""
from abc import ABC
from copy import deepcopy
from typing import (
    Any,
    Tuple,
    Literal,
    AsyncGenerator,
)

from ._formatter_base import FormatterBase
from ..message import Msg
from ..token import TokenCounterBase
from ..tracing import trace_format


class TruncatedFormatterBase(FormatterBase, ABC):
    """Base class for truncated formatters, which formats input messages into
    required formats with tokens under a specified limit."""

    def __init__(
        self,
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the TruncatedFormatterBase.

        Args:
            token_counter (`TokenCounterBase | None`, optional):
                A token counter instance used to count tokens in the messages.
                If not provided, the formatter will format the messages
                without considering token limits.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If not provided, the formatter will not truncate
                the messages.
        """
        self.token_counter = token_counter

        assert (
            max_tokens is None or 0 < max_tokens
        ), "max_tokens must be greater than 0"
        self.max_tokens = max_tokens

    @trace_format
    async def format(
        self,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Format the input messages into the required format. If token
        counter and max token limit are provided, the messages will be
        truncated to fit the limit.

        Args:
            msgs (`list[Msg]`):
                The input messages to be formatted.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages in the required format.
        """

        # Check if the input messages are valid
        self.assert_list_of_msgs(msgs)

        msgs = deepcopy(msgs)

        while True:
            formatted_msgs = await self._format(msgs)
            n_tokens = await self._count(formatted_msgs)

            if (
                n_tokens is None
                or self.max_tokens is None
                or n_tokens <= self.max_tokens
            ):
                return formatted_msgs

            # truncate the input messages
            msgs = await self._truncate(msgs)

    async def _format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format the input messages into the required format. This method
        should be implemented by the subclasses."""

        formatted_msgs = []
        start_index = 0
        if len(msgs) > 0 and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            match typ:
                case "tool_sequence":
                    formatted_msgs.extend(
                        await self._format_tool_sequence(group),
                    )
                case "agent_message":
                    formatted_msgs.extend(
                        await self._format_agent_message(
                            group,
                            is_first_agent_message,
                        ),
                    )
                    is_first_agent_message = False

        return formatted_msgs

    async def _format_system_message(
        self,
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for the LLM API.

        .. note:: This is the default implementation. For certain LLM APIs
        with specific requirements, you may need to implement a custom
        formatting function to accommodate those particular needs.
        """
        return {
            "role": "system",
            "content": msg.get_content_blocks("text"),
        }

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the LLM API."""
        raise NotImplementedError(
            "_format_tool_sequence is not implemented",
        )

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the LLM API."""
        raise NotImplementedError(
            "_format_agent_message is not implemented",
        )

    async def _truncate(self, msgs: list[Msg]) -> list[Msg]:
        """Truncate the input messages, so that it can fit the token limit.
        This function is called only when

        - both `token_counter` and `max_tokens` are provided,
        - the formatted output of the input messages exceeds the token limit.

        .. tip:: This function only provides a simple strategy, and developers
         can override this method to implement more sophisticated
         truncation strategies.

        .. note:: The tool call message should be truncated together with
         its corresponding tool result message to satisfy the LLM API
         requirements.

        Args:
            msgs (`list[Msg]`):
                The input messages to be truncated.

        Raises:
            `ValueError`:
                If the system prompt message already exceeds the token limit,
                or if there are tool calls without corresponding tool results.

        Returns:
            `list[Msg]`:
                The truncated messages.
        """
        start_index = 0
        if len(msgs) > 0 and msgs[0].role == "system":
            if len(msgs) == 1:
                # If the system prompt already exceeds the token limit, we
                # raise an error.
                raise ValueError(
                    f"The system prompt message already exceeds the token "
                    f"limit ({self.max_tokens} tokens).",
                )

            start_index = 1

        # Create a tool call IDs queues to delete the corresponding tool
        # result message
        tool_call_ids = set()
        for i in range(start_index, len(msgs)):
            msg = msgs[i]
            for block in msg.get_content_blocks("tool_use"):
                tool_call_ids.add(block["id"])

            for block in msg.get_content_blocks("tool_result"):
                try:
                    tool_call_ids.remove(block["id"])
                except KeyError:
                    pass

            # We can stop truncating if the queue is empty
            if len(tool_call_ids) == 0:
                return msgs[:start_index] + msgs[i + 1 :]

        if len(tool_call_ids) > 0:
            raise ValueError(
                "The input messages contains tool call(s) that do not have "
                f"the corresponding tool result(s): {tool_call_ids}. ",
            )

        return msgs[:start_index]

    async def _count(self, msgs: list[dict[str, Any]]) -> int | None:
        """Count the number of tokens in the input messages. If token counter
        is not provided, `None` will be returned.

        Args:
            msgs (`list[Msg]`):
                The input messages to count tokens for.
        """
        if self.token_counter is None:
            return None

        return await self.token_counter.count(msgs)

    @staticmethod
    async def _group_messages(
        msgs: list[Msg],
    ) -> AsyncGenerator[
        Tuple[Literal["tool_sequence", "agent_message"], list[Msg]],
        None,
    ]:
        """Group the input messages into two types and yield them as a
        generator. The two types are:

        - agent message that doesn't contain tool calls/results, and
        - tool sequence that consisted of a sequence of tool calls/results

        .. note:: The group operation is used in multi-agent scenario, where
         multiple entities are involved in the input messages. So that to be
         compatible with tools API, we have to group the messages and format
         them with different strategies.

        Args:
            msgs (`list[Msg]`):
                The input messages to be grouped, where the system prompt
                message shouldn't be included.

        Yields:
            `AsyncGenerator[Tuple[str, list[Msg]], None]`:
                A generator that yields tuples of group type and the list of
                messages in that group. The group type can be either
                "tool_sequence" or "agent_message".
        """

        group_type: Literal["tool_sequence", "agent_message"] | None = None
        group = []
        for msg in msgs:
            if group_type is None:
                if msg.has_content_blocks(
                    "tool_use",
                ) or msg.has_content_blocks("tool_result"):
                    group_type = "tool_sequence"
                else:
                    group_type = "agent_message"

                group.append(msg)
                continue

            # determine if this msg has the same type as the current group
            if group_type == "tool_sequence":
                if msg.has_content_blocks(
                    "tool_use",
                ) or msg.has_content_blocks("tool_result"):
                    group.append(msg)

                else:
                    yield group_type, group
                    group = [msg]
                    group_type = "agent_message"

            elif group_type == "agent_message":
                if msg.has_content_blocks(
                    "tool_use",
                ) or msg.has_content_blocks("tool_result"):
                    yield group_type, group
                    group = [msg]
                    group_type = "tool_sequence"

                else:
                    group.append(msg)
        if group_type:
            yield group_type, group
