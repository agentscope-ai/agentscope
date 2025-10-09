# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301,R1732,W0613,R1716,W0622
"""The participant agent."""
import random
import time
import json
import re
import os
from concurrent import futures
import math
from typing import Union, List

from loguru import logger

from agentscope.rpc import async_func, RpcClient
from agentscope.message import Msg
from agentscope.agent import AgentBase
from agentscope.environment import BasicEnv
from agentscope.exception import ResponseParsingError
from agentscope.utils.common import _get_timestamp
from agentscope.logging import log_msg

SAVE_DIR = f"./runs/{os.uname().nodename}"

RATIO_MAP = {
    "1/2": 1 / 2,
    "2/3": 2 / 3,
    "3/5": 3 / 5,
    "51/100": 51 / 100,
    "67/100": 67 / 100,
}

PROMPT = json.load(open("configs/prompt.json", "r", encoding="utf-8"))
SYSTEM = PROMPT["SYSTEM"]
USER = PROMPT["USER"]


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


class RandomParticipant(AgentBase):
    """A fake participant who generates number randomly."""

    def __init__(  # type: ignore[no-untyped-def]
        self,
        name: str,
        max_value: int = 100,
        sleep_time: float = 1.0,
        **kwargs,
    ) -> None:
        """Initialize the participant."""
        super().__init__(
            name=name,
        )
        self.max_value = max_value
        self.sleep_time = sleep_time
        self.round = 0

    def _generate_random_response(self) -> float:
        """generate a random int"""
        time.sleep(self.sleep_time)
        return random.randint(0, self.max_value)

    def reply(self, x: dict = None) -> dict:
        """Generate a random value"""
        self.round += 1
        response = self._generate_random_response()
        msg = Msg(name=self.name, role="assistant", content=response)
        log_msg(
            Msg(
                self.name,
                content={
                    "value": float(response),
                    "round": self.round,
                },
                role="assistant",
            ),
        )
        return msg


class LLMParticipant(AgentBase):
    """A participant agent who generates number using LLM."""

    def __init__(  # type: ignore[no-untyped-def]
        self,
        name: str,
        model_config_name: str,
        max_value: int = 100,
        ratio: str = "2/3",
        sys_id: str = "1",
        **kwargs,
    ) -> None:
        """Initialize the participant."""
        super().__init__(
            name=name,
            sys_prompt=SYSTEM[sys_id].format(max_value=max_value, ratio=ratio),
            model_config_name=model_config_name,
            use_memory=True,
        )
        self.max_value = max_value
        self.prompt = Msg(
            name="system",
            role="system",
            content=SYSTEM[sys_id].format(max_value=max_value, ratio=ratio),
        )
        logger.warning(f"{self.model.generate_args}")
        self.round = 0

    def parse_value(self, txt: str) -> float:
        """Parse the number from the response."""
        prompts = format_messages(
            [
                Msg(
                    name="system",
                    role="system",
                    content="You need to extract the number that the speaker wants to answer from the following text.\n"
                    + txt,
                ),
                Msg(
                    name="user",
                    role="user",
                    content="Now please directly give the extracted number in the following format:\nThe answer is [number].\n\nIf you can't extract the number, please reply directly:\nI CAN'T.\n",
                ),
            ],
        )
        parse_result = self.model(prompts).text
        numbers = re.findall(r"(\d+(\.\d+)?)", parse_result)
        if len(numbers) == 0:
            logger.error(
                f"Fail to parse value from [{txt}]",
            )
            return -1
        else:
            return float(numbers[0][0])

    def reply(self, x: dict = None) -> dict:
        """Generate a value by LLM"""
        if self.memory:
            self.memory.add(x)
        self.round += 1
        # prepare prompt
        prompt = format_messages([self.prompt, *self.memory.get_memory()])
        # call llm and generate response
        for attempts in range(3):
            try:
                raw_response = self.model(prompt).text
                response = self.parse_value(raw_response)
                break
            except ResponseParsingError:
                logger.warning("Failed to parse number")
                if attempts == 2:
                    logger.error(f"Max retries reached. Use {-1} instead.")
                    response = str(-1)
        v = float(response)
        if v <= self.max_value and v >= 0:
            log_msg(
                Msg(
                    self.name,
                    content={
                        "value": float(response),
                        "raw": raw_response,
                        "round": self.round,
                    },
                    role="assistant",
                ),
            )
        msg = Msg(self.name, content=response, role="assistant")
        # Record the message in memory
        if self.memory:
            self.memory.add(
                Msg(self.name, content=raw_response, role="assistant"),
            )

        return msg


