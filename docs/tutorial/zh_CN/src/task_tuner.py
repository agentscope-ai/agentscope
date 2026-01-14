# -*- coding: utf-8 -*-
"""
.. _tuner:

Tuner
=================

AgentScope 提供了 ``tuner`` 模块，用于通过强化学习（RL）训练智能体应用。
本教程将带你系统了解如何利用 ``tuner`` 提升智能体在特定任务上的表现，包括：

- 介绍 ``tuner`` 的核心组件
- 演示调优流程所需的关键代码实现
- 展示调优流程的配置与运行方法

主要组件
~~~~~~~~~~~~~~~~~~~
``tuner`` 模块为智能体训练工作流引入了三大核心组件：

- **任务数据集**：用于训练和评估智能体的任务集合。
- **工作流函数**：封装被调优智能体应用的决策逻辑。
- **评判函数**：评估智能体在特定任务上的表现，并为调优过程提供奖励信号。

此外，``tuner`` 还提供了若干用于自定义调优流程的配置类，包括：

- **TunerModelConfig**：用于指定被调优模型的相关配置。
- **AlgorithmConfig**：用于指定强化学习算法（如 GRPO、PPO 等）及其参数。

实现流程
~~~~~~~~~~~~~~~~~~~
本节以一个简单的数学智能体为例，演示如何用 ``tuner`` 进行训练。

任务数据集
--------------------
任务数据集包含用于训练和评估的任务集合。

``tuner`` 的任务数据集采用 Huggingface `datasets <https://huggingface.co/docs/datasets/quickstart>`_ 格式，并通过 ``datasets.load_dataset`` 加载。例如：

.. code-block:: text

    my_dataset/
        ├── train.jsonl  # 训练样本
        └── test.jsonl   # 测试样本

假设 `train.jsonl` 内容如下：

.. code-block:: json

    {"question": "2 + 2 等于多少？", "answer": "4"}
    {"question": "4 + 4 等于多少？", "answer": "8"}

在开始调优前，你可以用如下方法来确定你的数据集能够被正确加载：

.. code-block:: python

    from agentscope.tuner import DatasetConfig

    dataset = DatasetConfig(path="my_dataset", split="train")
    dataset.preview(n=2)
    # 输出前两个样本以验证数据集加载正确
    # [
    #   {
    #     "question": "2 + 2 等于多少？",
    #     "answer": "4"
    #   },
    #   {
    #     "question": "4 + 4 等于多少？",
    #     "answer": "8"
    #   }
    # ]

工作流函数
--------------------
工作流函数定义了智能体与环境的交互方式和决策过程。所有工作流函数需遵循 ``agentscope.tuner.WorkflowType`` 的输入/输出签名。

以下是一个用 ReAct 智能体回答数学问题的简单工作流函数示例：
"""

from typing import Dict, Optional
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.tuner import WorkflowOutput


async def example_workflow_function(
    task: Dict,
    model: ChatModelBase,
    auxiliary_models: Optional[Dict[str, ChatModelBase]] = None,
) -> WorkflowOutput:
    """一个用于调优的工作流函数示例。

    Args:
        task (`Dict`): 任务信息。
        model (`ChatModelBase`): 智能体使用的对话模型。
        auxiliary_models (`Optional[Dict[str, ChatModelBase]]`):
            用于辅助的额外对话模型，一般用于多智能体场景下模拟其他非训练智能体的行为。

    Returns:
        `WorkflowOutput`: 工作流生成的输出。
    """
    agent = ReActAgent(
        name="react_agent",
        sys_prompt="你是一个善于解决数学问题的智能体。",
        model=model,
        formatter=OpenAIChatFormatter(),
    )

    response = await agent.reply(
        msg=Msg(
            "user",
            task["question"],
            role="user",
        ),  # 从任务中提取问题
    )

    return WorkflowOutput(  # 返回响应结果
        response=response,
    )


# %%
# 你可以直接用任务字典和日常调试使用的 ``DashScopeChatModel`` / ``OpenAIChatModel`` 运行此工作流函数，从而在正式训练前测试其流程的正确性。例如：

import asyncio
import os
from agentscope.model import DashScopeChatModel

task = {"question": "123 加 456 等于多少？", "answer": "579"}
model = DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
)
workflow_output = asyncio.run(example_workflow_function(task, model))
assert isinstance(
    workflow_output.response,
    Msg,
), "在此示例中，响应应为 Msg 实例。"
print("\n工作流响应:", workflow_output.response.get_text_content())

# %%
#
# 评判函数
# --------------------
# 评判函数用于评估智能体在特定任务上的表现，并为调优过程提供奖励信号。
# 所有评判函数需遵循 ``agentscope.tuner.JudgeType`` 的输入/输出签名。
# 下面是一个简单的评判函数示例，通过比较智能体响应与标准答案给出奖励：

