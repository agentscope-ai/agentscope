
import asyncio
import os
import random
from types import SimpleNamespace
from typing import Callable
from datasets import load_dataset
from dspy import Module
import dspy
from dspy.datasets import DataLoader
from agentscope.formatter._openai_formatter import OpenAIChatFormatter
from agentscope.message._message_base import Msg
from agentscope.model._dashscope_model import DashScopeChatModel
from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._workflow import WorkflowOutput, WorkflowType
from agentscope.agent import ReActAgent

from dspy.predict.predict import Predict


class OptimizableAgent(Predict):
    def __init__(self, agent: ReActAgent):
        super().__init__("input -> output")
        self.signature = dspy.make_signature("input -> output")
        self.instructions = self.signature.instructions
        self.demos = []

        self._agent = agent
        # sync
        self.instructions=self._agent._sys_prompt
        self.signature.instructions=self.instructions
        
    def forward(self, **kwargs):
        raise NotImplementedError("OptimizableAgent is a wrapper, not callable")
    
    def _sync_instruction_i2a(self):
        self.instructions=self.signature.instructions
        self._agent._sys_prompt=self.instructions



class WorkflowWrapperModule(Module):
    def __init__(self, workflow: Callable[[ReActAgent],WorkflowType]):
        super().__init__()
        self._workflow = workflow
        self._agent = ReActAgent(
            name="react_agent",
            sys_prompt="Always print A or B randomly.",
            model=DashScopeChatModel("qwen-plus",os.environ['DASHSCOPE_API_KEY'], max_tokens=512),
            enable_meta_tool=True,
            formatter=OpenAIChatFormatter(),
        )
        
        self.predictor = OptimizableAgent(self._agent)
        

    def _set_chatmodel(self, model: ChatModelBase, auxiliary_models: dict[str, ChatModelBase]):
        self._model = model
        self._auxiliary_models = auxiliary_models


    def forward(self, **inputs):
        """
        Args:
            inputs (Dict): The inputs from dspy, including data only.
        """
        
        self.predictor._sync_instruction_i2a()
        
        print("instruction:",self._agent.sys_prompt)
        async def _workflow():
            return await self._workflow(self._agent)(inputs, self._model, self._auxiliary_models)

        self._latest_result = asyncio.run(_workflow())

        return self._latest_result.response

    def get_reward(self):
        raise NotImplementedError("use separate judge function")
        return self._latest_result.reward

    def get_metrics(self):
        raise NotImplementedError("use separate metrics")
        return self._latest_result.metrics
    


######################################

def create_workflow(agent:ReActAgent):
    
    async def run_react_agent(
        task: dict,
        model: ChatModelBase,
        auxiliary_models: dict[str, ChatModelBase],
    ) -> WorkflowOutput:
        """A simple workflow function using the ReAct agent to solve tasks.

        Args:
            task (Dict): The task to be solved.
            model (TunerChatModel): The language model to use.
            auxiliary_models (Dict[str, TunerChatModel]):
                A dictionary of additional chat models available for
                LLM-as-a-Judge. Not used in this workflow.

        Returns:
            float: The reward obtained by solving the task.
        """
        assert (
            len(auxiliary_models) == 0
        ), "No auxiliary models are used in this workflow."
        
        # set model
        agent.model=model
        
        response = await agent.reply(
            msg=Msg("user", task["text"], role="user"),
        )
        
        resp=SimpleNamespace()
        resp.label=response.get_text_content()
        
        return WorkflowOutput(
            response=resp,
        )
    
    return run_react_agent


def main():
    CLASSES = load_dataset("PolyAI/banking77", split="train", trust_remote_code=True).features["label"].names
    kwargs = {"fields": ("text", "label"), "input_keys": ("text",), "split": "train", "trust_remote_code": True}

    # Load the first 2000 examples from the dataset, and assign a hint to each *training* example.
    trainset = [
        dspy.Example(x, hint=CLASSES[x.label], label=CLASSES[x.label]).with_inputs("text")
        for x in DataLoader().from_huggingface(dataset_name="PolyAI/banking77", **kwargs)[:16]
    ]
    random.Random(0).shuffle(trainset)
    
    print("loaded dataset")
    
    classify = WorkflowWrapperModule(create_workflow)
    
    # set model
    lm=dspy.LM('dashscope/qwen-plus')
    dspy.configure(lm=lm)
    print("loaded lm")
    classify._set_chatmodel(DashScopeChatModel("qwen-plus",api_key=os.environ['DASHSCOPE_API_KEY'],max_tokens=512),{})

    # Optimize via BootstrapFinetune.
    import pdb;pdb.set_trace()
    
    kwargs = dict(num_threads=8, teacher_settings=dict(lm=lm), prompt_model=lm)
    optimizer = dspy.MIPROv2(metric=(lambda x, y, trace=None: y.label == "A"), auto='light', **kwargs)
    optimized = optimizer.compile(classify, trainset=trainset)

    optimized(text="What does a pending cash withdrawal mean?")
    import pdb;pdb.set_trace()
    

if __name__=="__main__":
    main()