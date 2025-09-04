# -*- coding: utf-8 -*-
"""The agent base class in agentscope."""
import asyncio
import json
from asyncio import Task
from collections import OrderedDict
from typing import Callable, Any

import shortuuid

from ._agent_meta import _AgentMeta
from .._logging import logger
from ..module import StateModule
from ..message import Msg
from ..types import AgentHookTypes


class AgentBase(StateModule, metaclass=_AgentMeta):
    """Base class for asynchronous agents."""

    id: str
    """The agent's unique identifier, generated using shortuuid."""

    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
        "pre_print",
        "post_print",
        "pre_observe",
        "post_observe",
    ]
    """Supported hook types for the agent base class."""

    _class_pre_reply_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
            ],
            dict[str, Any] | None,  # The modified kwargs or None
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before the reply
    function, taking `self` object, the input arguments as input, and
    generating the modified arguments (if needed). Then input arguments of the
    reply function will be re-organized into a keyword arguments dictionary.
    If the one hook returns a new dictionary, the modified arguments will be
    passed to the next hook or the original reply function."""

    _class_post_reply_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
                Msg,  # output, the output message
            ],
            Msg | None,
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the reply
    function, which takes the `self` object and deep copied
    positional and keyword arguments (args and kwargs), and the output message
    as input. If the hook returns a message, the new message will be passed
    to the next hook or the original reply function. Otherwise, the original
    output will be passed instead."""

    _class_pre_print_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
            ],
            dict[str, Any] | None,  # The modified kwargs or None
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before printing,
    which takes the `self` object, a deep copied arguments dictionary as input,
    and output the modified arguments (if needed). """

    _class_post_print_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
                Any,  # output, `None` if no output
            ],
            Any,
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the speak
    function, which takes the `self` object as input."""

    _class_pre_observe_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
            ],
            dict[str, Any] | None,  # The modified kwargs or None
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before the observe
    function, which takes the `self` object and a deep copied input
    arguments dictionary as input. To change the input arguments, the hook
    function needs to output the modified arguments dictionary, which will be
    used as the input of the next hook function or the original observe
    function."""

    _class_post_observe_hooks: dict[
        str,
        Callable[
            [
                "AgentBase",  # self
                dict[str, Any],  # kwargs
                None,  # The output, `None` if no output
            ],
            None,
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the observe
    function, which takes the `self` object as input."""

    def __init__(self) -> None:
        """Initialize the agent."""
        super().__init__()

        self.id = shortuuid.uuid()

        # The replying task and identify of the current replying
        self._reply_task: Task | None = None
        self._reply_id: str | None = None

        # Initialize the instance-level hooks
        self._instance_pre_print_hooks = OrderedDict()
        self._instance_post_print_hooks = OrderedDict()

        self._instance_pre_reply_hooks = OrderedDict()
        self._instance_post_reply_hooks = OrderedDict()

        self._instance_pre_observe_hooks = OrderedDict()
        self._instance_post_observe_hooks = OrderedDict()

        # The prefix used in streaming printing
        self._stream_prefix = {}

        # The subscribers that will receive the reply message by their
        # `observe` method. The key is the MsgHub id, and the value is the
        # list of agents.
        self._subscribers: dict[str, list[AgentBase]] = {}

        # We add this variable in case developers want to disable the console
        # output of the agent, e.g., in a production environment.
        self._disable_console_output: bool = False

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive the given message(s) without generating a reply.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message(s) to be observed.
        """
        raise NotImplementedError(
            f"The observe function is not implemented in"
            f" {self.__class__.__name__} class.",
        )

    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """The main logic of the agent, which generates a reply based on the
        current state and input arguments."""
        raise NotImplementedError(
            "The reply function is not implemented in "
            f"{self.__class__.__name__} class.",
        )

    async def print(self, msg: Msg, last: bool = True) -> None:
        """The function to display the message.

        Args:
            msg (`Msg`):
                The message object to be printed.
            last (`bool`, defaults to `True`):
                Whether this is the last one in streaming messages. For
                non-streaming message, this should always be `True`.
        """
        if self._disable_console_output:
            return

        thinking_and_text_to_print = []
        for block in msg.get_content_blocks():
            prefix = self._stream_prefix.get(msg.id, "")
            if block["type"] in ["text", "thinking"]:
                block_type = block["type"]
                format_prefix = "" if block_type == "text" else "(thinking)"

                thinking_and_text_to_print.append(
                    f"{msg.name}{format_prefix}: {block[block_type]}",
                )

                to_print = "\n".join(thinking_and_text_to_print)
                if len(to_print) > len(prefix):
                    print(to_print[len(prefix) :], end="")
                    self._stream_prefix[msg.id] = to_print

            elif last:
                if prefix:
                    if not prefix.endswith("\n"):
                        print(
                            "\n"
                            + json.dumps(block, indent=4, ensure_ascii=False),
                        )
                    else:
                        print(json.dumps(block, indent=4, ensure_ascii=False))
                else:
                    print(
                        f"{msg.name}: "
                        f"{json.dumps(block, indent=4, ensure_ascii=False)}",
                    )
        if last and msg.id in self._stream_prefix:
            last_prefix = self._stream_prefix.pop(msg.id)
            if not last_prefix.endswith("\n"):
                print()

    async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
        """Call the reply function with the given arguments."""
        self._reply_id = shortuuid.uuid()

        reply_msg: Msg | None = None
        try:
            self._reply_task = asyncio.current_task()
            reply_msg = await self.reply(*args, **kwargs)

        # The interruption is triggered by calling the interrupt method
        except asyncio.CancelledError:
            reply_msg = await self.handle_interrupt(*args, **kwargs)

        finally:
            # Broadcast the reply message to all subscribers
            if reply_msg:
                await self._broadcast_to_subscribers(reply_msg)
            self._reply_task = None

        return reply_msg

    async def _broadcast_to_subscribers(
        self,
        msg: Msg | list[Msg] | None,
    ) -> None:
        """Broadcast the message to all subscribers."""
        for subscribers in self._subscribers.values():
            for subscriber in subscribers:
                await subscriber.observe(msg)

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else."""
        raise NotImplementedError(
            f"The handle_interrupt function is not implemented in "
            f"{self.__class__.__name__}",
        )

    async def interrupt(self, msg: Msg | list[Msg] | None = None) -> None:
        """Interrupt the current reply process."""
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel(msg)

    def register_instance_hook(
        self,
        hook_type: AgentHookTypes,
        hook_name: str,
        hook: Callable,
    ) -> None:
        """Register a hook to the agent instance, which only takes effect
        for the current instance.

        Args:
            hook_type (`str`):
                The type of the hook, indicating where the hook is to be
                triggered.
            hook_name (`str`):
                The name of the hook. If the name is already registered, the
                hook will be overwritten.
            hook (`Callable`):
                The hook function.
        """
        if not isinstance(self, AgentBase):
            raise TypeError(
                "The register_instance_hook method should be called on an "
                f"instance of AsyncAgentBase, but got {self} of "
                f"type {type(self)}.",
            )
        hooks = getattr(self, f"_instance_{hook_type}_hooks")
        hooks[hook_name] = hook

    def remove_instance_hook(
        self,
        hook_type: AgentHookTypes,
        hook_name: str,
    ) -> None:
        """Remove an instance-level hook from the agent instance.

        Args:
            hook_type (`AgentHookTypes`):
                The type of the hook, indicating where the hook is to be
                triggered.
            hook_name (`str`):
                The name of the hook to remove.
        """
        if not isinstance(self, AgentBase):
            raise TypeError(
                "The remove_instance_hook method should be called on an "
                f"instance of AsyncAgentBase, but got {self} of "
                f"type {type(self)}.",
            )
        hooks = getattr(self, f"_instance_{hook_type}_hooks")
        if hook_name in hooks:
            del hooks[hook_name]
        else:
            raise ValueError(
                f"Hook '{hook_name}' not found in '{hook_type}' hooks of "
                f"{self.__class__.__name__} instance.",
            )

    @classmethod
    def register_class_hook(
        cls,
        hook_type: AgentHookTypes,
        hook_name: str,
        hook: Callable,
    ) -> None:
        """The universal function to register a hook to the agent class, which
        will take effect for all instances of the class.

        Args:
            hook_type (`AgentHookTypes`):
                The type of the hook, indicating where the hook is to be
                triggered.
            hook_name (`str`):
                The name of the hook. If the name is already registered, the
                hook will be overwritten.
            hook (`Callable`):
                The hook function.
        """

        assert (
            hook_type in cls.supported_hook_types
        ), f"Invalid hook type: {hook_type}"

        hooks = getattr(cls, f"_class_{hook_type}_hooks")
        hooks[hook_name] = hook

    @classmethod
    def remove_class_hook(
        cls,
        hook_type: AgentHookTypes,
        hook_name: str,
    ) -> None:
        """Remove a class-level hook from the agent class.

        Args:
            hook_type (`AgentHookTypes`):
                The type of the hook, indicating where the hook is to be
                triggered.
            hook_name (`str`):
                The name of the hook to remove.
        """

        assert (
            hook_type in cls.supported_hook_types
        ), f"Invalid hook type: {hook_type}"
        hooks = getattr(cls, f"_class_{hook_type}_hooks")
        if hook_name in hooks:
            del hooks[hook_name]

        else:
            raise ValueError(
                f"Hook '{hook_name}' not found in '{hook_type}' hooks of "
                f"{cls.__name__} class.",
            )

    @classmethod
    def clear_class_hooks(
        cls,
        hook_type: AgentHookTypes | None = None,
    ) -> None:
        """Clear all class-level hooks.

        Args:
            hook_type (`AgentHookTypes`, optional):
                The type of the hook to clear. If not specified, all
                class-level hooks will be cleared.
        """

        if hook_type is None:
            for typ in cls.supported_hook_types:
                hooks = getattr(cls, f"_class_{typ}_hooks")
                hooks.clear()
        else:
            assert (
                hook_type in cls.supported_hook_types
            ), f"Invalid hook type: {hook_type}"
            hooks = getattr(cls, f"_class_{hook_type}_hooks")
            hooks.clear()

    def clear_instance_hooks(
        self,
        hook_type: AgentHookTypes | None = None,
    ) -> None:
        """If `hook_type` is not specified, clear all instance-level hooks.
        Otherwise, clear the specified type of instance-level hooks."""
        if hook_type is None:
            for typ in self.supported_hook_types:
                if not hasattr(self, f"_instance_{typ}_hooks"):
                    raise ValueError(
                        f"Call super().__init__() in the constructor "
                        f"to initialize the instance-level hooks for "
                        f"{self.__class__.__name__}.",
                    )
                hooks = getattr(self, f"_instance_{typ}_hooks")
                hooks.clear()

        else:
            assert (
                hook_type in self.supported_hook_types
            ), f"Invalid hook type: {hook_type}"
            if not hasattr(self, f"_instance_{hook_type}_hooks"):
                raise ValueError(
                    f"Call super().__init__() in the constructor "
                    f"to initialize the instance-level hooks for "
                    f"{self.__class__.__name__}.",
                )
            hooks = getattr(self, f"_instance_{hook_type}_hooks")
            hooks.clear()

    def reset_subscribers(
        self,
        msghub_name: str,
        subscribers: list["AgentBase"],
    ) -> None:
        """Reset the subscribers of the agent.

        Args:
            msghub_name (`str`):
                The name of the MsgHub that manages the subscribers.
            subscribers (`list[AgentBase]`):
                A list of agents that will receive the reply message from
                this agent via their `observe` method.
        """
        self._subscribers[msghub_name] = [_ for _ in subscribers if _ != self]

    def remove_subscribers(self, msghub_name: str) -> None:
        """Remove the msghub subscribers by the given msg hub name.

        Args:
            msghub_name (`str`):
                The name of the MsgHub that manages the subscribers.
        """
        if msghub_name not in self._subscribers:
            logger.warning(
                "MsgHub named '%s' not found",
                msghub_name,
            )
        else:
            self._subscribers.pop(msghub_name)

    def disable_console_output(self) -> None:
        """This function will disable the console output of the agent, e.g.
        in a production environment to avoid messy logs."""
        self._disable_console_output = True
