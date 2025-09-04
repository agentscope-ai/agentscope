# -*- coding: utf-8 -*-
"""
.. _tracing:

追踪
==============================

AgentScope 实现了基于 OpenTelemetry 的追踪来监控和调试
智能体应用程序的执行，具有以下特性

- 为 LLM、工具、智能体、格式化器等提供内置追踪
- 支持错误和异常追踪
- 在 AgentScope Studio 中提供原生追踪 **可视化**
- 支持连接到 **第三方平台**，如 `Arize-Phoenix <https://github.com/Arize-ai/phoenix>`_、`Langfuse <https://langfuse.com/>`_ 等

设置
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note:: 连接到 :ref:`studio` 或第三方平台应该在应用程序开始时通过调用 ``agentscope.init`` 函数完成。

AgentScope Studio
---------------------------------------

.. figure:: ../../_static/images/studio_tracing.png
    :width: 100%
    :alt: AgentScope Studio 追踪页面
    :class: bordered-image
    :align: center

    *AgentScope Studio 中的追踪页面*


当连接到 AgentScope Studio 时，只需在 ``agentscope.init`` 函数中提供 ``studio_url`` 参数。

.. code-block:: python

    import agentscope

    agentscope.init(studio_url="http://xxx:port")


第三方平台
---------------------------------------

要连接到第三方追踪平台，请在 ``agentscope.init`` 函数中设置 ``tracing_url`` 参数。
``tracing_url`` 是您的 OpenTelemetry 收集器或任何支持 OTLP（OpenTelemetry 协议）的服务器 URL。

.. code-block:: python

    import agentscope

    # 连接到 OpenTelemetry 兼容的后端
    agentscope.init(tracing_url="https://your-tracing-backend:port/traces")

以 Arize-Phoenix 和 Langfuse 为例：

**Arize-Phoenix**：需要在环境变量中设置 ``PHOENIX_API_KEY``。

.. code-block:: python
    :caption: 连接到 Arize Phoenix

    # Arize Phoenix 集成
    import os

    PHOENIX_API_KEY = os.environ.get("PHOENIX_API_KEY")
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"api_key={PHOENIX_API_KEY}"

    agentscope.init(tracing_url="https://app.phoenix.arize.com/v1/traces")

**LangFuse**：需要在环境变量中设置 ``LANGFUSE_PUBLIC_KEY`` 和 ``LANGFUSE_SECRET_KEY``。
授权头是使用这些密钥构建的。

.. code-block:: python
    :caption: 连接到 LangFuse

    import os, base64

    LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
    LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
    LANGFUSE_AUTH_STRING = f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}"

    LANGFUSE_AUTH = base64.b64encode(LANGFUSE_AUTH_STRING.encode("utf-8")).decode("ascii")
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {LANGFUSE_AUTH}"

    # 欧盟数据区域
    agentscope.init(tracing_url="https://cloud.langfuse.com/api/public/otel/v1/traces")
    # 美国数据区域
    # agentscope.init(tracing_url="https://us.cloud.langfuse.com/api/public/otel/v1/traces")


自定义追踪
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

如前所述，AgentScope 中的追踪功能是基于 OpenTelemetry 实现的。
这意味着 AgentScope 中的追踪与开发者基于 OpenTelemetry SDK 自己实现的的追踪代码**完全兼容**。

此外，AgentScope 内置了以下装饰器来追踪相应的模块，它们对不同类的特殊属性，以及返回值做了相应的特殊处理：

- ``@trace_llm``：追踪 ``ChatModelBase`` 子类的 ``__call__`` 函数
- ``@trace_reply``：追踪 ``AgentBase`` 子类的 ``reply`` 函数
- ``@trace_format``：追踪 ``FormatterBase`` 子类的 ``format`` 函数
- ``@trace``：追踪一般函数


追踪大语言模型
----------------------------------------

``@trace_llm`` 装饰器用于追踪 ``ChatModelBase`` 类的 ``__call__`` 函数。

.. code-block:: python
    :caption: 追踪新的 ChatModel 类

    class ExampleChatModel(ChatModelBase):
        \"\"\"示例模型\"\"\"

        ...

        @trace_llm
        async def __call__(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> AsyncGenerator[ChatResponse, None] | ChatResponse:
            \"\"\"LLM 调用\"\"\"
            ...


追踪智能体
----------------------------------------

``@trace_reply`` 装饰器用于追踪智能体的 `reply` 函数。

.. code-block:: python
    :caption: 追踪新的 Agent 类

    class ExampleAgent(AgentBase):
        \"\"\"示例智能体类\"\"\"

        @trace_reply
        async def reply(self, *args: Any, **kwargs: Any) -> Msg:
            \"\"\"回复消息。\"\"\"
            ...


追踪格式化器
----------------------------------------
``@trace_format`` 装饰器用于格式化器实现并追踪 `format` 函数。

.. code-block:: python
    :caption: 追踪新的 Formatter 类

    class ExampleFormatter(FormatterBase):
        \"\"\"简单的示例格式化器类\"\"\"

        @trace_format
        async def format(self, *args: Any, **kwargs: Any) -> list[dict]:
            \"\"\"示例格式化\"\"\"


一般函数追踪
----------------------------------------

``@trace`` 装饰器与上述装饰器不同，它是一个通用的追踪装饰器，可以应用于任何函数。
它需要一个 `name` 参数来标识被追踪的函数，并且可以追踪各种类型的函数，包括：

- 同步函数
- 同步生成器函数
- 异步函数
- 异步生成器函数

.. code-block:: python
    :caption: 一般追踪示例

    # 1. 同步函数
    @trace(name='simple_function')
    def simple_function(name: str, age: int) -> str:
        \"\"\"带有自动追踪的简单函数。\"\"\"
        return f"你好, {name}! 你的年龄是 {age} 岁。"

    # 2. 同步生成器函数
    @trace(name='number_generator')
    def number_generator(n: int) -> Generator[int, None, None]:
        \"\"\"生成从 0 到 n-1 的数字。\"\"\"
        for i in range(n):
            yield i

    # 3. 异步函数
    @trace(name='async_function')
    async def async_function(data: dict) -> dict:
        \"\"\"异步处理数据。\"\"\"
        return {"processed": data}

    # 4. 异步生成器函数
    @trace(name='async_stream')
    async def async_stream(n: int) -> AsyncGenerator[str, None]:
        \"\"\"异步生成数据流。\"\"\"
        for i in range(n):
            yield f"data_{i}"

"""