class LLMParticipantWithBackground(AgentBase):
    """A participant agent with background"""

    def __init__(  # type: ignore[no-untyped-def]
        self,
        name: str,
        model_config_name: str,
        max_value: int = 100,
        ratio: str = "2/3",
        sys_id: str = "1",
        background: str = None,
        **kwargs,
    ) -> None:
        super().__init__(
            name=name,
            model_config_name=model_config_name,
            use_memory=True,
        )
        self.max_value = max_value
        self.prompt = Msg(
            name="system",
            role="system",
            content=SYSTEM[sys_id].format(
                max_value=max_value,
                ratio=ratio,
                background=background,
            ),
        )
        logger.warning(f"{self.model.generate_args}")
        self.round = 0

    def parse_value(self, txt: str) -> float:
        """Parse the number from the response."""
        prompt = format_messages(
            [
                Msg(
                    name="system",
                    role="system",
                    content="You need to extract the number that the speaker wants to answer from the following text.\n"
                    + txt,
                ),
                Msg(
                    name="user",
                    role="user",
                    content="Now please directly give the extracted number in the following format:\nThe answer is [number].\n\nIf you can't extract the number, please reply directly:\nI CAN'T.\n",
                ),
            ],
        )
        parse_result = self.model(prompt).text
        numbers = re.findall(r"(\d+(\.\d+)?)", parse_result)
        if len(numbers) == 0:
            logger.error(
                f"Fail to parse value from [{txt}]",
            )
            return -1
        else:
            return float(numbers[0][0])

    def reply(self, x: dict = None) -> dict:
        """Generate a value by LLM"""
        if self.memory:
            self.memory.add(x)
        self.round += 1
        # prepare prompt
        prompt = format_messages([self.prompt, *self.memory.get_memory()])
        # call llm and generate response
        for attempts in range(3):
            try:
                raw_response = self.model(prompt).text
                response = self.parse_value(raw_response)
                break
            except ResponseParsingError:
                logger.warning("Failed to parse number")
                if attempts == 2:
                    logger.error(f"Max retries reached. Use {-1}.")
                    response = -1
        v = float(response)
        if v <= self.max_value:
            logger.chat(
                Msg(
                    self.name,
                    content={
                        "value": float(response),
                        "raw": raw_response,
                        "round": self.round,
                    },
                    role="assistant",
                ),
            )
        msg = Msg(self.name, content=response, role="assistant")
        # Record the message in memory
        if self.memory:
            self.memory.add(
                Msg(self.name, content=raw_response, role="assistant"),
            )

        return msg


class ParserAgent(AgentBase):
    """Parse the experiment result"""

    def __init__(self, name: str, **kwargs):  # type: ignore[no-untyped-def]
        super().__init__(name=name, use_memory=False)

    def parse_result(self, log_dir: str) -> list:
        """Parse result from log files"""
        logger.info(f"parse result from {log_dir}")
        results = []
        tasks = []

        def parse_file(filepath: str) -> list:
            result = []
            with open(filepath, "r", encoding="utf-8") as file:
                for line in file.readlines():
                    rec = json.loads(line)
                    result.append(rec)
            return result

        with futures.ThreadPoolExecutor() as executor:
            for filename in os.listdir(log_dir):
                if filename.startswith("server"):
                    filepath = os.path.join(log_dir, filename, "logging.chat")
                    tasks.append(executor.submit(parse_file, filepath))
            items = [task.result() for task in tasks]
        for item in items:
            results.extend(item)
        return results

    def reply(self, x: dict = None) -> dict:
        return Msg(
            name=self.name,
            role="assistant",
            content=self.parse_result(SAVE_DIR),
        )


