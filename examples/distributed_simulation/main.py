# -*- coding: utf-8 -*-
""" A large-scale social simulation experiment """

import argparse
import time
import math
from concurrent import futures
from concurrent.futures import as_completed
from loguru import logger

from participant import Moderator, RandomParticipant, LLMParticipant

import agentscope
from agentscope.agent import AgentBase
from agentscope.server import RpcAgentServerLauncher
from agentscope.message import Msg


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=["participant", "main"],
        default="main",
    )
    parser.add_argument(
        "--agent-type",
        choices=["random", "llm"],
        default="random",
    )
    parser.add_argument("--max-value", type=int, default=100)
    parser.add_argument("--sleep-time", type=float, default=1.0)
    parser.add_argument(
        "--hosts",
        type=str,
        nargs="+",
        default=["localhost"],
    )
    parser.add_argument("--participant-num", type=int, default=100)
    parser.add_argument("--base-port", type=int, default=12010)
    parser.add_argument(
        "--server-per-host",
        type=int,
    )
    parser.add_argument("--model-per-host", type=int, default=1)
    parser.add_argument("--moderator-per-host", type=int, default=1)
    return parser.parse_args()


def setup_participant_agent_server(host: str, port: int) -> None:
    """Set up agent server"""
    agentscope.init(
        project="simulation",
        name="server",
        runtime_id=str(port),
        save_code=False,
        save_api_invoke=False,
        model_configs="configs/model_configs.json",
        use_monitor=False,
    )
    assistant_server_launcher = RpcAgentServerLauncher(
        host=host,
        port=port,
        max_pool_size=16384,
        custom_agent_classes=[Moderator, RandomParticipant, LLMParticipant],
    )
    assistant_server_launcher.launch(in_subprocess=False)
    assistant_server_launcher.wait_until_terminate()


def init_moderator(
    name: str,
    configs: list[dict],
    host: str,
    port: int,
    agent_type: str,
    max_value: int,
    sleep_time: float,
) -> AgentBase:
    """Init moderator"""
    return Moderator(  # pylint: disable=E1123
        name=name,
        part_configs=configs,
        agent_type=agent_type,
        max_value=max_value,
        sleep_time=sleep_time,
        to_dist={
            "host": host,
            "port": port,
        },
    )


def run_main_process(
    hosts: list[str],
    base_port: int,
    server_per_host: int,
    model_per_host: int,
    participant_num: int,
    moderator_per_host: int = 10,
    agent_type: str = "random",
    max_value: int = 100,
    sleep_time: float = 1.0,
) -> None:
    """Run main process"""
    agentscope.init(
        project="simulation",
        name="main",
        save_code=False,
        save_api_invoke=False,
        model_configs="configs/model_configs.json",
        use_monitor=False,
    )
    host_num = len(hosts)
    total_agent_server_num = server_per_host * host_num
    participant_per_agent_server = math.ceil(
        participant_num / total_agent_server_num,
    )
    ist = time.time()
    configs = []
    logger.info(f"init {participant_num} {agent_type} participant agents...")
    # build init configs of participants
    for i in range(participant_num):
        idx = i // participant_per_agent_server
        host_id = idx // server_per_host
        port_id = idx % server_per_host
        model_id = i % model_per_host
        host = hosts[host_id]
        port = base_port + port_id
        config_name = f"model_{model_id + 1}"
        if agent_type == "random":
            configs.append(
                {
                    "name": f"P{i}",
                    "host": host,
                    "port": port,
                },
            )
        else:
            configs.append(
                {
                    "name": f"P{i}",
                    "model_config_name": config_name,
                    "host": host,
                    "port": port,
                },
            )

    mods = []
    moderator_num = moderator_per_host * host_num
    participant_per_moderator = participant_num // moderator_num
    tasks = []

    logger.info(f"init {moderator_num} moderator agents...")
    # init moderators
    with futures.ThreadPoolExecutor(max_workers=None) as executor:
        for i in range(moderator_num):
            tasks.append(
                executor.submit(
                    init_moderator,
                    name=f"mod_{i}",
                    configs=configs[
                        i
                        * participant_per_moderator : (i + 1)  # noqa
                        * participant_per_moderator
                    ],
                    host=hosts[i // moderator_per_host],
                    port=base_port + server_per_host + i % moderator_per_host,
                    agent_type=agent_type,
                    max_value=max_value,
                    sleep_time=sleep_time,
                ),
            )
        for task in as_completed(tasks):
            mods.append(task.result())

    iet = time.time()
    logger.info(f"[init takes {iet - ist} s]")

    # run te
    st = time.time()
    results = []
    for p in mods:
        results.append(p())
    summ = 0
    cnt = 0
    for r in results:
        try:
            summ += int(r.content["sum"])
            cnt += int(r.content["cnt"])
        except Exception:
            logger.error(r.content)
    et = time.time()
    logger.chat(
        Msg(
            name="Moderator",
            role="assistant",
            content=f"The average value is {summ / cnt} [takes {et - st} s]",
        ),
    )


if __name__ == "__main__":
    args = parse_args()
    if args.role == "participant":
        setup_participant_agent_server(args.hosts[0], args.base_port)
    elif args.role == "main":
        run_main_process(
            hosts=args.hosts,
            base_port=args.base_port,
            participant_num=args.participant_num,
            server_per_host=args.server_per_host,
            model_per_host=args.model_per_host,
            moderator_per_host=args.moderator_per_host,
            agent_type=args.agent_type,
            sleep_time=args.sleep_time,
            max_value=args.max_value,
        )
