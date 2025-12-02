# -*- coding: utf-8 -*-
"""
.. _system-prompt-optimization:

系统提示优化
============================

AgentScope 实现了一个用于优化智能体系统提示的模块。

.. _system-prompt-generator:

系统提示生成器
^^^^^^^^^^^^^^^^^^^^^^^^

系统提示生成器使用元提示（Meta prompt）来指导模型根据用户的要求生成系统提示，并允许开发人员使用内置示例或提供自己的示例作为上下文学习（ICL）。

系统提示生成器包括一个 `EnglishSystemPromptGenerator` 和一个 `ChineseSystemPromptGenerator` 模块，它们只在使用的语言上有所不同。

我们以 `ChineseSystemPromptGenerator` 为例,说明如何使用系统提示生成器。

初始化
^^^^^^^^^^^^^^^^^^^^^^^^

要初始化生成器,你需要首先在 `agentscope.init` 函数中注册你的模型配置。
"""

from agentscope.prompt import ChineseSystemPromptGenerator
import agentscope

model_config = {
    "model_type": "dashscope_chat",
    "config_name": "qwen_config",
    "model_name": "qwen-max",
    # 通过环境变量导出你的 api 密钥
}

# %%
# 生成器将使用内置的默认元提示来指导大语言模型生成系统提示。
#

agentscope.init(
    model_configs=model_config,
)

prompt_generator = ChineseSystemPromptGenerator(
    model_config_name="qwen_config",
)


# %%
# 我们欢迎用户自由尝试不同的优化方法。我们提供了相应的 `SystemPromptGeneratorBase` 模块，可以通过继承来实现自定义的系统提示生成器。
#
# 生成系统提示
# ^^^^^^^^^^^^^^^^^^^^^^^^^
#
# 调用生成器的 `generate` 函数来生成系统提示，如下所示。
#
# 可以输入一个需求，或者要优化的系统提示。

generated_system_prompt = prompt_generator.generate(
    user_input="为一位小红书营销专家生成系统提示，他负责推广书籍。",
)

print(generated_system_prompt)

# %%
# 上下文学习（ICL）
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# AgentScope 在系统提示生成中支持上下文学习。
#
# 要使用示例，AgentScope 提供了以下参数:
#
# - `example_num`: 附加到元提示的示例数量，默认为 0
# - `example_selection_strategy`: 选择示例的策略，可选 "random" 和 "similarity"。
# - `example_list`: 一个示例列表，其中每个示例必须是一个带有键 "user_prompt" 和 "opt_prompt" 的字典。如果未指定,将使用内置的示例列表。
#
# 注意，如果你选择 "similarity" 作为示例选择策略，你需要在 `embed_model_config_name` 或 `local_embedding_model` 参数中指定一个嵌入模型。
#
# 它们的区别如下:
#
# - `embed_model_config_name`: 你必须先在 `agentscope.init` 中注册嵌入模型，并在此参数中指定模型配置名称。
# - `local_embedding_model`: 或者，你可以使用 `sentence_transformers.SentenceTransformer` 库支持的本地小型嵌入模型。
#
# 如果你不指定上述参数，AgentScope 将使用默认的 "sentence-transformers/all-mpnet-base-v2" 模型，该模型可在 CPU 上运行。

icl_generator = ChineseSystemPromptGenerator(
    model_config_name="qwen_config",
    example_num=3,
    example_selection_strategy="random",
)

icl_generated_system_prompt = icl_generator.generate(
    user_input="为一位小红书营销专家生成系统提示，他负责推广书籍。",
)

print(icl_generated_system_prompt)

# %%
# .. note:: 1. 样例的 Embedding 将被缓存在 `~/.cache/agentscope/` 中，以避免重复计算。
#  2. `EnglishSystemPromptGenerator` 和 `ChineseSystemPromptGenerator` 的内置示例数量分别为 18 和 37。请注意 Embedding API 服务的成本。
#
