# -*- coding: utf-8 -*-
# pylint: disable=protected-access
""" Base class for Agent """

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from functools import wraps
from types import GeneratorType
from typing import Optional, Generator, Tuple, Callable, Dict, Literal
from typing import Sequence
from typing import Union
from typing import Any
import json
import uuid

import shortuuid
from loguru import logger

from ..rpc.rpc_config import DistConf
from ..rpc.rpc_meta import RpcMeta, async_func, sync_func
from ..logging import log_stream_msg, log_msg
from ..manager import ModelManager
from ..message import Msg, ToolUseBlock, TextBlock
from ..memory import TemporaryMemory


class _HooksMeta(type):
    """The hooks metaclass for all agent."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the `reply` and `observe` function with hooks."""
        if "reply" in attrs:
            original_reply = attrs["reply"]

            @wraps(original_reply)
            def wrapped_reply(
                self: AgentBase,
                *args: Any,
                **kwargs: Any,
            ) -> Union[Union[Msg, list[Msg]], None]:
                # Object-level pre-reply hooks
                current_args, current_kwargs = args, kwargs
                for _, hook in self._hooks_pre_reply.items():
                    hook_result = hook(
                        self,
                        deepcopy(current_args),
                        deepcopy(current_kwargs),
                    )
                    if hook_result is not None:
                        assert (
                            isinstance(hook_result, (list, tuple))
                            and len(hook_result) == 2
                        ), (
                            "Pre-reply hook must return a (args, kwargs) "
                            f"tuple or None, got {type(hook_result)} from "
                            f"hook {_}"
                        )
                        current_args, current_kwargs = hook_result

                # Class-level pre-reply hooks
                for _, hook in self._class_hooks_pre_reply.items():
                    hook_result = hook(
                        self,
                        deepcopy(current_args),
                        deepcopy(current_kwargs),
                    )
                    if hook_result is not None:
                        assert (
                            isinstance(hook_result, (list, tuple))
                            and len(hook_result) == 2
                        ), (
                            "Pre-reply hook must return a (args, kwargs) "
                            f"tuple or None, got {type(hook_result)} "
                            f"from hook {_}"
                        )
                        current_args, current_kwargs = hook_result

                # Original function
                reply_result = original_reply(
                    self,
                    *current_args,
                    **current_kwargs,
                )

                # Object-level post-reply hooks
                current_output = reply_result
                for _, hook in self._hooks_post_reply.items():
                    hook_result = hook(
                        self,
                        deepcopy(current_args),
                        deepcopy(current_kwargs),
                        deepcopy(current_output),
                    )
                    if hook_result is not None:
                        current_output = hook_result

                # Class-level post-reply hooks
                for _, hook in self._class_hooks_post_reply.items():
                    hook_result = hook(
                        self,
                        deepcopy(current_args),
                        deepcopy(current_kwargs),
                        deepcopy(current_output),
                    )
                    if hook_result is not None:
                        current_output = hook_result

                return current_output

            attrs["reply"] = wrapped_reply

        if "observe" in attrs:
            original_observe = attrs["observe"]

            @wraps(original_observe)
            def wrapped_observe(
                self: AgentBase,
                x: Union[Msg, list[Msg]],
            ) -> None:
                # Object-level pre hooks
                current_input = deepcopy(x)
                for _, hook in self._hooks_pre_observe.items():
                    hook_result = hook(self, deepcopy(current_input))
                    if hook_result is not None:
                        current_input = hook_result

                # Class-level pre hooks
                for _, hook in self._class_hooks_pre_observe.items():
                    hook_result = hook(self, deepcopy(current_input))
                    if hook_result is not None:
                        current_input = hook_result

                # Original function
                original_observe(self, current_input)

                # Object-level post hooks
                for _, hook in self._hooks_post_observe.items():
                    hook(self)  # type: ignore[call-arg]

                # Class-level post hooks
                for _, hook in self._class_hooks_post_observe.items():
                    hook(self)

            attrs["observe"] = wrapped_observe

        return super().__new__(mcs, name, bases, attrs)


