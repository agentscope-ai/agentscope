# -*- coding: utf-8 -*-
"""An env used as a chatroom."""
from typing import List, Any, Union, Generator, Tuple, Optional
from copy import deepcopy
import re
import random
import threading
import time
from loguru import logger

from agentscope.agents import (
    UserAgent,
    AgentBase,
)
from agentscope.message import Msg
from agentscope.exception import (
    EnvListenerError,
)
from agentscope.environment import (
    Env,
    BasicEnv,
    EventListener,
    Event,
    event_func,
)
from agentscope.model import ModelResponse
from agentscope.manager import ModelManager


CHATROOM_TEMPLATE = """
======= CHATROOM BEGIN ========

## ANNOUNCEMENT:
{announcement}

## HISTORY:
{history}

======= CHATROOM END ========
"""


def format_messages(msgs: Union[Msg, List[Msg]]) -> list[dict]:
    """Format the messages"""
    messages = []
    if isinstance(msgs, Msg):
        msgs = [msgs]
    for msg in msgs:
        messages.append(
            {
                "role": msg.role,
                "name": msg.name,
                "content": str(msg.content),
            },
        )
    return messages


class ChatRoomMember(BasicEnv):
    """A member of chatroom."""

    def __init__(
        self,
        name: str,
        agent: AgentBase,
        history_idx: int = 0,
    ) -> None:
        super().__init__(name)
        self._agent = agent
        self._history_idx = history_idx

    @property
    def agent_name(self) -> str:
        """Get the name of the agent."""
        return self._agent.name

    @property
    def history_idx(self) -> int:
        """Get the history index of the agent."""
        return self._history_idx

    @property
    def agent(self) -> AgentBase:
        """Get the agent of the member."""
        return self._agent

    def chat_freely(
        self,
        delay: float = 5,
        interval: float = 3,
        max_round: int = 10,
    ) -> None:
        """Let the agent chat freely"""
        sleep_time = random.random() * delay
        time.sleep(sleep_time)
        for _ in range(max_round):
            msg = self._agent()
            if "goodbye" in msg.content.lower():
                break
            time.sleep(interval)

    def chat(self) -> None:
        """call the agent to chat"""
        self._agent()


