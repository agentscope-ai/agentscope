# -*- coding: utf-8 -*-
"""The base class for ReAct agent in agentscope."""
from abc import abstractmethod
from collections import OrderedDict
from typing import Callable, Any

from ._agent_base import AgentBase
from ._agent_meta import _ReActAgentMeta
from ..message import Msg


class ReActAgentBase(AgentBase, metaclass=_ReActAgentMeta):
    """The ReAct agent base class.

    To support ReAct algorithm, this class extends the AgentBase class by
    adding two abstract interfaces: reasoning and acting, while supporting
    hook functions at four positions: pre-reasoning, post-reasoning,
    pre-acting, and post-acting by the `_ReActAgentMeta` metaclass.
    """

    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
        "pre_print",
        "post_print",
        "pre_observe",
        "post_observe",
        "pre_reasoning",
        "post_reasoning",
        "pre_acting",
        "post_acting",
    ]
    """Supported hook types for the agent base class."""

    _class_pre_reasoning_hooks: dict[
        str,
        Callable[
            [
                "ReActAgentBase",  # self
                dict[str, Any],  # kwargs
            ],
            dict[str, Any] | None,  # The modified kwargs or None
        ],
    ] = OrderedDict()
    """The class-level pre-reasoning hooks, taking `self` object, the input
    arguments as input"""

    _class_post_reasoning_hooks: dict[
        str,
        Callable[
            [
                "ReActAgentBase",  # self
                dict[str, Any],  # kwargs
                Any,  # output
            ],
            Msg | None,  # the modified output message or None
        ],
    ] = OrderedDict()
    """The class-level post-reasoning hooks, taking `self` object, the input
    arguments and the output message as input, and return the modified output
    message or None if no modification is needed."""

    _class_pre_acting_hooks: dict[
        str,
        Callable[
            [
                "ReActAgentBase",  # self
                dict[str, Any],  # kwargs
            ],
            dict[str, Any] | None,  # The modified kwargs or None
        ],
    ] = OrderedDict()
    """The class-level pre-acting hooks, taking `self` object, the input
    arguments as input, and return the modified input arguments or None if no
    modification is needed."""

    _class_post_acting_hooks: dict[
        str,
        Callable[
            [
                "ReActAgentBase",  # self
                dict[str, Any],  # kwargs
                Any,  # output
            ],
            Msg | None,  # the modified output message or None
        ],
    ] = OrderedDict()
    """The class-level post-acting hooks, taking `self` object, the input
    arguments and the output message as input, and return the modified output
    message or None if no modification is needed."""

    def __init__(
        self,
    ) -> None:
        """Initialize the ReAct agent base class."""
        super().__init__()

        # Init reasoning and acting hooks
        self._instance_pre_reasoning_hooks = OrderedDict()
        self._instance_post_reasoning_hooks = OrderedDict()
        self._instance_pre_acting_hooks = OrderedDict()
        self._instance_post_acting_hooks = OrderedDict()

    @abstractmethod
    async def _reasoning(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The reasoning process of the ReAct agent, which will be wrapped
        with pre- and post-hooks."""

    @abstractmethod
    async def _acting(self, *args: Any, **kwargs: Any) -> Any:
        """The acting process of the ReAct agent, which will be wrapped with
        pre- and post-hooks."""
