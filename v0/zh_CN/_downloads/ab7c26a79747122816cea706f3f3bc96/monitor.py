# -*- coding: utf-8 -*-
"""
.. _configuring_and_monitoring:

配置和监控
==================================

AgentScope 的主入口是 `agentscope.init`，在这里您可以配置应用程序。
"""

import agentscope


agentscope.init(
    model_configs=[  # 模型配置
        {
            "config_name": "my-qwen-max",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
        },
    ],
    project="项目 Alpha",  # 项目名称
    name="测试-1",  # 运行时名称
    disable_saving=False,  # 是否禁用文件保存，推荐开启
    save_dir="./runs",  # 保存目录
    save_log=True,  # 是否保存日志
    save_code=False,  # 是否保存此次运行的代码
    save_api_invoke=False,  # 保存 API 调用
    cache_dir="~/.cache",  # 缓存目录，用于缓存 Embedding 和其它
    use_monitor=True,  # 是否监控 token 使用情况
    logger_level="INFO",  # 日志级别
)

# %%
# 导出配置
# --------------------------------
# `state_dict` 方法可用于导出正在运行的应用程序的配置。
#

import json

print(json.dumps(agentscope.state_dict(), indent=2))

# %%
# 运行监控
# --------------------------
# AgentScope 提供了 AgentScope Studio，这是一个 Web 可视化界面，用于监控和管理正在运行的应用程序和历史记录。
# 有关更多详细信息，请参阅 :ref:`visual` 部分。
#

# %%
# .. _token_usage:
#
# 监控 Token 使用情况
# ------------------------
# `print_llm_usage` 将打印并返回当前运行应用程序的 token 使用情况。
#

from agentscope.models import DashScopeChatWrapper

qwen_max = DashScopeChatWrapper(
    config_name="-",
    model_name="qwen-max",
)
qwen_plus = DashScopeChatWrapper(
    config_name="-",
    model_name="qwen-plus",
)

# 调用 qwen-max 和 qwen-plus 来模拟 token 使用情况
_ = qwen_max([{"role": "user", "content": "Hi!"}])
_ = qwen_plus([{"role": "user", "content": "Who are you?"}])

usage = agentscope.print_llm_usage()

print(json.dumps(usage, indent=2))
