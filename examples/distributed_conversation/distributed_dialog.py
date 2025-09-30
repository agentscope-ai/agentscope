# -*- coding: utf-8 -*-
""" An example of distributed dialog """

import argparse
from loguru import logger

import agentscope
from agentscope.agent import UserAgent, DialogAgent
from agentscope.server import RpcAgentServerLauncher


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=["assistant", "user"],
        default="user",
    )
    parser.add_argument(
        "--assistant-port",
        type=int,
        default=12010,
    )
    parser.add_argument(
        "--assistant-host",
        type=str,
        default="localhost",
    )
    return parser.parse_args()


def setup_assistant_server(assistant_host: str, assistant_port: int) -> None:
    """Set up assistant rpc server"""
    agentscope.init(
        model_configs="configs/model_configs.json",
        project="Distributed Conversation",
    )
    assistant_server_launcher = RpcAgentServerLauncher(
        host=assistant_host,
        port=assistant_port,
    )
    assistant_server_launcher.launch()
    assistant_server_launcher.wait_until_terminate()


def run_main_process(assistant_host: str, assistant_port: int) -> None:
    """Run dialog main process"""
    agentscope.init(
        model_configs="configs/model_configs.json",
        project="Distributed Conversation",
    )
    assistant_agent = DialogAgent(
        name="Assistant",
        sys_prompt="You are a helpful assistant.",
        model_config_name="qwen",
        use_memory=True,
    ).to_dist(
        host=assistant_host,
        port=assistant_port,
    )
    user_agent = UserAgent(
        name="User",
        require_url=False,
    )
    logger.info(
        "Setup successfully, have fun chatting! (enter 'exit' to close the "
        "agent)",
    )
    msg = user_agent()
    while not msg.content.endswith("exit"):
        msg = assistant_agent(msg)
        logger.chat(msg.result())
        msg = user_agent(msg)


if __name__ == "__main__":
    args = parse_args()
    if args.role == "assistant":
        setup_assistant_server(args.assistant_host, args.assistant_port)
    elif args.role == "user":
        run_main_process(args.assistant_host, args.assistant_port)
