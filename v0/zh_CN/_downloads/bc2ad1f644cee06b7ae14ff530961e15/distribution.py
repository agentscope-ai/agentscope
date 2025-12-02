# -*- coding: utf-8 -*-
# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: sphinx
#       format_version: '1.1'
#       jupytext_version: 1.16.4
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

"""
.. _distribution:

分布式
============

本节介绍 AgentScope 分布式的使用方法。AgentScope 原生提供了基于 gRPC 的分布式模式，
在这种模式下，一个应用程序中的多个智能体可以部署到不同的进程或者甚至不同的机器上，从而充分利用计算资源，提高效率。

基本使用
~~~~~~~~~~~

与传统模式相比，AgentScope 的分布式模式不需要修改主进程代码。只需在初始化智能体时调用 `to_dist` 函数。

本节将展示如何使用 AgentScope 的分布式模式进行网页搜索。
为了展示 AgentScope 分布式模式带来的加速效果，示例中将自定义一个 `WebAgent` 类，在该类型将等待5秒来模拟抓取网页和从中寻找答案的过程。

执行搜索的过程是 `run` 函数。传统模式和分布式模式之间唯一的区别在于初始化阶段，即 `init_without_dist` 和 `init_with_dist`。
在分布式模式下，您需要调用 `to_dist` 函数，将原始智能体转换为对应的分布式版本。

.. code-block:: python

    # 请勿在jupyter notebook中运行此代码。
    # 请将代码复制到 `dist_main.py` 文件中，并使用 `python dist_main.py` 命令运行此代码。
    # 在运行此代码之前，请安装分布式版本的 agentscope。

    import time
    import agentscope
    from agentscope.agents import AgentBase
    from agentscope.message import Msg

    class WebAgent(AgentBase):

        def __init__(self, name):
            super().__init__(name)

        def get_answer(self, url: str, query: str):
            time.sleep(5)
            return f"来自 {self.name} 的答案"

        def reply(self, x: dict = None) -> dict:
            return Msg(
                name=self.name,
                content=self.get_answer(x.content["url"], x.content["query"])
            )


    QUERY = "示例查询"
    URLS = ["页面_1", "页面_2", "页面_3", "页面_4", "页面_5"]

    def init_without_dist():
        return [WebAgent(f"W{i}") for i in range(len(URLS))]


    def init_with_dist():
        return [WebAgent(f"W{i}").to_dist() for i in range(len(URLS))]


    def run(agents):
        start = time.time()
        results = []
        for i, url in enumerate(URLS):
            results.append(agents[i].reply(
                Msg(
                    name="system",
                    role="system",
                    content={
                        "url": url,
                        "query": QUERY
                    }
                )
            ))
        for result in results:
            print(result.content)
        end = time.time()
        return end - start


    if __name__ == "__main__":
        agentscope.init()
        start = time.time()
        simple_agents = init_without_dist()
        dist_agents = init_with_dist()
        end = time.time()
        print(f"初始化时间：{end - start}")
        print(f"无分布式模式下的运行时间：{run(simple_agents)}")
        print(f"分布式模式下的运行时间：{run(dist_agents)}")


运行此示例的输出如下：

.. code-block:: text

    初始化时间：16.50428819656372
    来自 W0 的答案
    来自 W1 的答案
    来自 W2 的答案
    来自 W3 的答案
    来自 W4 的答案
    无分布式模式下的运行时间：25.034368991851807
    来自 W0 的答案
    来自 W1 的答案
    来自 W3 的答案
    来自 W2 的答案
    来自 W4 的答案
    分布式模式下的运行时间：5.0517587661743164

从上面的示例输出中，我们可以观察到在采用分布式模式后（25秒->5秒），运行速度显著提高。

上面的示例是AgentScope分布式模式最常见的使用场景。当不追求极端性能时，建议直接使用这种方法。
如果您需要进一步优化性能，则需要对AgentScope分布式模式有更深入的了解。
下面将介绍AgentScope分布式模式的高级用法。
"""

