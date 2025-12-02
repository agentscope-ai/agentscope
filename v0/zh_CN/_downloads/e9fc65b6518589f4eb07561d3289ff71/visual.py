# -*- coding: utf-8 -*-
"""
.. _visual-interface:

可视化
=========================

AgentScope 支持基于 AgentScope Studio 和 Gradio 的网页可视化，同时支持用户对接自定义或第三方的可视化平台。


AgentScope Studio
~~~~~~~~~~~~~~~~~~~~~~

AgentScope Studio 是基于 React(vite) 和 NodeJS 开发的 WebUI 工具，用于应用的可视化，和监控应用运行，监控 API 调用以及 Token 使用情况。

.. image:: https://img.alicdn.com/imgextra/i1/O1CN01wrmPHM1GFBWSvufAt_!!6000000000592-0-tps-3104-1782.jpg
   :class: bordered-image

.. note:: 目前 AgentScope Studio 处于快速开发中，包括 UI，功能和性能等方面都在不断迭代中，欢迎任何反馈、贡献或建议!

安装
------------------

首先需要安装 npm

.. code-block:: bash

    # MacOS
    brew install node

    # Ubuntu
    sudo apt update
    sudo apt install nodejs npm

    # Windows 请访问 https://nodejs.org/ 进行安装


通过以下命令进行安装

.. code-block:: bash

    npm install -g @agentscope/studio

启动
---------------------

在命令台通过如下命令启动

.. code-block:: bash

    as_studio

使用
---------------------

启动 AgentScope 的 Python 程序时，通过 `agentscope.init` 函数中的 `studio_url` 字段连接 AgentScope Studio。

.. code-block:: python

    import agentscope

    agentscope.init(
        # ...
        studio_url="https://localhost:3000"  # 替换成你本地的 AgentScope Studio 地址
    )

    # ...

.. note:: 一旦连接，Python 程序中所有智能体对象调用 `speak` 函数打印出的输出都将转发
 到 AgentScope Studio，同时程序中的 `UserAgent` 的输入操作也将从终端转移到 AgentScope
 Studio 的 Dashboard 面板中。

.. image:: https://img.alicdn.com/imgextra/i1/O1CN01bFa0I61VHfuckdckm_!!6000000002628-0-tps-3104-1782.jpg
   :class: bordered-image
   :caption: Dashboard 页面

Studio 中的 Dashboard 页面将按照 `agentscope.init` 函数中传入的 `project` 参数对程序
进行分组，点击后即可查看该项目分组内所有历史运行情况。

需要用户进行输入时，对话界面的输入按键会进行提示，不需要输入时，输入按钮会处于禁用状态。

.. image:: https://img.alicdn.com/imgextra/i4/O1CN01eCEYvA1ueuOkien7T_!!6000000006063-1-tps-960-600.gif
   :class: bordered-image

Gradio
~~~~~~~~~~~~~~~~~~~~~~

首先，请确保已安装完整版本的 AgentScope, 其中包含 Gradio 包。

.. code-block:: bash

    # From pypi
    pip install agentscope[full]

    # From source code
    cd agentscope
    pip install .[full]


之后，请确保您的应用程序被封装在一个 `main` 函数中。

.. code-block:: python

    from agentscope.agents import DialogAgent, UserAgent
    import agentscope


    def main():
        # Your code here
        agentscope.init(model_configs={
            "config_name": "my-qwen-max",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max"
        })

        agent = DialogAgent(
            name="Alice,
            model_config_name="my-qwen-max",
            sys_prompt="You're a helpful assistant named Alice."
        )
        user = UserAgent(agent)

        msg = None
        while True:
            msg = agent(msg)
            msg = user(msg)
            if msg.content == "exit":
                break


然后在终端执行以下命令启动 Gradio UI:

.. code-block :: bash

    as_gradio {path_to_your_python_code}

最后，您可以访问 Gradio UI，如下所示:

.. image:: https://img.alicdn.com/imgextra/i1/O1CN0181KSfH1oNbfzjUAVT_!!6000000005213-0-tps-3022-1530.jpg
   :class: bordered-image

自定义可视化
~~~~~~~~~~~~~~~~~~~~~~

自定义可视化主要分为两个组成部分

1. 消息显示：将智能体中 `speak` 函数打印的输出转发到需要显示的地方
2. 用户输入：将 `UserAgent` 中的**输入操作**转移到目标平台，这样用户可以从目标平台进行输入

上述两个操作分别是通过 AgentScope 中的钩子函数 `pre_speak_hook`，以及 `UserAgent` 中
的 `override_input_method` 完成（AgentScope 中的 Studio 和 Gradio 也是
通过同样的方法实现）。

消息显示
^^^^^^^^^^^^^^^^^^^^^

首先构建钩子函数
"""
from pydantic import BaseModel

from agentscope.agents import AgentBase
from agentscope.message import Msg, TextBlock, ImageBlock
from typing import Union, Any, Optional


def pre_speak_hook(
    self: AgentBase,
    msg: Msg,
    stream: bool,
    last: bool,
) -> Union[Msg, None]:
    """"""
    # 将给输入消息转发到需要显示的地方，例如通过 requests.post 推送消息
    # ...
    return None


# %%
# 然后注册该钩子函数，注意这里你可以控制钩子函数的作用范围，可以是基类级别的，也可以是对象级别的转发

# 类级别的注册，所有 AgentBase 及 子类的对象都会注册该钩子函数
AgentBase.register_class_hook(
    "pre_speak",
    "customized_pre_speak_hook",
    pre_speak_hook,
)

# %%
# 当然也可以针对某一个智能体对象进行注册
#
# .. code-block:: python
#
#     agent = DialogAgent(
#         # ...
#     )
#     agent.register_hook(
#         "pre_speak",
#         "customized_pre_speak_hook",
#         pre_speak_hook
#     )
#
# .. tip:: 更多关于钩子函数的内容，请阅读:ref:`hook`章节
#
# 用户输入
# ^^^^^^^^^^^^^^^^^^^^^
# 转移用户输入，需要实现一个 `UserInputBase` 的子类，并实现其中的 `__call__` 函数，该函数
# 会在调用 `UserAgent` 的 `reply` 函数时被触发，用于通知目标可视化平台现在需要哪个用户进行
# 输入，并且在拿到输入后返回一个 `UserInputData` 对象
#
# .. tip:: 可以参考 `agentscope.agents.TerminalUserInput` 和
#  `agentscope.agents.StudioUserInput` 两个内置模块的代码实现

from agentscope.agents import UserInputBase, UserInputData


class CustomizedUserInput(UserInputBase):
    def __call__(
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_schema: Optional[BaseModel] = None,
        **kwargs: dict,
    ) -> UserInputData:
        """通知目标平台需要进行用户输入，并且获取用户输入"""
        # ...
        return UserInputData(
            blocks_input=[
                # 替换为实际输入，可以是文本或是多模态的输入
                TextBlock(type="text", text="你好！"),
                ImageBlock(type="image", url="http://xxx.png"),
            ],
            structured_input=None,
        )


# %%
# 然后在 `UserAgent` 中注册该输入方法，注意，该注册会覆盖掉原有默认的
# `TerminalUserInput` 类的对象
#
# .. code-block:: python
#
#     UserAgent.override_class_input_method(
#         input_method=CustomizedUserInput()
#     )
#
# .. tip:: `UserAgent` 同样支持对象和类级别的注册，分别对应 `override_input_method`
#  和 `override_class_input_method` 方法，它们会在对象（self）和类（cls）两个不同级别
#  进行注册。
