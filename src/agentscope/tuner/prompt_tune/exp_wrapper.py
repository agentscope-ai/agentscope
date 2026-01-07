import asyncio
from types import SimpleNamespace
from dspy.primitives.module import Module

import random
from typing import Literal

from datasets import load_dataset

import dspy
from dspy.datasets import DataLoader

from agentscope.model._model_base import ChatModelBase
from agentscope.tuner._workflow import WorkflowType


class MockModule(Module):
    def __init__(self):
        super().__init__()
        # MIPROv2 检查 isinstance(param, Predict)，所以我们需要这个 Mock 类模仿它的行为
        # 或者我们直接让 MockPredictor 继承自 dspy.predict.Predict 但不实现逻辑
        from dspy.predict.predict import Predict
        class OptimizableMock(Predict):
            def __init__(self, signature):
                super().__init__(signature)
                self.signature = dspy.make_signature(signature)
                self.instructions = self.signature.instructions
                self.demos = []
            def forward(self, **kwargs):
                return dspy.Prediction(label='A' if random.random()<0.5 else 'B')
        
        self.predictor = OptimizableMock("text -> label")

    def forward(self, **inputs):
        return self.predictor(**inputs)


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
    
    
    
    lm=dspy.LM('dashscope/qwen-plus')
    dspy.configure(lm=lm)
    print("loaded lm")
    
    # Define the DSPy module for classification. It will use the hint at training time, if available.
    # signature = dspy.Signature("text, hint -> label").with_updated_fields('label', type_=Literal[tuple(CLASSES)])
    classify = MockModule()

    # Optimize via BootstrapFinetune.
    import pdb;pdb.set_trace()
    
    kwargs = dict(num_threads=8, teacher_settings=dict(lm=lm), prompt_model=lm)
    optimizer = dspy.MIPROv2(metric=(lambda x, y, trace=None: y.label == 'A'), auto="medium", **kwargs)
    optimized = optimizer.compile(classify, trainset=trainset)

    optimized(text="What does a pending cash withdrawal mean?")
    import pdb;pdb.set_trace()
    

if __name__=="__main__":
    main()