class ChatRoom(BasicEnv):
    """A chatroom env."""

    def __init__(
        self,
        name: str = None,
        model_config_name: str = None,
        announcement: Msg = None,
        participants: List[AgentBase] = None,
        all_history: bool = False,
        use_mention: bool = True,
        **kwargs: Any,
    ) -> None:
        """Init a ChatRoom instance.

        Args:
            name (`str`): The name of the chatroom.
            announcement (`Msg`): The announcement message.
            participants (`List[AgentBase]`): A list of agents
            all_history (`bool`): If `True`, new participant can see all
            history messages, else only messages generated after joining
            can be seen. Default to `False`.
            use_mention (`bool`): If `True`, the agent can mention other
            agents by @name. Default to `True`.
        """
        super().__init__(
            name=name,
            **kwargs,
        )
        self.children = {}
        for p in participants if participants else []:
            self.join(p)
        self.event_listeners = {}
        self.all_history = all_history
        if use_mention:
            self.add_listener(
                "speak",
                listener=Notifier(),
            )
        self.history = []
        self.announcement = announcement
        self.member_introduction = {}
        if model_config_name is not None:
            model_manager = ModelManager.get_instance()
            self.model = model_manager.get_model_by_config_name(
                model_config_name,
            )

    @event_func
    def join(self, agent: AgentBase) -> bool:
        """Add a participant to the chatroom."""
        if agent.name in self.children:
            return False
        self.children[agent.name] = ChatRoomMember(
            name=agent.name,
            agent=agent,
            history_idx=len(self.history),
        )
        self.member_introduction[agent.name] = agent.introduction
        self.add_listener("speak", Notifier())
        return True

    @event_func
    def leave(self, agent: AgentBase) -> bool:
        """Remove the participant agent from the chatroom."""
        if agent.name not in self.children:
            return False
        del self.children[agent.name]
        del self.member_introduction[agent.name]
        return True

    @event_func
    def speak(self, message: Msg) -> None:
        """Speak a message in the chatroom."""
        self.history.append(message)

    @event_func
    def get_history(self, agent_name: str) -> List[Msg]:
        """Get all history messages, since the participant join in the
        chatroom"""
        if agent_name not in self.children:
            # only participants can get history message
            return []
        if self.all_history:
            history_idx = 0
        else:
            history_idx = self.children[agent_name].history_idx
        return deepcopy(self.history[history_idx:])

    def get_history_length(self, agent_name: str) -> int:
        """Get the length of the history of the agent."""
        if agent_name not in self.children:
            return 0
        if self.all_history:
            history_idx = 0
        else:
            history_idx = self.children[agent_name].history_idx
        return len(self.history) - history_idx

    def describe(self, agent_name: str, **kwargs: Any) -> str:
        """Get the description of the chatroom."""
        ann = self.announcement.content if self.announcement.content else ""
        members_introduction = "\n\n".join(
            [
                f"{name}: {introduction}"
                for name, introduction in self.member_introduction.items()
            ],
        )
        ann += f"\n{members_introduction}\n\n"
        ann += (
            """Please generate a suitable response in this work group based"""
            """ on the following chat history. When you need to mention """
            """someone, you can use @ to remind them. You only need to """
            f"""output {agent_name}'s possible replies, without giving """
            """anyone else's replies or continuing the conversation."""
        )
        history = "\n\n".join(
            [
                f"{msg.name}: {msg.content}"
                for msg in self.get_history(agent_name)
            ],
        )
        return CHATROOM_TEMPLATE.format(
            announcement=ann,
            history=history,
        )

    @event_func
    def set_announcement(self, announcement: Msg) -> None:
        """Set the announcement of the chatroom."""
        self.announcement = announcement

    @event_func
    def get_announcement(self) -> Msg:
        """Get the announcement of the chatroom."""
        return deepcopy(self.announcement)

    # Syntactic sugar, not an event function
    def listen_to(
        self,
        target_names: List[str],
        listener: EventListener,
    ) -> None:
        """The listener will be called when a message whose name is in
        `target_names` is send to the chatroom."""
        if target_names is None or len(target_names) == 0:
            return

        class ListenTo(EventListener):
            """A middleware that activates `target_listener`"""

            def __init__(
                self,
                name: str,
                target_names: List[str],
                target_listener: EventListener,
            ) -> None:
                super().__init__(name=name)
                self.target_names = target_names
                self.target_listener = target_listener

            def __call__(self, env: Env, event: Event) -> None:
                if event.args["message"].name in self.target_names:
                    self.target_listener(env, event)

        if not self.add_listener(
            "speak",
            listener=ListenTo(
                name=f"listen_to_{listener.name}",
                target_names=target_names,
                target_listener=listener,
            ),
        ):
            raise EnvListenerError("Fail to add listener.")

    def chatting_parse_func(self, response: ModelResponse) -> ModelResponse:
        """Parse the response of the chatting agent."""
        pattern_str = ""
        for child in self.children.values():
            if pattern_str:
                pattern_str += "|"
            pattern_str += rf"""\s?{child.agent_name}: """
        pattern = re.compile(pattern_str, re.DOTALL)
        logger.debug(repr(pattern_str))
        logger.debug(response.text)
        texts = [s.strip() for s in pattern.split(response.text) if s.strip()]
        logger.debug(texts)
        return ModelResponse(text=texts[0])

    def chat_freely(
        self,
        delay: float = 1,
        interval: float = 5,
        max_round: int = 10,
        agent_name_list: List[str] = None,
    ) -> None:
        """Let all agents to chat freely without any preset order"""
        tasks = []
        if agent_name_list is None:
            agent_name_list = list(self.children.keys())
        for agent_name in agent_name_list:
            task = threading.Thread(
                target=self.children[agent_name].chat_freely,
                kwargs={
                    "delay": delay,
                    "interval": interval,
                    "max_round": max_round,
                },
            )
            tasks.append(task)
            task.start()
        for task in tasks:
            task.join()

    def chat_in_sequence(self, agent_name_order: List[str] = None) -> None:
        """Let all agents chat in sequence

        Args:
            agent_name_order (`List[str]`): Order of speakers' names.
        """
        agent_name_order = agent_name_order or list(self.children.keys())
        for agent_name in agent_name_order:
            self.children[agent_name].chat()


