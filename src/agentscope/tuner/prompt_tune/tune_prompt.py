
from agentscope.message import Msg
from agentscope.formatter import OpenAIChatFormatter
from agentscope.agent import ReActAgent
from agentscope.tuner import (
    tune,
    Dataset,
    WorkflowOutput,
    JudgeOutput,
    TunerChatModel,
    Algorithm,
)
from typing import Dict
import asyncio
import os
from typing import Any, Callable

from datasets import load_dataset
import dspy
from agentscope import logger
from agentscope.agent._react_agent import ReActAgent
from agentscope.model._dashscope_model import DashScopeChatModel
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._config import check_judge_function
from agentscope.tuner._model import TunerChatModel
from agentscope.tuner._workflow import WorkflowType
from agentscope.tuner._judge import JudgeType
from agentscope.tuner._dataset import Dataset
from agentscope.tuner._algorithm import Algorithm
from agentscope.tuner.prompt_tune.wrapper import WorkflowWrapperModule


def wrap_judge_fn(judge_fn: JudgeType):
    async def inner(task: dict, response: Any, auxiliary_models: dict[str, ChatModelBase]):
        output = await judge_fn(task, response, auxiliary_models)
        return output.reward

    def _sync_wrapper(task: dict, response: Any, auxiliary_models: dict[str, ChatModelBase]):
        return asyncio.run(inner(task, response, auxiliary_models))

    return _sync_wrapper


def tune_prompt(
    *,
    workflow_func: Callable[[ReActAgent], WorkflowType],
    init_agent: ReActAgent,
    judge_func: JudgeType,
    train_dataset: Dataset,
    eval_dataset: Dataset | None = None,
    model: ChatModelBase,
    auxiliary_models: dict[str, ChatModelBase] | None = None,
)->ReActAgent:
    auxiliary_models = auxiliary_models or {}
    if isinstance(model, TunerChatModel):
        logger.warning(
            "Model is a TunerChatModel, which will not be optimized during prompt tuning.")
    check_judge_function(judge_func)

    trainset = load_dataset(
        path=train_dataset.path,
        name=train_dataset.name,
        split=train_dataset.split,
        streaming=True,
    )
    
    dspy_trainset = [
        dspy.Example(inp=x).with_inputs('inp')
        for x in trainset
    ][:16] # FIXME

    module = WorkflowWrapperModule(workflow_func, init_agent)
    # model and auxiliary_models are not necessary for prompt tuning.
    # what about providing a new interface?
    module._set_chatmodel(model, auxiliary_models)

    # teacher lm
    if 'DASHSCOPE_API_KEY' in os.environ:
        lm = dspy.LM('dashscope/qwen-plus')
    else:
        lm = dspy.LM('openai/gpt-4o')

    optimizer = dspy.MIPROv2(
        metric=(lambda data, output, trace=None: wrap_judge_fn(
            judge_func)(data.inp, output, auxiliary_models)),
        auto='light',
        teacher_settings={
            'lm': lm,
        },
        prompt_model=lm, # TODO: what is this
        task_model=lm, # TODO: what is this
    )

    # optimize
    result = optimizer.compile(module, trainset=dspy_trainset)

    # evaluate if eval_dataset is provided
    if eval_dataset is not None:
        evalset = load_dataset(
            path=eval_dataset.path,
            name=eval_dataset.name,
            split=eval_dataset.split,
            streaming=True,
        )

        dspy_evalset = [
            dspy.Example(inp=x).with_inputs('inp')
            for x in evalset
        ][:16] # FIXME

        evaluate = dspy.Evaluate(
            devset=dspy_evalset,
            metric=lambda data, output, trace=None: wrap_judge_fn(
                judge_func)(data.inp, output, auxiliary_models),
            display_progress=True,
            display_table=5,
            num_threads=16,
        )

        eval_res = evaluate(result)
        score = eval_res.score
        print(f"Evaluation score: {score}")

    return result._agent