from typing import Any
from agentscope.tuner import JudgeOutput


async def example_judge_function(
    task: Dict,
    response: Any,
    auxiliary_models: Optional[Dict[str, ChatModelBase]] = None,
) -> JudgeOutput:
    """仅用于演示的简单评判函数。

    Args:
        task (`Dict`): 任务信息。
        response (`Any`): WorkflowOutput 的响应字段。
        auxiliary_models (`Optional[Dict[str, ChatModelBase]]`):
            用于 LLM-as-a-Judge 的辅助模型。
    Returns:
        `JudgeOutput`: 评判函数分配的奖励。
    """
    ground_truth = task["answer"]
    reward = 1.0 if ground_truth in response.get_text_content() else 0.0
    return JudgeOutput(reward=reward)


# 本地测试函数的正确性：
judge_output = asyncio.run(
    example_judge_function(
        task,
        workflow_output.response,
    ),
)
print(f"评判奖励: {judge_output.reward}")

# %%
# 评判函数同样可以按照上述案例中展示的方式在正式训练前进行本地测试，以确保其逻辑正确。
#
# .. tip::
#    你可以在评判函数中利用已有的 `MetricBase <https://github.com/agentscope-ai/agentscope/blob/main/src/agentscope/evaluate/_metric_base.py>`_ 实现，计算更复杂的指标，并将其组合为复合奖励。
#
# 配置并运行
# ~~~~~~~~~~~~~~~
# 最后，你可以用 ``tuner`` 模块配置并运行调优流程。
# 在开始调优前，请确保环境已安装 `Trinity-RFT <https://github.com/modelscope/Trinity-RFT>`_，这是 ``tuner`` 的依赖。
#
# 下面是调优流程的配置与启动示例：
#
# .. note::
#    此示例仅供演示。完整可运行示例请参考 `Tune ReActAgent <https://github.com/agentscope-ai/agentscope/tree/main/examples/tuner/react_agent>`_
#
# .. code-block:: python
#
#        from agentscope.tuner import tune, AlgorithmConfig, DatasetConfig, TunerModelConfig
#        # 你的工作流 / 评判函数 ...
#
#        if __name__ == "__main__":
#            dataset = DatasetConfig(path="my_dataset", split="train")
#            model = TunerModelConfig(model_path="Qwen/Qwen3-0.6B", max_model_len=16384)
#            algorithm = AlgorithmConfig(
#                algorithm_type="multi_step_grpo",
#                group_size=8,
#                batch_size=32,
#                learning_rate=1e-6,
#            )
#            tune(
#                workflow_func=example_workflow_function,
#                judge_func=example_judge_function,
#                model=model,
#                train_dataset=dataset,
#                algorithm=algorithm,
#            )
#
# 这里用 ``DatasetConfig`` 配置训练数据集，用 ``TunerModelConfig`` 配置可训练模型相关参数，用 ``AlgorithmConfig`` 指定强化学习算法及其超参数。
#
# .. tip::
#    ``tune`` 函数基于 `Trinity-RFT <https://github.com/modelscope/Trinity-RFT>`_ 实现，内部会将输入参数转换为 YAML 配置。
#    高级用户可忽略 ``model``、``train_dataset``、``algorithm`` 参数，直接通过 ``config_path`` 指定 YAML 配置文件。
#    推荐使用配置文件方式以便更细粒度地控制训练过程，充分利用 Trinity-RFT 的高级功能。
#    你可参考 Trinity-RFT 的 `配置指南 <https://modelscope.github.io/Trinity-RFT/en/main/tutorial/trinity_configs.html>`_ 了解更多配置选项。
#
# 你可以将上述代码保存为 ``main.py``，并用如下命令运行：
#
# .. code-block:: bash
#
#        ray start --head
#        python main.py
#
# 检查点和日志会自动保存到当前工作目录下的 ``checkpoints/AgentScope`` 目录，每次运行会以时间戳为后缀保存到子目录。
# tensorboard 日志可在检查点目录下的 ``monitor/tensorboard`` 中找到。
#
# .. code-block:: text
#
#        your_workspace/
#            └── checkpoints/
#                └──AgentScope/
#                    └── Experiment-20260104185355/  # 每次运行以时间戳保存
#                        ├── monitor/
#                        │   └── tensorboard/  # tensorboard 日志
#                        └── global_step_x/    # 第 x 步保存的模型检查点
#
# .. tip::
#    更多调优样例请参考 AgentScope-Samples 库中的 `tuner 目录 <https://github.com/agentscope-ai/agentscope-samples/tree/main/tuner>`_
