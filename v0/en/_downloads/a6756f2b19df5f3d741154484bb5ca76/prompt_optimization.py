# -*- coding: utf-8 -*-
"""
.. _system-prompt-optimization:

System Prompt Optimization
============================

AgentScope implements a module for optimizing Agent System Prompts.

.. _system-prompt-generator:

System Prompt Generator
^^^^^^^^^^^^^^^^^^^^^^^^

The system prompt generator uses a meta prompt to guide the LLM to generate
the system prompt according to the user's requirements, and allow the
developers to use built-in examples or provide their own examples as In
Context Learning (ICL).

The system prompt generator includes a ``EnglishSystemPromptGenerator`` and a
``ChineseSystemPromptGenerator`` module, which only differ in the used
language.

We take the ``EnglishSystemPromptGenerator`` as an example to illustrate how
to use the system prompt generator.

Initialization
^^^^^^^^^^^^^^^^^^^^^^^^

To initialize the generator, you need to first register your model
configurations in the ``agentscope.init`` function.
"""

from agentscope.prompt import EnglishSystemPromptGenerator
import agentscope

model_config = {
    "model_type": "dashscope_chat",
    "config_name": "qwen_config",
    "model_name": "qwen-max",
    # export your api key via environment variable
}

# %%
# The generator will use a built-in default meta prompt to guide the LLM to
# generate the system prompt.


agentscope.init(
    model_configs=model_config,
)

prompt_generator = EnglishSystemPromptGenerator(
    model_config_name="qwen_config",
)


# %%
# Users are welcome to freely try different optimization methods. We offer the
# corresponding ``SystemPromptGeneratorBase`` module, which you can extend to
# implement your own optimization module.
#
# Generation
# ^^^^^^^^^^^^^^^^^^^^^^^^^
#
# Call the ``generate`` function of the generator to generate the system prompt
# as follows.
#
# You can input a requirement, or your system prompt to be optimized.

generated_system_prompt = prompt_generator.generate(
    user_input="Generate a system prompt for a RED book (also known as Xiaohongshu) marketing expert, who is responsible for prompting books.",
)

print(generated_system_prompt)

# %%
# Generation with In Context Learning
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# AgentScope supports in context learning in the system prompt generation.
#
# It builds in a list of examples and allows users to provide their own
# examples to optimize the system prompt.
#
# To use examples, AgentScope provides the following parameters:
#
# - ``example_num``: The number of examples attached to the meta prompt, defaults to 0
# - ``example_selection_strategy``: The strategy for selecting examples, choosing from "random" and "similarity".
# - ``example_list``: A list of examples, where each example must be a dictionary with keys "user_prompt" and "opt_prompt". If not specified, the built-in example list will be used.
#
# Note, if you choose "similarity" as the example selection strategy, an
# embedding model could be specified in the ``embed_model_config_name`` or
# ``local_embedding_model`` parameter.
#
# Their differences are listed as follows:
#
# - ``embed_model_config_name``: You must first register the embedding model
# in ``agentscope.init`` and specify the model configuration name in this
# parameter.
# - ``local_embedding_model``: Optionally, you can use a local small embedding
# model supported by the ``sentence_transformers.SentenceTransformer`` library.
#
# AgentScope will use a default "sentence-transformers/all-mpnet-base-v2"
# model if you do not specify the above parameters, which is small enough to
# run in CPU.

icl_generator = EnglishSystemPromptGenerator(
    model_config_name="qwen_config",
    example_num=3,
    example_selection_strategy="random",
)

icl_generated_system_prompt = icl_generator.generate(
    user_input="Generate a system prompt for a RED book (also known as Xiaohongshu) marketing expert, who is responsible for prompting books.",
)

print(icl_generated_system_prompt)

# %%
# .. note:: 1. The example embeddings will be cached in ``~/.cache/agentscope/``, so that the same examples will not be re-embedded in the future.
#  2. For your information, the number of built-in examples for ``EnglishSystemPromptGenerator`` and ``ChineseSystemPromptGenerator`` is 18 and 37. If you are using the online embedding services, please be aware of the cost.
