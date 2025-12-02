# -*- coding: utf-8 -*-
"""
.. _hook:
Hooks
===========================

Hooks are extension points in AgentScope that allow developers to customize agent behaviors at specific execution points. They provide a flexible way to modify or extend the agent's functionality without changing its core implementation.

AgentScope implements hooks around three core agent functions:

- `reply`: Generates response messages based on the agent's current state
- `speak`: Displays and records messages to the terminal
- `observe`: Records incoming messages from environment or other agents

Available Hooks
----------------------
Each core function has corresponding pre- and post-execution hooks:

- `pre_reply_hook` / `post_reply_hook`
- `pre_speak_hook` / `post_speak_hook`
- `pre_observe_hook` / `post_observe_hook`

For example, you can use `pre_speak_hook` to redirect messages to different outputs like web interfaces or external applications.

.. important:: When working with hooks, keep these important rules in mind:

 1. **Hook Function Signature**
  - First argument must be the `AgentBase` object (i.e., `self`)
  - Subsequent arguments are copies of the original function arguments
 2. **Execution Order**
  - Hooks are executed in registration order
  - Multiple hooks can be chained together
 3. **Return Value Handling**
  - For pre-hooks: Non-None return values are passed to the next hook or target function
   - When a hook returns None, the next hook will use the most recent non-None return value from previous hooks
   - If all previous hooks return None, the next hook receives a copy of the original arguments
   - The final non-None return value (or original arguments if all hooks return None) is passed to the target function
  - For post-hooks: Only the `post-reply` hook has a return value, which works the same way as pre-hooks.
 4. **Important**: Never call the target function (reply/speak/observe) within a hook to avoid infinite loops

Hooks Signatures
-----------------------

The signatures of the hook functions are as follows:
"""

from typing import Union, Tuple, Any, Dict

from agentscope.agents import AgentBase
from agentscope.message import Msg


def pre_reply_hook_template(
    self: AgentBase,
    args: Tuple[Any, ...],  # The positional arguments
    kwargs: Dict[str, Any],  # The keyword arguments
) -> Union[
    None,
    Tuple[
        Tuple[Any, ...],
        Dict[str, Any],
    ],  # The modified positional and keyword arguments
]:
    """Pre-reply hook template."""
    pass


def post_reply_hook_template(
    self: AgentBase,
    args: Tuple[Any, ...],  # The positional arguments
    kwargs: Dict[str, Any],  # The keyword arguments
    output: Msg,  # The output message
) -> Union[None, Msg]:  # The modified output message
    """Post-reply hook template."""
    pass


def pre_speak_hook_template(
    self: AgentBase,
    x: Msg,  # The message to be displayed
    stream: bool,  # Stream mode or not
    last: bool,  # If it's the last chunk message in stream mode
) -> Union[Msg, None]:  # The modified displayed message
    """Pre-speak hook template."""
    pass


def post_speak_hook_template(self: AgentBase) -> None:
    """Post-speak hook template."""
    pass


def pre_observe_hook_template(
    self: AgentBase,
    x: Union[Msg, list[Msg]],
) -> Union[None, Union[Msg, list[Msg]]]:  # The modified input message(s)
    """Pre-observe hook template."""
    pass


def post_observe_hook_template(self: AgentBase) -> None:
    """Post-observe hook template."""
    pass


# %%
# Examples
# -------------------
# AgentScope allows to register, remove and clear hooks by calling the
# corresponding methods.
#
# You can register hooks at both the **object** and **class** levels. Hooks at the
# **object** level are only effective for the current object, while hooks at the
# **class** level are effective for all objects of that class.
#
# .. note:: Object-level hooks are stored in the `_hooks_{hook_type}` attribute of the object, while class-level hooks are stored in the `_class_hooks_{hook_type}` attribute of the class.
#
# .. note:: For all hooks, the execution order is: object-level hooks --> class-level hooks
#
# Next we show how to use these hooks in AgentScope.
#
# Object-Level Hooks
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# We first create a simple agent that echoes the incoming message:

from typing import Optional, Union

from agentscope.agents import AgentBase
from agentscope.message import Msg


class TestAgent(AgentBase):
    def __init__(self):
        super().__init__(name="TestAgent")

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        return x


# %%
# Reply Hooks
# """""""""""""""""""""""""
# Next, we define two pre-hooks, which both modify the input message(s), but
# one return the modified message(s) and the other does not:


def pre_reply_hook_1(
    self,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Union[None, Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """The first pre-reply hook that changes the message content."""
    if isinstance(args[0], Msg):
        args[0].content = "[Pre-hook-1] " + args[0].content
    elif isinstance(args[0], list):
        for msg in args[0]:
            msg.content = "[Pre-hook-1] " + msg.content
    return None


def pre_reply_hook_2(
    self,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Union[None, Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """The second pre-reply hook that changes the message content."""
    if isinstance(args[0], Msg):
        args[0].content = "[Pre-hook-2] " + args[0].content
    elif isinstance(args[0], list):
        for msg in args[0]:
            msg.content = "[Pre-hook-2] " + msg.content
    return args, kwargs  # Return the modified input


# %%
# Then we create a post-hook that appends a suffix to the message content:


def post_reply_hook(self, args, kwargs, output) -> Msg:
    """The post-reply hook that appends a suffix to the message content."""
    output.content += " [Post-hook]"
    return output


# %%
# Finally, we register the hooks and test the agent:

agent = TestAgent()

agent.register_hook("pre_reply", "pre_hook_1", pre_reply_hook_1)
agent.register_hook("pre_reply", "pre_hook_2", pre_reply_hook_2)
agent.register_hook("post_reply", "post_hook", post_reply_hook)

msg = Msg("user", "[Original message]", "user")

msg_response = agent(msg)

print("The content of the response message:\n", msg_response.content)

# %%
# We can see that the response has one "[Pre-hook-2]" prefix and one
# "[Post-hook]" suffix. The "[Pre-hook-1]" prefix is missing because the
# first pre-hook does not return the modified message(s).
#
# Speak Hooks
# """""""""""""""""""""""""
# To be compatible with the streaming output, the pre-speak hook takes two
# additional arguments:
#
# - `stream`: a boolean flag indicating the streaming status
# - `last`: a boolean flag indicating if the input message is the last one in the stream
#
# .. tip:: When dealing with the streaming output, you can use the msg id to determine
#  whether two messages are from the same streaming output or not.
#
# We show how to use the pre/post-speak hooks below:

from agentscope.agents import DialogAgent
import agentscope

agentscope.init(
    model_configs=[
        {
            "config_name": "streaming_config",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
            "stream": True,
        },
    ],
)

streaming_agent = DialogAgent(
    name="TestAgent",
    model_config_name="streaming_config",
    sys_prompt="You're a helpful assistant.",
)


# Create a pre-speak hook that displays the message content
def pre_speak_hook(self, x: Msg, stream: bool, last: bool) -> Msg:
    """The pre-speak hook that display the message content."""
    # You can change or redirect the message here
    print(
        "id: ",
        x.id,
        "stream: ",
        stream,
        "last: ",
        last,
        "content: ",
        x.content,
    )
    # Avoid printing message disorder
    x.content = ""
    return x


def post_speak_hook(self) -> None:
    """The post-speak hook that display the message content."""
    # We count the number of calling the speak function here.
    if not hasattr(self, "cnt"):
        self.cnt = 0
    self.cnt += 1


# Register the hooks
streaming_agent.register_hook("pre_speak", "pre_speak_hook", pre_speak_hook)
streaming_agent.register_hook("post_speak", "post_speak_hook", post_speak_hook)

# %%
# Now let's test the `speak` hooks:

msg = Msg(
    "user",
    "Now, count from 1 to 15, step by 1 and separate each number by a comma.",
    "user",
)

msg_response = streaming_agent(msg)

print("The cnt of calling the speak function:", streaming_agent.cnt)


# %%
# Observe Hooks
# """""""""""""""""""""""""
# Similar as the speak hooks, we show how to use the pre/post-observe hooks
# below:

import json


def pre_observe_hook(self, x: Union[Msg, list[Msg]]) -> Union[Msg, list[Msg]]:
    """The pre-observe hook that display the message content."""
    if isinstance(x, Msg):
        x.content = "Observe: " + x.content
    elif isinstance(x, list):
        for msg in x:
            msg.content = "Observe: " + msg.content
    return x


def post_observe_hook(self) -> None:
    """The post-observe hook that display the message content."""
    if not hasattr(post_observe_hook, "cnt"):
        setattr(post_observe_hook, "cnt", 0)
    post_observe_hook.cnt += 1


# Clear the memory first
agent.memory.clear()

agent.register_hook("pre_observe", "pre_observe_hook", pre_observe_hook)
agent.register_hook("post_observe", "post_observe_hook", post_observe_hook)

agent.observe(
    Msg(
        "user",
        "The sun is shining.",
        "user",
    ),
)

print(
    "The content of the memory:\n",
    json.dumps([_.to_dict() for _ in agent.memory.get_memory()], indent=4),
)
print("The cnt of calling the observe function:", post_observe_hook.cnt)

# %%
# Class-Level Hooks
# ^^^^^^^^^^^^^^^^^^^^^^
#
# The class-level hooks are similar to the object-level hooks, but they are
# registered at the class level and are effective for all objects of that
# class.
#
# The register, remove and clear methods for class-level hooks are prefixed
# by "class"
#

# Create a new agent object
agent2 = TestAgent()

AgentBase.clear_all_class_hooks()

print("hooks of agent:", list(agent._class_hooks_pre_reply.keys()))
print("hooks of agent2:", list(agent2._class_hooks_pre_reply.keys()))

# %%
# Next, we register a class-level pre-reply hook and test it:

# Register a class-level pre-reply hook
AgentBase.register_class_hook("pre_reply", "class_hook_1", pre_reply_hook_1)

print("hooks of agent:", list(agent._class_hooks_pre_reply.keys()))
print("hooks of agent2:", list(agent2._class_hooks_pre_reply.keys()))

# %%
# We can see that the class-level hook is effective for all objects of
# that class.