class Notifier(EventListener):
    """A listener that will call the mentioned agent"""

    def __init__(
        self,
    ) -> None:
        super().__init__(name="mentioned_notifier")
        self.pattern = re.compile(r"(?<=@)\w+")

    def __call__(self, room: Env, event: Event) -> None:
        names = self.pattern.findall(str(event.args["message"].content))
        names = list(set(names))

        for name in names:
            if name in room.children:
                logger.info(
                    f"{event.args['message'].name} mentioned {name}.",
                )
                room.children[name].agent.add_mentioned_message(
                    event.args["message"],
                )


class ChatRoomAgent(UserAgent):
    """
    An agent in a chatroom.
    """

    def __init__(  # pylint: disable=W0613
        self,
        name: str,
        sys_prompt: str,
        model_config_name: str,
        **kwargs: Any,
    ) -> None:
        self.name = name
        self.sys_prompt = sys_prompt.format(name=name)
        self.model = ModelManager.get_instance().get_model_by_config_name(
            model_config_name,
        )

        if self.sys_prompt:
            prompt = format_messages(
                [
                    Msg(
                        name="user",
                        role="user",
                        content=(
                            f"Please generate a brief character introduction "
                            f"in one sentence, which based on the following "
                            f"prompt:\n"
                            f"Prompt: {sys_prompt}\n"
                            f"The generated description needs to follow the "
                            f"following format:\n"
                            f"[PERSONA BEGIN]\n"
                            f"Description: One sentence introduction\n"
                            f"[PERSONA END]"
                        ),
                    ),
                ],
            )
            raw_introduction = self.model(prompt).text
            raw_introduction = raw_introduction.split("[PERSONA BEGIN]", 1)[1]
            raw_introduction = raw_introduction.split("[PERSONA END]")[0]
            self.introduction = raw_introduction.strip()
        else:
            self.introduction = ""
        logger.info(f"introduction: {self.introduction}")
        self.room_history_length = 0
        self.room_silent_count = 0
        self.room = None
        self.mentioned_messages = []
        self.mentioned_messages_lock = threading.Lock()

    def add_mentioned_message(self, msg: Msg) -> None:
        """Add mentioned messages"""
        with self.mentioned_messages_lock:
            self.mentioned_messages.append(msg)

    def join(self, room: ChatRoom) -> bool:
        """Join a room"""
        self.room = room
        self.room_history_length = self.room.get_history_length(self.name)
        return room.join(self)

    def _is_mentioned(self) -> bool:
        """Check whether the agent is mentioned"""
        return bool(self.mentioned_messages)

    def _generate_mentioned_prompt(self) -> Tuple[bool, str]:
        """Generate a hint for the agent"""
        with self.mentioned_messages_lock:
            if len(self.mentioned_messages) > 0:
                hint = "You have been mentioned in the following messages:\n"
                hint += "\n".join(
                    [
                        f"{msg.name}: {msg.content}"
                        for msg in self.mentioned_messages
                    ],
                )
                self.mentioned_messages = []
                return True, hint
            return False, ""

    def _want_to_speak(self, hint: str) -> bool:
        """Check whether the agent want to speak currently"""
        hint = (
            f"{self.sys_prompt}\n\nYou are participating in a chatroom.\n"
            + hint
        )
        prompt = format_messages(
            [
                Msg(name="system", role="system", content=hint),
                Msg(
                    name="user",
                    role="user",
                    content="Based on the CHATROOM."
                    " Do you want to or need to speak in the chatroom now?\n"
                    "Return yes or no.",
                ),
            ],
        )
        logger.debug(prompt)
        response = self.model(
            prompt,
            max_retries=3,
        ).text
        logger.info(f"[SPEAK OR NOT] {self.name}: {response}")
        return "yes" in response.lower()

    def speak(
        self,
        content: Union[str, Msg, Generator[Tuple[bool, str], None, None]],
    ) -> None:
        """Speak to room.

        Args:
            content
            (`Union[str, Msg, Generator[Tuple[bool, str], None, None]]`):
                The content of the message to be spoken in chatroom.
        """
        super().speak(content)
        self.room.speak(content)

    def reply(self, x: Msg = None) -> Msg:
        """Generate reply to chat room"""
        room_history_length = self.room.get_history_length(self.name)
        if room_history_length != self.room_history_length:
            self.room_history_length = room_history_length
            self.room_silent_count = 0
        else:
            self.room_silent_count += 1
        room_info = self.room.describe(self.name)
        reply_hint = ""
        mentioned, mentioned_hint = self._generate_mentioned_prompt()
        if mentioned:
            reply_hint = f"{mentioned_hint}\n{self.name}:"
        else:
            # decide whether to speak
            if self.room_history_length <= 3 or (
                self.room_silent_count <= 2 and self._want_to_speak(room_info)
            ):
                reply_hint = (
                    f"Please generate a response based on the"
                    f" CHATROOM. You need only generate response without "
                    f"reasoning.\n{self.name}:"
                )
            else:
                return Msg(name="assistant", role="assistant", content="")
        user_hint = (
            # f"{self.sys_prompt}\n\n"
            f"You are participating in a chatroom.\n"
            f"\n{room_info}\n{reply_hint}"
        )
        prompt = format_messages(
            [
                Msg(
                    name="system",
                    role="system",
                    content=self.sys_prompt,
                ),
                Msg(name="user", role="user", content=user_hint),
            ],
        )
        prompt[-1]["content"] = prompt[-1]["content"].strip()
        logger.debug(prompt)
        response = self.model(
            prompt,
            parse_func=self.room.chatting_parse_func,
            max_retries=3,
        ).text
        msg = Msg(name=self.name, content=response, role="assistant")
        if response:
            self.speak(msg)
        self.room_history_length = self.room.get_history_length(self.name)
        return msg


