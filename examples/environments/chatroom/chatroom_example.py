# -*- coding: utf-8 -*-
"""A simple example of chatroom with three agent."""

import os
import argparse

from envs.chatroom import ChatRoom, ChatRoomAgent

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
    return parser.parse_args()


def main(args: argparse.Namespace) -> None:
    """Example for chatroom"""
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

    ann = Msg(
        name="Boss",
        content=(
            "This is a game development work group, "
            "please discuss how to develop an open world game."
        ),
        role="system",
    )
    r = ChatRoom(
        name="chat",
        announcement=ann,
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
    )

    # Setup the persona of Alice, Bob and Carol
    alice = ChatRoomAgent(  # Game Art Designer
        name="Alice",
        sys_prompt=r"""You are a game art designer named Alice.""",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
    )
    alice.join(r)

    bob = ChatRoomAgent(  # Game Programmer
        name="Bob",
        sys_prompt=r"""You are a game programmer named Bob.""",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        to_dist=args.use_dist,
    )
    bob.join(r)

    carol = ChatRoomAgent(  # Game Designer
        name="Carol",
        sys_prompt=r"""You are a game planner named Carol.""",
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
