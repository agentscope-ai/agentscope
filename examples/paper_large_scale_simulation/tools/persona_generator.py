# -*- coding: utf-8 -*-
"""Generate Persona with LLM"""
import os
import json
import argparse
from typing import Any
from tqdm import tqdm

from loguru import logger
import numpy as np
import agentscope
from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.server import RpcAgentServerLauncher
from agentscope.rpc.retry_strategy import RetryFixedTimes

MODEL_CONFIG_NAME = "my_model"
MODEL_CONFIG = {
    "model_type": "dashscope_chat",
    "config_name": MODEL_CONFIG_NAME,
    "model_name": "qwen-max",
    "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
}

BEGIN_TAG = "[PERSONA BEGIN]"
END_TAG = "[PERSONA END]"

SYS_PROMPT_ZH = """你是一个角色人格描述生成小助手，你需要基于用户提供的 JSON 格式的提示信息，将其扩展为完整的角色人格描述。生成的描述需要遵循如下格式：

```
    [PERSONA BEGIN]
    - 姓名：必填
    - 性别：男/女/不愿透露
    - 年龄：xx 岁/不愿透露
    - 人格描述：一段话简述该角色的人格
    [PERSONA END]
```
"""  # noqa

SYS_PROMPT_EN = """
You are a role personality description assistant, you need to generate a complete role personality description based on the provided JSON. The generated description should follow the following format:

```
    [PERSONA BEGIN]
    - Name: Required
    - Gender: Male/Female/I don't want to disclose
    - Age: xx years old/I don't want to disclose
    - Personality Description: A brief description of the role's personality
    [PERSONA END]
```
"""  # noqa

USER_PROMPT_ZH = "请基于如下 JSON 生成角色的人格描述:\n"
USER_PROMPT_EN = (
    "Please generate a role persona based on the following JSON:\n"
)


class PersonaGenerator(AgentBase):
    """An agent that can generate persona"""

    def __init__(
        self,
        name: str,
        model_config_name: str = None,
        language: str = "en",
    ):
        super().__init__(
            name,
            sys_prompt=None,
            model_config_name=model_config_name,
            use_memory=False,
        )
        self.sys_prompt = Msg(
            name="system",
            role="system",
            content=SYS_PROMPT_EN if language == "en" else SYS_PROMPT_ZH,
        )
        self.user_prompt = (
            USER_PROMPT_EN if language == "en" else USER_PROMPT_ZH
        )

    def _extract_persona(self, content: str) -> str:
        if BEGIN_TAG in content and END_TAG in content:
            return content[
                content.find(BEGIN_TAG)
                + len(BEGIN_TAG) : content.find(END_TAG)
            ]
        else:
            raise ValueError("Invalid persona format")

    def reply(self, x: Msg) -> Msg:  # pylint: disable=W0222
        desc = x.content
        assert isinstance(desc, dict), "Persona description should be a dict"
        prompt = self.model.format(
            self.sys_prompt,
            Msg(
                name="user",
                role="user",
                content=self.user_prompt
                + json.dumps(desc, indent=2, ensure_ascii=False),
            ),
        )
        response = self.model(prompt)
        persona = RetryFixedTimes(max_retries=5, delay=2).retry(
            self._extract_persona,
            response.text,
        )
        logger.debug(persona)
        return Msg(name=self.name, role="assistant", content=persona)


def generate_samples(config_path: str) -> list:
    """Generate samples based on the given config"""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    total_num = config["total_num"]
    samples = [{} for _ in range(total_num)]
    for distribution in config["distributions"]:
        distribution_name = distribution["name"]
        categories = distribution["categories"]

        # Extract category names and percentages
        category_names = [category["category_name"] for category in categories]
        percentages = [category["percentage"] for category in categories]
        attributes = {
            category["category_name"]: category.get(
                "attributes",
                {distribution_name: category["category_name"]},
            )
            for category in categories
        }

        # Convert percentages to actual numbers of samples
        num_samples_per_category = (np.array(percentages) * total_num).astype(
            int,
        )

        # Adjust any rounding errors to ensure total_num samples
        while num_samples_per_category.sum() < total_num:
            diff = total_num - num_samples_per_category.sum()
            for i in range(diff):
                # Add one to the first category that needs more samples
                num_samples_per_category[
                    i % len(num_samples_per_category)
                ] += 1
        while num_samples_per_category.sum() > total_num:
            diff = num_samples_per_category.sum() - total_num
            for i in range(diff):
                # Subtract one from the first category that has more samples
                num_samples_per_category[
                    i % len(num_samples_per_category)
                ] -= 1

        # Create samples for current distribution
        category_samples = []
        for category, count in zip(category_names, num_samples_per_category):
            category_samples.extend([category] * count)

        # Shuffle to avoid ordering biases
        np.random.shuffle(category_samples)

        # Assign the generated samples to the overall sample list
        for i in range(total_num):
            samples[i].update(attributes[category_samples[i]])

    return samples


def main(
    config_path: str,
    save_path: str,
    worker_num: int = 5,
    language: str = "en",
) -> None:
    """The main function to generate persona"""
    agentscope.init(
        project="simulation",
        name="persona_generation",
        model_configs=MODEL_CONFIG,
    )
    launcher = RpcAgentServerLauncher(custom_agent_classes=[PersonaGenerator])
    launcher.launch()
    workers = [
        PersonaGenerator(
            name="Generator",
            model_config_name=MODEL_CONFIG_NAME,
            language=language,
        ).to_dist(host=launcher.host, port=launcher.port)
        for _ in range(worker_num)
    ]
    samples = generate_samples(config_path)
    print(samples)
    results = []
    for i, sample in enumerate(samples):
        results.append(
            workers[i % worker_num](
                Msg(
                    name="system",
                    role="system",
                    content=sample,
                ),
            ),
        )
    with open(save_path, "w", encoding="utf-8") as f:
        for result in tqdm(results):
            f.write(
                json.dumps({"prompt": result.content}, ensure_ascii=False)
                + "\n",
            )
    launcher.shutdown()


def parse_args() -> Any:
    """Parse args"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-path",
        "-c",
        type=str,
        help="path of the config file",
    )
    parser.add_argument(
        "--save-path",
        "-o",
        type=str,
        help="path of the output file",
    )
    parser.add_argument(
        "--worker-num",
        "-w",
        type=int,
        default=5,
        help="number of workers",
    )
    parser.add_argument(
        "--language",
        choices=["en", "zh"],
        default="en",
        help="language of the config file",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args.config_path, args.save_path, args.worker_num, args.language)
