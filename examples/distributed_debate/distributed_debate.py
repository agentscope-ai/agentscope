# -*- coding: utf-8 -*-
""" An example of distributed debate """
import argparse

from user_proxy_agent import UserProxyAgent


import agentscope
from agentscope.agent import DialogAgent
from agentscope.msghub import msghub
from agentscope.server import RpcAgentServerLauncher
from agentscope.message import Msg


FIRST_ROUND = """
Welcome to the debate on whether Artificial General Intelligence (AGI) can be achieved using the GPT model framework. This debate will consist of three rounds. In each round, the affirmative side will present their argument first, followed by the negative side. After both sides have presented, the adjudicator will summarize the key points and analyze the strengths of the arguments.

The rules are as follows:

Each side must present clear, concise arguments backed by evidence and logical reasoning.
No side may interrupt the other while they are presenting their case.
After both sides have presented, the adjudicator will have time to deliberate and will then provide a summary, highlighting the most persuasive points from both sides.
The adjudicator's summary will not declare a winner for the individual rounds but will focus on the quality and persuasiveness of the arguments.
At the conclusion of the three rounds, the adjudicator will declare the overall winner based on which side won two out of the three rounds, considering the consistency and strength of the arguments throughout the debate.
Let us begin the first round. The affirmative side: please present your argument for why AGI can be achieved using the GPT model framework.
"""  # noqa

SECOND_ROUND = """
Let us begin the second round. It's your turn, the affirmative side.
"""

THIRD_ROUND = """
Next is the final round.
"""

END = """
Judge, please declare the overall winner now.
"""


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=["pro", "con", "main"],
        default="main",
    )
    parser.add_argument("--is-human", action="store_true")
    parser.add_argument("--pro-host", type=str, default="localhost")
    parser.add_argument(
        "--pro-port",
        type=int,
        default=12011,
    )
    parser.add_argument("--con-host", type=str, default="localhost")
    parser.add_argument(
        "--con-port",
        type=int,
        default=12012,
    )
    parser.add_argument("--judge-host", type=str, default="localhost")
    parser.add_argument(
        "--judge-port",
        type=int,
        default=12013,
    )
    return parser.parse_args()


def setup_server(parsed_args: argparse.Namespace) -> None:
    """Setup rpc server for participant agent"""
    agentscope.init(
        model_configs="configs/model_configs.json",
        project="Distributed Conversation",
    )
    host = getattr(parsed_args, f"{parsed_args.role}_host")
    port = getattr(parsed_args, f"{parsed_args.role}_port")
    server_launcher = RpcAgentServerLauncher(
        host=host,
        port=port,
        custom_agent_classes=[UserProxyAgent, DialogAgent],
    )
    server_launcher.launch(in_subprocess=False)
    server_launcher.wait_until_terminate()


def print_msg(msg: Msg) -> None:
    """Print message"""
    print(msg.formatted_str(colored=True))


def run_main_process(parsed_args: argparse.Namespace) -> None:
    """Setup the main debate competition process"""
    pro_agent, con_agent, judge_agent = agentscope.init(
        model_configs="configs/model_configs.json",
        agent_configs="configs/debate_agent_configs.json",
        project="Distributed Conversation",
    )
    judge_agent = judge_agent.to_dist()
    pro_agent = pro_agent.to_dist(
        host=parsed_args.pro_host,
        port=parsed_args.pro_port,
    )
    con_agent = con_agent.to_dist(
        host=parsed_args.con_host,
        port=parsed_args.con_port,
    )
    participants = [pro_agent, con_agent, judge_agent]
    announcements = [
        Msg(name="system", content=FIRST_ROUND, role="system"),
        Msg(name="system", content=SECOND_ROUND, role="system"),
        Msg(name="system", content=THIRD_ROUND, role="system"),
    ]
    end = Msg(name="system", content=END, role="system")
    with msghub(participants=participants) as hub:
        for i in range(3):
            hub.broadcast(announcements[i])
            pro_resp = pro_agent()
            print_msg(pro_resp)
            con_resp = con_agent()
            print_msg(con_resp)
            judge_agent()
        hub.broadcast(end)
        judge_agent()


if __name__ == "__main__":
    args = parse_args()
    if args.role == "main":
        run_main_process(args)
    else:
        setup_server(args)