class _AgentMeta(_HooksMeta, RpcMeta):
    """The combined metaclass for all agent.

    TODO: The RpcMeta will be deprecated in the future.
    """

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Add hooks to the agent class."""
        # First apply HooksMeta
        _HooksMeta.__new__(mcs, name, bases, attrs)
        # Then apply RpcMeta
        return RpcMeta.__new__(mcs, name, bases, attrs)


class AgentBase(metaclass=_AgentMeta):
    """Base class for all agent.

    All agent should inherit from this class and implement the `reply`
    function.
    """

    _version: int = 1

    _class_hooks_pre_reply: dict[
        str,
        Callable[
            [
                AgentBase,  # self
                Tuple[Any, ...],  # args
                Dict[str, Any],  # kwargs
            ],
            Union[Tuple[Tuple[Any, ...], Dict[str, Any]], None],
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before the reply
    function, which takes the `self` object and deep copied
    positional and keyword arguments as input (args and kwargs). If the
    return of the hook is not `None`, the new output will be
    passed to the next hook or the original reply function. Otherwise,
    the original input will be passed instead."""

    _class_hooks_post_reply: dict[
        str,
        Callable[
            [
                AgentBase,  # self
                Tuple[Any, ...],  # args
                Dict[str, Any],  # kwargs
                Msg,  # output
            ],
            Union[Msg, None],
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the reply
    function, which takes the `self` object and deep copied
    positional and keyword arguments (args and kwargs), and the output message
    as input. If the hook returns a message, the new message will be passed
    to the next hook or the original reply function. Otherwise, the original
    output will be passed instead."""

    _class_hooks_pre_speak: dict[
        str,
        Callable[
            [AgentBase, Msg, bool, bool],
            Union[Msg, None],
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before printing,
    which takes the `self` object, a deep copied printing message, a streaming
    flag, and a last flag as input. In streaming mode, the deep copied message
    will be a chunk of the original message, the streaming flat will be
    `True`, and the last flag will be `False` at the end of the streaming.
    If the hook returns a message, the new message will be passed to the next
    hook or the original speak function. Otherwise, the original input
    message will be passed instead."""

    _class_hooks_post_speak: dict[
        str,
        Callable[
            [AgentBase],
            None,
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the speak
    function, which takes the `self` object as input."""

    _class_hooks_pre_observe: dict[
        str,
        Callable[
            [AgentBase, Union[Msg, list[Msg]]],
            Union[Union[Msg, list[Msg]], None],
        ],
    ] = OrderedDict()
    """The class-level hook functions that will be called before the observe
    function, which takes the `self` object and a deep copied input message(s)
    as input. If the hook returns a new message, the new message will be
    passed to the next hook or the original observe function. Otherwise,
    the original input will be passed instead."""

    _class_hooks_post_observe: dict[
        str,
        Callable[[AgentBase], None],
    ] = OrderedDict()
    """The class-level hook functions that will be called after the observe
    function, which takes the `self` object as input."""

    def __init__(
        self,
        name: str,
        sys_prompt: Optional[str] = None,
        model_config_name: str = None,
        use_memory: bool = True,
        to_dist: Optional[Union[DistConf, bool]] = False,
    ) -> None:
        r"""Initialize an agent from the given arguments.

        Args:
            name (`str`):
                The name of the agent.
            sys_prompt (`Optional[str]`):
                The system prompt of the agent, which can be passed by args
                or hard-coded in the agent.
            model_config_name (`str`, defaults to None):
                The name of the model config, which is used to load model from
                configuration.
            use_memory (`bool`, defaults to `True`):
                Whether the agent has memory.
            to_dist (`Optional[Union[DistConf, bool]]`, default to `False`):
                The configurations passed to :py:meth:`to_dist` method. Used in
                :py:class:`RpcMeta`, when this parameter is provided,
                the agent will automatically be converted into its distributed
                version. Below are some examples:

                .. code-block:: python

                    # run as a sub process
                    agent = XXXAgent(
                        # ... other parameters
                        to_dist=True,
                    )

                    # connect to an existing agent server
                    agent = XXXAgent(
                        # ... other parameters
                        to_dist=DistConf(
                            host="<ip of your server>",
                            port=<port of your server>,
                            # other parameters
                        ),
                    )

                See :doc:`Tutorial<tutorial/208-distribute>` for detail.
        """
        self.name = name
        self.sys_prompt = sys_prompt

        # TODO: support to receive a ModelWrapper instance
        if model_config_name is not None:
            model_manager = ModelManager.get_instance()
            self.model = model_manager.get_model_by_config_name(
                model_config_name,
            )

        if use_memory:
            self.memory = TemporaryMemory()
        else:
            self.memory = None

        # The audience of this agent, which means if this agent generates a
        # response, it will be passed to all agent in the audience.
        self._audience = None
        # convert to distributed agent, conversion is in `_AgentMeta`
        if to_dist is not False and to_dist is not None:
            logger.info(
                f"Convert {self.__class__.__name__}[{self.name}] into"
                " a distributed agent.",
            )

        # Initialize the object-level hooks
        self._hooks_pre_speak = OrderedDict()
        self._hooks_post_speak = OrderedDict()

        self._hooks_pre_reply = OrderedDict()
        self._hooks_post_reply = OrderedDict()

        self._hooks_pre_observe = OrderedDict()
        self._hooks_post_observe = OrderedDict()

        # Used to identify one reply from agent
        self._reply_id = None

    @classmethod
    def generate_agent_id(cls) -> str:
        """Generate the agent_id of this agent instance"""
        # TODO: change cls.__name__ into a global unique agent_type
        return uuid.uuid4().hex

    @async_func
    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        """Define the actions taken by this agent.

        Args:
            x (`Optional[Union[Msg, list[Msg]]]`, defaults to `None`):
                The input message(s) to the agent, which also can be omitted if
                the agent doesn't need any input.

        Returns:
            `Msg`: The output message generated by the agent.

        Note:
            Given that some agent are in an adversarial environment,
            their input doesn't include the thoughts of other agent.
        """
        raise NotImplementedError(
            f"Agent [{type(self).__name__}] is missing the required "
            f'"reply" function.',
        )

    @async_func
    def __call__(self, *args: Any, **kwargs: Any) -> Msg:
        """Calling the reply function, and broadcast the generated
        response to all audiences if needed."""

        self._reply_id = shortuuid.uuid()

        res = self.reply(*args, **kwargs)

        self._reply_id = None

        # broadcast to audiences if needed
        if self._audience is not None:
            self._broadcast_to_audience(res)

        return res

    def speak(
        self,
        content: Union[
            str,
            Msg,
            Generator[Tuple[bool, str], None, None],
            None,
        ],
        tool_calls: Optional[list[ToolUseBlock]] = None,
    ) -> None:
        """
        Speak out the message generated by the agent. If a string is given,
        a Msg object will be created with the string as the content.

        Args:
            content
             (`Union[str, Msg, Generator[Tuple[bool, str], None, None],
             None]`):
                The content of the message to be spoken out. If a string is
                given, a Msg object will be created with the agent's name, role
                as "assistant", and the given string as the content.
                If the content is a Generator, the agent will speak out the
                message chunk by chunk.
            tool_calls (`Optional[list[ToolUseBlock]]`, defaults to `None`):
                The tool calls generated by the agent. This parameter is only
                used when the content is a string or a generator (TODO). When
                the content is a Msg object, the tool calls should be included
                in the content field of the Msg object.
        """

        def _call_pre_speak_hooks(
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> Msg:
            """Call the hooks in the speak function."""
            # Object-level pre-speak hooks
            current_input = deepcopy(msg)
            for _, hook in self._hooks_pre_speak.items():
                hook_result = hook(self, deepcopy(current_input), stream, last)
                if hook_result is not None:
                    current_input = hook_result

            # Class-level pre-speak hooks
            for _, hook in self._class_hooks_pre_speak.items():
                hook_result = hook(self, deepcopy(current_input), stream, last)
                if hook_result is not None:
                    current_input = hook_result

            return current_input

        # Streaming mode
        if isinstance(content, GeneratorType):
            # The streaming message must share the same id for displaying in
            # the agentscope studio.
            msg = Msg(name=self.name, content="", role="assistant")
            for last, text_chunk in content:
                msg.content = text_chunk  # type: ignore[misc]
                # Call the hooks
                new_input = _call_pre_speak_hooks(
                    msg,
                    stream=True,
                    last=last,
                )
                log_stream_msg(new_input, last=last)

            # Call the object-level post speak hooks
            for _, hook in self._hooks_post_speak.items():
                hook(self)

            # Call the class-level post speak hooks
            for _, hook in self._class_hooks_post_speak.items():
                hook(self)

            return
        # Non-streaming mode
        if isinstance(content, str):
            content = [TextBlock(type="text", text=content)]
            if tool_calls:
                content.extend(tool_calls)
            msg_to_speak = Msg(
                name=self.name,
                content=content,
                role="assistant",
            )
        elif content is None:
            msg_to_speak = (
                Msg(
                    name=self.name,
                    content=tool_calls,
                    role="assistant",
                )
                if tool_calls
                else None
            )
        elif isinstance(content, Msg):
            msg_to_speak = content
        else:
            raise TypeError(
                "From version 0.0.5, the speak method only accepts str, Msg "
                "object and Generator, got {type(content)} instead.",
            )

        if msg_to_speak is None:
            logger.warning(
                "The content and tool_calls cannot be `None` at the same "
                "time, skip the speak function.",
            )
            return

        # Call the hooks
        msg_hook = _call_pre_speak_hooks(msg_to_speak, stream=False, last=True)

        log_msg(msg_hook)

        # Call the object-level post speak hooks
        for _, hook in self._hooks_post_speak.items():
            hook(self)

        # Call the class-level post speak hooks
        for _, hook in self._class_hooks_post_speak.items():
            hook(self)

    def observe(self, x: Union[Msg, Sequence[Msg]]) -> None:
        """Observe the input, store it in memory without response to it.

        Args:
            x (`Union[Msg, Sequence[Msg]]`):
                The input message to be recorded in memory.
        """
        if self.memory:
            self.memory.add(x)

    def reset_audience(self, audience: Sequence[AgentBase]) -> None:
        """Set the audience of this agent, which means if this agent
        generates a response, it will be passed to all audiences.

        Args:
            audience (`Sequence[AgentBase]`):
                The audience of this agent, which will be notified when this
                agent generates a response message.
        """
        # TODO: we leave the consideration of nested msghub for future.
        #  for now we suppose one agent can only be in one msghub
        self._audience = [_ for _ in audience if _ != self]

    def clear_audience(self) -> None:
        """Remove the audience of this agent."""
        # TODO: we leave the consideration of nested msghub for future.
        #  for now we suppose one agent can only be in one msghub
        self._audience = None

    def rm_audience(
        self,
        audience: Union[Sequence[AgentBase], AgentBase],
    ) -> None:
        """Remove the given audience from the Sequence"""
        if not isinstance(audience, Sequence):
            audience = [audience]

        for agent in audience:
            if self._audience is not None and agent in self._audience:
                self._audience.pop(self._audience.index(agent))
            else:
                logger.warning(
                    f"Skip removing agent [{agent.name}] from the "
                    f"audience for its inexistence.",
                )

    def _broadcast_to_audience(self, x: dict) -> None:
        """Broadcast the input to all audiences."""
        for agent in self._audience:
            agent.observe(x)

    @sync_func
    def __str__(self) -> str:
        serialized_fields = {
            "name": self.name,
            "type": self.__class__.__name__,
            "sys_prompt": self.sys_prompt,
            "agent_id": self.agent_id,
        }
        if hasattr(self, "model"):
            serialized_fields["model"] = {
                "model_type": self.model.model_type,
                "config_name": self.model.config_name,
            }
        return json.dumps(serialized_fields, ensure_ascii=False)

    @property
    def agent_id(self) -> str:
        """The unique id of this agent.

        Returns:
            str: agent_id
        """
        return self._oid

    @agent_id.setter
    def agent_id(self, agent_id: str) -> None:
        """Set the unique id of this agent."""
        self._oid = agent_id

    def register_hook(
        self,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
        hook_name: str,
        hook: Callable,
    ) -> None:
        """The universal function to register a hook to the agent.

        Args:
            hook_type (`Literal["pre_reply", "post_reply", "pre_speak",
             "post_speak", "pre_observe", "post_observe"]`):
                The type of the hook, which should be one of "pre_reply",
                "post_reply", "pre_speak", "post_speak", "pre_observe",
                and "post_observe".
            hook_name (`str`):
                The name of the hook. If the name is already registered, the
                hook will be overwritten.
            hook (`Callable`):
                The hook function.
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(self, "_hooks_" + hook_type)
        hooks[hook_name] = hook

    def remove_hook(
        self,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
        hook_name: str,
    ) -> None:
        """The universal function to remove a hook from the agent object.

        Args:
            hook_type (`Literal["pre_reply", "post_reply", "pre_speak",
             "post_speak", "pre_observe", "post_observe"]`):
                The type of the hook, which should be one of "pre_reply",
                "post_reply", "pre_speak", "post_speak", "pre_observe",
                and "post_observe".
            hook_name (`str`):
                The name of the hook to be removed.
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(self, "_hooks_" + hook_type)
        if hook_name in hooks:
            hooks.pop(hook_name)
        else:
            raise ValueError(
                f"Hook [{hook_name}] not found in {hook_type}.",
            )

    def clear_hooks(
        self,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
    ) -> None:
        """Clear the specified hooks from the agent object.

        Args:
            hook_type (`Literal["pre_reply", "post_reply", "pre_speak",
             "post_speak", "pre_observe", "post_observe"]`):
                The type of the hook, which should be one of "pre_reply",
                "post_reply", "pre_speak", "post_speak", "pre_observe",
                and "post_observe".
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(self, "_hooks_" + hook_type)
        hooks.clear()

    def clear_all_obj_hooks(self) -> None:
        """Clear all hooks from the agent object"""
        self.clear_hooks("pre_reply")
        self.clear_hooks("post_reply")
        self.clear_hooks("pre_observe")
        self.clear_hooks("post_observe")
        self.clear_hooks("pre_speak")
        self.clear_hooks("post_speak")

    @classmethod
    def register_class_hook(
        cls,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
        hook_name: str,
        hook: Callable,
    ) -> None:
        """The universal function to register a hook to the agent class, which
        will take effect for all instances of the class.

        Args:
            hook_type (`str`):
                The type of the hook, which should be one of "pre_reply",
                "post_reply", "pre_speak", "post_speak", "pre_observe",
                and "post_observe".
            hook_name (`str`):
                The name of the hook. If the name is already registered, the
                hook will be overwritten.
            hook (`Callable`):
                The hook function.
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(cls, "_class_hooks_" + hook_type)
        hooks[hook_name] = hook

    @classmethod
    def remove_class_hook(
        cls,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
        hook_name: str,
    ) -> None:
        """The universal function to remove a hook from the agent class.

        Args:
            hook_type (`Literal["pre_reply", "post_reply", "pre_speak",
             "post_speak", "pre_observe", "post_observe"]`):
                The type of the hook, which should be one of "pre_reply",
                "post_reply", "pre_speak", "post_speak", "pre_observe",
                and "post_observe".
            hook_name (`str`):
                The name of the hook to be removed.
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(cls, "_class_hooks_" + hook_type)
        if hook_name in hooks:
            hooks.pop(hook_name)

    @classmethod
    def clear_class_hooks(
        cls,
        hook_type: Literal[
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ],
    ) -> None:
        """Clear the specified hooks from the agent class.

        Args:
            hook_type (`Literal["pre_reply", "post_reply", "pre_speak",
             "post_speak", "pre_observe", "post_observe"]`):
                The name of the hooks attribute, which should be one of
                "pre_reply", "post_reply", "pre_speak", "post_speak",
                "pre_observe", and "post_observe".
        """
        assert hook_type in [
            "pre_reply",
            "post_reply",
            "pre_speak",
            "post_speak",
            "pre_observe",
            "post_observe",
        ], f"Invalid hook type: {hook_type}"

        hooks = getattr(cls, "_class_hooks_" + hook_type)
        hooks.clear()

    @classmethod
    def clear_all_class_hooks(cls) -> None:
        """Clear all hooks from the agent class"""
        cls.clear_class_hooks("pre_reply")
        cls.clear_class_hooks("post_reply")
        cls.clear_class_hooks("pre_observe")
        cls.clear_class_hooks("post_observe")
        cls.clear_class_hooks("pre_speak")
        cls.clear_class_hooks("post_speak")