class ChatRoomAgentWithAssistant(ChatRoomAgent):
    """A ChatRoomAgent with assistant"""

    def __init__(
        self,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.timeout = timeout
        self.room_history_length = 0

    def reply(self, x: Msg = None) -> Msg:
        input_data = self._input_method(
            agent_id=self.agent_id,
            agent_name=self.name,
            structured_schema=None,
        )

        if (
            input_data.blocks_input
            and len(input_data.blocks_input) == 1
            and input_data.blocks_input[0].get("type") == "text"
        ):
            # Turn blocks_input into a string if only one text block exists
            content = input_data.blocks_input[0].get("text")
        else:
            content = input_data.blocks_input

        if content is not None:  # user input
            response = content
        else:  # assistant reply
            room_history_length = self.room.get_history_length(self.name)
            if room_history_length == self.room_history_length:
                return Msg(name="assistant", role="assistant", content="")
            self.room_history_length = room_history_length
            room_info = self.room.describe(self.name)
            reply_hint = ""
            mentioned, mentioned_hint = self._generate_mentioned_prompt()
            if mentioned:
                reply_hint = f"{mentioned_hint}\n{self.name}:"
            else:
                reply_hint = (
                    f"Please generate a response based on the CHATROOM."
                    f"\n{self.name}:"
                )
            system_hint = (
                f"You are participating in a chatroom.\n"
                f"\n{room_info}\n{reply_hint}"
            )

            prompt = format_messages(
                [
                    Msg(
                        name=self.name,
                        content=self.sys_prompt,
                        role="system",
                    ),
                    Msg(name="user", content=system_hint, role="user"),
                ],
            )
            logger.debug(prompt)
            response = self.model(
                prompt,
                parse_func=self.room.chatting_parse_func,
                max_retries=3,
            ).text
            if not response.startswith("[auto reply]"):
                response = "[auto reply] " + response
        msg = Msg(name=self.name, content=response, role="user")
        self.speak(msg)
        self.room_history_length = self.room.get_history_length(self.name)
        return msg