class Group(BasicEnv):
    """A group of participants."""

    def __init__(  # type: ignore[no-untyped-def]
        self,
        name: str,
        agent_type: str = "random",
        ratio: str = "2/3",
        max_value: int = 100,
        sleep_time: float = 1.0,
        usr_id: str = "2",
        participant_configs: list[dict] = None,
        **kwargs,
    ) -> None:
        logger.info(f"init Group {name}")
        super().__init__(name=name)
        if agent_type == "llm":
            self.participants = [
                LLMParticipant(
                    name=config["name"],
                    model_config_name=config["model_config_name"],
                    max_value=max_value,
                    ratio=ratio,
                    sys_id=config["sys_id"],
                    to_dist={
                        "host": config["host"],
                        "port": config["port"],
                        "retry_strategy": {
                            "type": "fixed",
                            "max_retries": 100,
                            "delay": 2,
                        },
                    },
                )
                for config in participant_configs
            ]
        else:
            self.participants = [
                RandomParticipant(
                    name=config["name"],
                    max_value=max_value,
                    sleep_time=sleep_time,
                    to_dist={
                        "host": config["host"],
                        "port": config["port"],
                        "retry_strategy": {
                            "type": "fixed",
                            "max_retries": 20,
                            "delay": 2,
                        },
                    },
                )
                for config in participant_configs
            ]
        self.usr_prompt = USER[usr_id]
        self.sum = 0
        self.cnt = 0
        self.max_value = max_value

    @async_func
    def run(self, round: int, winner: float) -> dict:
        """Play one round of game in this group."""
        if round != 0:
            content = f"The winner number of this round is {winner:.2f}. Let's move on to the next round.\n{self.usr_prompt}"

        else:
            content = self.usr_prompt
        msg = Msg(name="group", role="user", content=content)
        self.sum = 0
        self.cnt = 0
        result = []
        for p in self.participants:
            result.append(p(msg))
        for r in result:
            try:
                v = r["content"]
                if 0 <= v <= self.max_value:
                    self.sum += v
                    self.cnt += 1
            except Exception as e:
                print(e)
        return {"sum": self.sum, "cnt": self.cnt}


def merge_result(results: list[dict]) -> list:
    """Merge the result from different machines"""
    result = []
    for r in results:
        result.extend(r["content"])
    grouped = {}
    for r in result:
        round_value = r["content"]["round"]
        if round_value not in grouped:
            grouped[round_value] = {}
        grouped[round_value].update({r["name"]: r["content"]})
    return list(grouped.values())


def save_result(
    results: list,
    run_time: float,
    save_path: str = "./result",
    ratio: str = "2/3",
) -> None:
    """Save the result into file"""
    os.makedirs(save_path, exist_ok=True)
    import numpy as np
    from matplotlib import pyplot as plt

    for r, result in enumerate(results):
        values = [v["value"] for v in result.values()]
        win = np.mean(values) * RATIO_MAP[ratio]
        stats = {
            "win": win,
            "cnt": len(values),
            "avg": float(np.mean(values)),
            "med": float(np.median(values)),
            "std": float(np.std(values)),
            "max": float(np.max(values)),
            "min": float(np.min(values)),
            "time": run_time,
        }
        values = [int(v) for v in values]
        with open(
            os.path.join(save_path, f"result_{r}.json"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    {"data": result, "stats": stats},
                    indent=2,
                ),
            )
        # draw img
        plt.clf()
        counts = np.bincount(values, minlength=101)
        plt.figure(figsize=(4, 3))
        plt.bar(range(101), counts, color="#2980b9", alpha=0.7)
        plt.axvline(
            x=win,
            color="#f39c12",
            linestyle="dotted",
            linewidth=1,
            label=f"Winner: {win:.2f}",
        )
        plt.xlabel("Number")
        plt.ylabel("Frequency")
        plt.legend()
        plt.tight_layout()
        plt.savefig(
            os.path.join(save_path, f"result_{r}.pdf"),
            bbox_inches="tight",
            pad_inches=0.02,
        )


def check_server_alive(
    hosts: list,
    base_port: int,
    agent_server_per_host: int,
) -> None:
    """Check server alive"""
    max_retry = 10
    for host in hosts:
        for port in range(base_port, base_port + agent_server_per_host):
            client = RpcClient(host, port)
            i = 0
            while not client.is_alive() and i < max_retry:
                logger.warning(
                    f"server [{host}:{port}] is not alive, retry...",
                )
                time.sleep(5)
                i += 1
            if i >= max_retry:
                logger.error("Exceed max retry")
                raise RuntimeError("Exceed max retry")


