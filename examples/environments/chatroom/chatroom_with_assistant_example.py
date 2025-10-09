# -*- coding: utf-8 -*-
"""A simple example of chatroom with chatting assistant."""

import os
import argparse

from envs.chatroom import ChatRoom, ChatRoomAgent, ChatRoomAgentWithAssistant

import agentscope
from agentscope.message import Msg


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--logger-level",
        choices=["DEBUG", "INFO"],
        default="INFO",
    )
    parser.add_argument(
        "--use-dist",
        action="store_true",
    )
    parser.add_argument(
        "--studio-url",
        default=None,
        type=str,
    )
    parser.add_argument(
        "--timeout",
        default=5,
        type=int,
    )
    return parser.parse_args()


def main(args: argparse.Namespace) -> None:
    """Example for chatroom with assistant"""
    # Prepare the model configuration
    YOUR_MODEL_CONFIGURATION_NAME = "dash"
    YOUR_MODEL_CONFIGURATION = [
        {
            "model_type": "dashscope_chat",
            "config_name": "dash",
            "model_name": "qwen-turbo",
            "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        },
    ]

    # Initialize the agent
    agentscope.init(
        model_configs=YOUR_MODEL_CONFIGURATION,
        use_monitor=False,
        logger_level=args.logger_level,
        studio_url=args.studio_url,
    )

    ann = Msg(name="", content="", role="system")
    r = ChatRoom(name="chat", announcement=ann, to_dist=args.use_dist)

    # Setup the persona of Alice and Bob
    alice = ChatRoomAgent(
        name="Alice",
        sys_prompt=r"""""",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
    )
    alice.join(r)

    bob = ChatRoomAgentWithAssistant(
        name="Bob",
        sys_prompt=r"""You are Bob's chat room assistant and Bob is """
        r"""currently unable to reply to messages. Please generate a """
        r"""suitable response based on the following chat history without """
        r"""reasoning. The content you reply to must be based on the chat """
        r"""history. Please refuse to reply to questions that are beyond """
        r"""the scope of the chat history.""",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
        timeout=args.timeout,
    )
    bob.join(r)

    # Setup some chatting history
    alice.speak(
        Msg(
            name="Alice",
            content=(
                "Hi Bob, nice to meet you. "
                "Can you tell me a bit about yourself?"
            ),
            role="assistant",
        ),
    )
    bob.speak(
        Msg(
            name="Bob",
            content=(
                "Of course, nice to meet you too, Alice. "
                "I'm originally from Hunan, a beautiful province in southern "
                "China known for its spicy food and stunning natural scenery."
            ),
            role="user",
        ),
    )
    alice.speak(
        Msg(
            name="Alice",
            content=(
                "Oh, that sounds fascinating! "
                "So, what do you do for a living, Bob?"
            ),
            role="assistant",
        ),
    )
    bob.speak(
        Msg(
            name="Bob",
            content=(
                "I work as a software engineer. I've been in this field for "
                "about 5 years now, designing and developing various "
                "applications and systems. It's a challenging but rewarding "
                "job that keeps me on my toes."
            ),
            role="user",
        ),
    )
    alice.speak(
        Msg(
            name="Alice",
            content=(
                "That's great! It takes a lot of skill and creativity to be "
                "a good software engineer. Speaking of creativity, do you "
                "have any hobbies or activities you enjoy outside of work?"
            ),
            role="assistant",
        ),
    )
    bob.speak(
        Msg(
            name="Bob",
            content=(
                "Yes, I'm quite passionate about playing board games. "
                "There's something really enjoyable about the strategy, "
                "competition, and social interaction they provide. Whether "
                "it's classic games like chess or more modern ones like "
                "Settlers of Catan, I find them all very engaging."
            ),
            role="user",
        ),
    )
    alice.speak(
        Msg(
            name="Alice",
            content=(
                "Board games are a wonderful way to unwind and connect with "
                "friends and family. It sounds like you have a great balance "
                "between your professional and personal life, Bob. "
                "Thanks for sharing!"
            ),
            role="assistant",
        ),
    )
    bob.speak(
        Msg(
            name="Bob",
            content=(
                "Absolutely, thank you for asking, Alice. "
                "It was a pleasure chatting with you."
            ),
            role="user",
        ),
    )

    # Setup the persona of Carol
    carol = ChatRoomAgent(
        name="Carol",
        sys_prompt="""You are Carol, and now you need to interview Bob. """
        """Just ask him where he is from, which school he graduated from, """
        """his profession, and his hobbies. You'd better only ask one """
        """question at a time.""",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
    )
    carol.join(r)

    # Start the chat
    r.chat_freely(
        delay=10,
        interval=10,
        max_round=10,
    )


if __name__ == "__main__":
    main(parse_args())
