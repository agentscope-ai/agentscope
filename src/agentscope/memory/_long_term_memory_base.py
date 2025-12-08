# -*- coding: utf-8 -*-
"""The long-term memory base class."""

from typing import Any

from ..message import Msg
from ..module import StateModule
from ..tool import ToolResponse


class LongTermMemoryBase(StateModule):
    """The long-term memory base class, which should be a time-series
    memory management system.

    The `record_to_memory` and `retrieve_from_memory` methods are two tool
    functions for agent to manage the long-term memory voluntarily. You can
    choose not to implement these two functions.

    The `record` and `retrieve` methods are for developers to use. For example,
    retrieving/recording memory at the beginning of each reply, and adding
    the retrieved memory to the system prompt.
    """

    async def record(
        self,
        msgs: list[Msg | None],
        **kwargs: Any,
    ) -> None:
        """A developer-designed method to record information from the given
        input message(s) to the long-term memory."""
        raise NotImplementedError(
            "The `record` method is not implemented. ",
        )

    async def retrieve(
        self,
        msg: Msg | list[Msg] | None,
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """A developer-designed method to retrieve information from the
        long-term memory based on the given input message(s). The retrieved
        information will be added to the system prompt of the agent."""
        raise NotImplementedError(
            "The `retrieve` method is not implemented. ",
        )

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
                Your thinking and reasoning about what to record
            content (`list[str]`):
                The content to remember, which is a list of strings.
        """
        raise NotImplementedError(
            "The `record_to_memory` method is not implemented. "
            "You can implement it in your own long-term memory class.",
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
                The maximum number of memories to retrieve per search, i.e.,
                the number of memories to retrieve for each keyword. Defaults
                to 5.

        Returns:
            `list[Msg]`:
                A list of messages that match the keywords.
        """
        raise NotImplementedError(
            "The `retrieve_from_memory` method is not implemented. "
            "You can implement it in your own long-term memory class.",
        )