class GuessTwoThirdGame(BasicEnv):
    """Guess the 2/3 of the average game."""

    def __init__(
        self,
        name: str,
        hosts: list[str],
        base_port: int,
        agent_server_per_host: int,
        model_per_host: int,
        participant_num: int,
        env_server_per_host: int = 10,
        agent_type: str = "random",
        sys_id: str = "1",
        usr_id: str = "1",
        model_name: str = "qwen2_72b",
        sleep_time: float = 1.0,
        max_value: int = 100,
        ratio: str = "2/3",
        round: int = 5,
    ) -> None:
        super().__init__(name)
        self.hosts = hosts
        self.host_num = len(hosts)
        self.base_port = base_port
        self.agent_server_per_host = agent_server_per_host
        self.env_server_per_host = env_server_per_host
        self.model_per_host = model_per_host
        self.participant_num = participant_num
        self.agent_type = agent_type
        self.sys_id = sys_id
        self.usr_id = usr_id
        self.model_name = model_name
        self.max_value = max_value
        self.ratio = ratio
        self.sleep_time = sleep_time
        self.round = 0
        self.max_round = round
        self.winners = []
        self._init_env()

    def _generate_participant_configs(
        self,
    ) -> list:
        total_agent_server_num = self.agent_server_per_host * self.host_num
        participant_per_agent_server = math.ceil(
            self.participant_num / total_agent_server_num,
        )
        configs = []
        logger.info(
            f"init {self.participant_num} {self.agent_type} participant agents...",
        )
        # build init configs of participants
        for i in range(self.participant_num):
            idx = i // participant_per_agent_server
            host_id = idx // self.agent_server_per_host
            port_id = idx % self.agent_server_per_host
            model_id = i % self.model_per_host
            host = self.hosts[host_id]
            port = self.base_port + port_id
            if self.agent_type == "random":
                configs.append(
                    {
                        "name": f"P{i}",
                        "host": host,
                        "port": port,
                    },
                )
            else:
                config_name = (
                    f"{self.model_name}_{self.model_per_host}_{model_id + 1}"
                )
                configs.append(
                    {
                        "name": f"P{i}",
                        "model_config_name": config_name,
                        "host": host,
                        "port": port,
                        "sys_id": self.sys_id,
                    },
                )
        return configs

    def _init_env(
        self,
    ) -> None:
        check_server_alive(
            hosts=self.hosts,
            base_port=self.base_port,
            agent_server_per_host=self.agent_server_per_host,
        )
        ist = time.time()
        configs = self._generate_participant_configs()

        self.envs = []
        env_num = self.env_server_per_host * self.host_num
        participant_per_group = self.participant_num // env_num
        logger.info(f"init {env_num} envs...")
        # init groups
        for i in range(env_num):
            self.envs.append(
                Group(
                    name=f"group_{i}",
                    agent_type=self.agent_type,
                    ratio=self.ratio,
                    participant_configs=configs[
                        i
                        * participant_per_group : (i + 1)
                        * participant_per_group
                    ],
                    max_value=self.max_value,
                    sleep_time=self.sleep_time,
                    usr_id=self.usr_id,
                    to_dist={
                        "host": self.hosts[i // self.env_server_per_host],
                        "port": self.base_port
                        + self.agent_server_per_host
                        + i % self.env_server_per_host,
                        "retry_strategy": {
                            "type": "fixed",
                            "max_retries": 100,
                            "delay": 1,
                        },
                    },
                ),
            )
        iet = time.time()
        logger.info(f"[init takes {iet - ist} s]")

    def step(self) -> None:
        """Run one step of the game."""
        st = time.time()
        tasks = []
        summ = 0
        cnt = 0
        for g in self.envs:
            tasks.append(
                g.run(
                    self.round,
                    self.winners[-1] if len(self.winners) > 0 else 0,
                ),
            )
        for t in tasks:
            summ += t["sum"]
            cnt += t["cnt"]
        self.winners.append(summ / cnt * RATIO_MAP[self.ratio])
        et = time.time()
        log_msg(
            Msg(
                name="Moderator",
                role="assistant",
                content=f"The average value of round {self.round + 1} is {summ / cnt :.2f} [takes {et - st :.3f} s]",
            ),
        )

    def record(self, run_time: float) -> None:
        """Record the game result."""
        results = []
        for host in self.hosts:
            parser = ParserAgent(
                name=f"parser-{host}",
                to_dist={"host": host, "port": self.base_port},
            )
            results.append(parser())
        result = merge_result(results)
        save_path = os.path.join(
            "./result",
            self.name,
            f"{self.model_name}" if self.agent_type == "llm" else "random",
            f"{self.sys_id}-{self.usr_id}-{self.participant_num}-{self.host_num}-{RATIO_MAP[self.ratio]:.3f}",
            _get_timestamp(format_="%Y-%m-%d-%H:%M:%S"),
        )
        save_result(result, run_time, save_path, self.ratio)
        log_msg(
            Msg(
                name="Moderator",
                role="assistant",
                content=f"Save result to {save_path}",
            ),
        )

    def run(self) -> None:
        """Run the game"""
        st = time.time()
        while self.round < self.max_round:
            self.step()
            self.round += 1
        et = time.time()
        try:
            self.record(et - st)
        except Exception as e:
            logger.error(f"Fail to save results: {e}")
