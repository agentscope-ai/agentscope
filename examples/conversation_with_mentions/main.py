# -*- coding: utf-8 -*-
""" A group chat where user can talk any time implemented by agentscope. """
from groupchat_utils import (
    select_next_one,
    filter_agents,
)

import agentscope
from agentscope.agent import UserAgent
from agentscope.message import Msg
from agentscope.msghub import msghub

USER_TIME_TO_SPEAK = 10
DEFAULT_TOPIC = """
This is a chat room and you can speak freely and briefly.
"""

SYS_PROMPT = """
You can designate a member to reply to your message, you can use the @ symbol.
This means including the @ symbol in your message, followed by
that person's name, and leaving a space after the name.

All participants are: {agent_names}
"""


def main() -> None:
    """group chat"""
    npc_agents = agentscope.init(
        model_configs="./configs/model_configs.json",
        agent_configs="./configs/agent_configs.json",
        project="Conversation with Mentions",
    )

    user = UserAgent()

    agents = list(npc_agents) + [user]

    hint = Msg(
        name="Host",
        content=DEFAULT_TOPIC
        + SYS_PROMPT.format(
            agent_names=[agent.name for agent in agents],
        ),
        role="assistant",
    )

    rnd = 0
    speak_list = []
    with msghub(agents, announcement=hint):
        while True:
            x = user(timeout=USER_TIME_TO_SPEAK)
            if x.content == "exit":
                break

            speak_list += filter_agents(x.content, npc_agents)

            if len(speak_list) > 0:
                next_agent = speak_list.pop(0)
                x = next_agent()
            else:
                next_agent = select_next_one(npc_agents, rnd)
                x = next_agent()

            speak_list += filter_agents(x.content, npc_agents)

            rnd += 1


if __name__ == "__main__":
    main()