###############################################################################
# 高级用法
# ~~~~~~~~~~~~~~~
#
# 本节将介绍 AgentScope 分布式模式的高级使用方法，以进一步提高操作效率。
#
# 基本概念
# --------------
#
#
# 在深入学习高级用法之前，我们必须先了解AgentScope分布式模式的一些基本概念。
#
# - **主进程**：AgentScope应用程序所在的进程被称为主进程。例如，上一节中的 `run` 函数就是在主进程中运行的。每个 AgentScope 应用程序只有一个主进程。
# - **智能体服务器进程**：AgentScope智能体服务器进程是智能体在分布式模式下运行的进程。例如，在上一节中，`dist_agents` 中的所有智能体都在智能体服务器进程中运行。可以有多个AgentScope智能体服务器进程。这些进程可以运行在任何可网络访问的机器上，每个智能体服务器进程中可以同时运行多个智能体。
# - **子进程模式**：在子进程模式下，智能体服务器进程由主进程启动为子进程。例如，在上一节中，`dist_agents` 中的每个智能体实际上都是主进程的一个子进程。这是默认模式，也就是说，如果您直接调用 `to_dist` 函数而不传入任何参数，它将使用此模式。
# - **独立进程模式**：在独立进程模式下，智能体服务器与主进程无关，需要预先在机器上启动。需要向 `to_dist` 函数传递特定参数，具体用法将在下一节中介绍。
#
# 使用独立进程模式
# ----------------------
#
# 与子进程模式相比，独立进程模式可以避免初始化子进程的开销，从而减少执行开始时的延迟。这可以有效地提高多次调用 `to_dist` 的程序的效率。
#
# 在独立进程模式下，您需要预先在机器上启动智能体服务器进程，并向 `to_dist` 函数传递特定参数。这里，我们将继续使用基本用法一节中的示例，假设基本用法的代码文件为 `dist_main.py`。然后，创建并单独运行以下脚本。
#
# .. code-block:: python
#
#     # 请勿在jupyter notebook中运行此代码。
#     # 将此代码复制到名为 `dist_server.py` 的文件中，并使用命令 `python dist_server.py` 运行。
#     # 在运行此代码之前，请安装分布式版本的 agentscope。
#     # pip install agentscope[distributed]
#
#     from dist_main import WebAgent
#     import agentscope
#
#     if __name__ == "__main__":
#         agentscope.init()
#         assistant_server_launcher = RpcAgentServerLauncher(
#             host="localhost",
#             port=12345,
#             custom_agent_classes=[WebAgent],
#         )
#         assistant_server_launcher.launch(in_subprocess=False)
#         assistant_server_launcher.wait_until_terminate()
#
#
# 该脚本在 `dist_server.py` 文件中启动AgentScope智能体服务器进程，该文件位于与基本用法中的 `dist_main.py` 文件相同的目录下。此外，我们还需要对 `dist_main.py` 文件做一些小的修改，添加一个新的 `init_with_dist_independent` 函数，并用这个新函数替换对 `init_with_dist` 的调用。
#
# .. code-block:: python
#
#     def init_with_dist_independent():
#         return [WebAgent(f"W{i}").to_dist(host="localhost", port=12345) for i in range(len(URLS))]
#
#     if __name__ == "__main__":
#         agentscope.init()
#         start = time.time()
#         simple_agents = init_without_dist()
#         dist_agents = init_with_dist_independent()
#         end = time.time()
#         print(f"初始化所需时间：{end - start}")
#         print(f"无分布式模式下的运行时间：{run(simple_agents)}")
#         print(f"分布式模式下的运行时间：{run(dist_agents)}")
#
#
# 完成修改后，打开一个命令提示符并运行 `dist_server.py` 文件。一旦成功启动，再打开另一个命令提示符并运行 `dist_main.py` 文件。
#
# 此时，`dist_main.py` 的输出中初始化时间将显著减少。例如，这里的初始化时间仅为0.02秒。
#
# .. code-block:: text
#
#     初始化所需时间：0.018129825592041016
#     ...
#
#
# 需要注意的是，上面的示例中使用了 `host="localhost"` 和 `port=12345` ，并且 `dist_main.py` 和 `dist_server.py` 都在同一台机器上运行。在实际使用时，`dist_server.py`可以运行在不同的机器上。此时，`host` 应该设置为运行 `dist_server.py` 的机器的 IP 地址，而 `port` 应该设置为任何可用端口，确保不同机器可以通过网络进行通信。
#
# 避免重复初始化
# ------------------------------
#
# 在上面的代码中，`to_dist` 函数是在已经初始化过的智能体上调用的。`to_dist` 的本质是将原始智能体克隆到智能体服务器进程中，同时在主进程中保留一个 `RpcAgent` 作为原始智能体的代理。对这个 `RpcAgent` 的调用将被转发到智能体服务器进程中对应的智能体。
#
# 这种做法存在一个潜在问题：原始智能体会被初始化两次——一次在主进程中，一次在智能体服务器进程中，而且这两次初始化是按顺序执行的，无法通过并行来加速。对于初始化成本较低的智能体，直接调用 `to_dist` 函数不会对性能造成显著影响。但是对于初始化成本较高的智能体，避免冗余初始化就很重要。因此，AgentScope分布式模式提供了一种分布式模式初始化的替代方法，允许直接在任何智能体的初始化函数中传递 `to_dist` 参数，如下面修改后的示例所示：
#
# .. code-block:: python
#
#     def init_with_dist():
#         return [WebAgent(f"W{i}", to_dist=True) for i in range(len(URLS))]
#
#
#     def init_with_dist_independent():
#         return [WebAgent(f"W{i}", to_dist={"host": "localhost", "port": "12345"}) for i in range(len(URLS))]
#
#
# 对于子进程模式，您只需在初始化函数中传递 `to_dist=True` 即可。对于独立进程模式，则需要将原本传递给 `to_dist` 函数的参数以字典形式传递给 `to_dist` 字段。
