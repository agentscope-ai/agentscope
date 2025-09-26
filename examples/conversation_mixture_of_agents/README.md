# Using Mixture of Agents to Enhance Performance in AgentScope

This example will show how to use [Mixture of Agents]((https://arxiv.org/abs/2406.04692)) in AgentScope.

## Background

There are many existing efforts to enhance the performance of large language models (LLMs) by stacking the number of models during inference time.
Existing works such as [MoA](https://github.com/togethercomputer/MoA), [More Agent is All You Need](https://arxiv.org/abs/2402.05120) have already shown promising results.
You can refer to the [paper of MoA](https://arxiv.org/abs/2406.04692) or their [Readme.md](https://github.com/togethercomputer/MoA/blob/main/README.md) for more details.

## Implementation

Here, we implement the MoA algorithm in AgentScope, and provide an example of using MoA.
For implementation, please refer to [mixture_of_agent.py](https://github.com/modelscope/agentscope/blob/main/src/agentscope/strategy/mixture_of_agent.py).


## Usage
MoA modules can be used anywhere. Here we provide an example of using MoA in the [conversation_moa.py](https://github.com/modelscope/agentscope/blob/main/examples/conversation_mixture_of_agents/conversation_moa.py) file.

To use run it, you first have to fill in the api_keys for different models, or host local models using vllm or ollama.
If you are not familiar with setting up model serving, you can refer to [scripts/README.md](https://github.com/modelscope/agentscope/blob/main/scripts/README.md).

Then you can have a conversation with the MoA module by running the following code:

```python
python conversation_moa.py
```

If you are interested in using the MoA module elsewhere, you can learn how to use it following the code in [conversation_moa.py](https://github.com/modelscope/agentscope/blob/main/examples/conversation_mixture_of_agents/conversation_moa.py).


You can init the module as follows:
```python
    moa_module = MixtureOfAgents(
        main_model="qwen-max",  # the model you use
        reference_models=["gpt-4", "qwen-max", "gemini-pro"],
        show_internal=False,  # set to True to see the internal of MoA modules
        rounds=1,  # can range from 0 to inf
    )
```

`show-internal` means that whether the internal messages of MoA modules will be shown.
`rounds` means the number of layers of MoA. Default is 1. Can be set to 0. As the number goes higher, more tokens will be used.

After you init the module, you can use it to replace the original model.

Instead of first uses `prompt = model.format(msg)` then uses `__call__` by `res = model(prompt).text`, you can use `res = moa_module(msg)` directly.

To be more specific, comparing with how the DialogAgent use the model:
```python
    # prepare prompt
    prompt = self.model.format(
        Msg("system", self.sys_prompt, role="system"),
        self.memory
        and self.memory.get_memory()
        or x,  # type: ignore[arg-type]
    )
    # call llm and generate response
    response = self.model(prompt).text
```

With MoA module, you can use it as follows:
```python
    response = self.moa_module(
        Msg("system", self.sys_prompt, role="system"),
        self.memory
        and self.memory.get_memory()
        or x,  # type: ignore[arg-type]
    )
```

## Prerequisites
To set up model serving with open-source LLMs, follow the guidance in
[scripts/README.md](https://github.com/modelscope/agentscope/blob/main/scripts/README.md).

## Notice
With the MoA module, inference time will increase and more tokens are used. So use it with caution.