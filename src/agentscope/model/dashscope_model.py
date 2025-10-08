# -*- coding: utf-8 -*-
"""Model wrapper for DashScope model"""
import json
from abc import ABC
from http import HTTPStatus
from typing import Any, Union, List, Optional, Generator

from loguru import logger

from ._model_usage import ChatUsage
from ..formatter import DashScopeFormatter
from ..manager import FileManager
from ..message import Msg, ToolUseBlock

try:
    import dashscope

    dashscope_version = dashscope.version.__version__
    if dashscope_version < "1.19.0":
        logger.warning(
            f"You are using 'dashscope' version {dashscope_version}, "
            "which is below the recommended version 1.19.0. "
            "Please consider upgrading to maintain compatibility.",
        )
    from dashscope.api_entities.dashscope_response import GenerationResponse
except ImportError:
    dashscope = None
    GenerationResponse = None

from agentscope.base import ModelWrapperBase, ModelResponse


class DashScopeWrapperBase(ModelWrapperBase, ABC):
    """The model wrapper for DashScope API."""

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the DashScope wrapper.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in DashScope API.
            api_key (`str`, default `None`):
                The API key for DashScope API.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in DashScope api generation,
                e.g. `temperature`, `seed`.
        """
        if model_name is None:
            model_name = config_name
            logger.warning("model_name is not set, use config_name instead.")

        super().__init__(config_name=config_name, model_name=model_name)

        if dashscope is None:
            raise ImportError(
                "The package 'dashscope' is not installed. Please install it "
                "by running `pip install dashscope>=1.19.0`",
            )

        self.generate_args = generate_args or {}

        self.api_key = api_key
        self.max_length = None


class DashScopeChatWrapper(DashScopeWrapperBase):
    """The model wrapper for DashScope's chat API, refer to
    https://help.aliyun.com/zh/dashscope/developer-reference/api-details

    Example Response:
        - Refer to
        https://help.aliyun.com/zh/dashscope/developer-reference/quick-start?spm=a2c4g.11186623.0.0.7e346eb5RvirBw

        .. code-block:: json

            {
                "status_code": 200,
                "request_id": "a75a1b22-e512-957d-891b-37db858ae738",
                "code": "",
                "message": "",
                "output": {
                    "text": null,
                    "finish_reason": null,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "xxx"
                            }
                        }
                    ]
                },
                "usage": {
                    "input_tokens": 25,
                    "output_tokens": 77,
                    "total_tokens": 102
                }
            }

    """

    model_type: str = "dashscope_chat"

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        stream: bool = False,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the DashScope wrapper.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in DashScope API.
            api_key (`str`, default `None`):
                The API key for DashScope API.
            stream (`bool`, default `False`):
                If True, the response will be a generator in the `stream`
                field of the returned `ModelResponse` object.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in DashScope api generation,
                e.g. `temperature`, `seed`.
        """

        super().__init__(
            config_name=config_name,
            model_name=model_name,
            api_key=api_key,
            generate_args=generate_args,
            **kwargs,
        )

        self.stream = stream

    def __call__(
        self,
        messages: list,
        stream: Optional[bool] = None,
        tools: list[dict] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Processes a list of messages to construct a payload for the
        DashScope API call. It then makes a request to the DashScope API
        and returns the response. This method also updates monitoring
        metrics based on the API response.

        Each message in the 'messages' list can contain text content and
        optionally an 'image_urls' key. If 'image_urls' is provided,
        it is expected to be a list of strings representing URLs to images.
        These URLs will be transformed to a suitable format for the DashScope
        API, which might involve converting local file paths to data URIs.

        Args:
            messages (`list`):
                A list of messages to process.
            stream (`Optional[bool]`, default `None`):
                The stream flag to control the response format, which will
                overwrite the stream flag in the constructor.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Optional[str]`, default `None`):
                The function name that force the model to use.
            **kwargs (`Any`):
                The keyword arguments to DashScope chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please
                refer to
                https://help.aliyun.com/zh/dashscope/developer-reference/api-details
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                A response object with the response text in text field, and
                the raw response in raw field. If stream is True, the response
                will be a generator in the `stream` field.

        Note:
            `parse_func`, `fault_handler` and `max_retries` are reserved for
            `_response_parse_decorator` to parse and check the response
            generated by model wrapper. Their usages are listed as follows:
                - `parse_func` is a callable function used to parse and check
                the response generated by the model, which takes the response
                as input.
                - `max_retries` is the maximum number of retries when the
                `parse_func` raise an exception.
                - `fault_handler` is a callable function which is called
                when the response generated by the model is invalid after
                `max_retries` retries.
            The rule of roles in messages for DashScope is very rigid,
            for more details, please refer to
            https://help.aliyun.com/zh/dashscope/developer-reference/api-details
        """

        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: checking messages
        if not isinstance(messages, list):
            raise ValueError(
                "Dashscope `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for DashScope API.",
            )

        # step3: forward to generate response
        if stream is None:
            stream = self.stream

        kwargs.update(
            {
                "model": self.model_name,
                "messages": messages,
                # Set the result to be "message" format.
                "result_format": "message",
                "stream": stream,
            },
        )

        if tools:
            kwargs["tools"] = tools

        if tool_choice:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {
                    "name": tool_choice,
                },
            }

        # Switch to the incremental_output mode
        if stream:
            kwargs["incremental_output"] = True

        response = dashscope.Generation.call(api_key=self.api_key, **kwargs)

        # step3: invoke llm api, record the invocation and update the monitor
        if stream:

            def generator() -> Generator[str, None, None]:
                last_chunk = None
                text = ""
                for chunk in response:
                    if chunk.status_code != HTTPStatus.OK:
                        error_msg = (
                            f"Request id: {chunk.request_id}\n"
                            f"Status code: {chunk.status_code}\n"
                            f"Error code: {chunk.code}\n"
                            f"Error message: {chunk.message}"
                        )
                        raise RuntimeError(error_msg)

                    text += chunk.output["choices"][0]["message"]["content"]
                    yield text
                    last_chunk = chunk

                # Replace the last chunk with the full text
                last_chunk.output["choices"][0]["message"]["content"] = text

                # Save the model invocation and update the monitor
                self._save_model_invocation_and_update_monitor(
                    kwargs,
                    last_chunk,
                )

            return ModelResponse(
                stream=generator(),
                raw=response,
            )

        else:
            if response.status_code != HTTPStatus.OK:
                error_msg = (
                    f"Request id: {response.request_id},\n"
                    f"Status code: {response.status_code},\n"
                    f"Error code: {response.code},\n"
                    f"Error message: {response.message}."
                )

                raise RuntimeError(error_msg)

            # Record the model invocation and update the monitor
            self._save_model_invocation_and_update_monitor(
                kwargs,
                response,
            )

            response_message = response.output["choices"][0]["message"]
            blocks = None
            if "tool_calls" in response_message:
                tool_calls = response_message["tool_calls"]
                blocks = []
                for tool_call in tool_calls:
                    blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=tool_call["id"],
                            name=tool_call["function"]["name"],
                            input=json.loads(
                                tool_call["function"]["arguments"],
                            ),
                        ),
                    )

            text = (
                None
                if response_message["content"] == ""
                else response_message["content"]
            )

            return ModelResponse(
                text=text,
                tool_calls=blocks,
                raw=response,
            )

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: GenerationResponse,
    ) -> None:
        """Save the model invocation and update the monitor accordingly.

        Args:
            kwargs (`dict`):
                The keyword arguments to the DashScope chat API.
            response (`GenerationResponse`):
                The response object returned by the DashScope chat API.
        """
        formatted_usage = ChatUsage(
            prompt_tokens=response.usage.get("input_tokens", 0),
            completion_tokens=response.usage.get("output_tokens", 0),
        )

        # Update the token record accordingly
        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
            **formatted_usage.usage.model_dump(),
        )

        # Save the model invocation after the stream is exhausted
        self._save_model_invocation(
            arguments=kwargs,
            response=response,
            usage=formatted_usage,
        )

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> List[dict]:
        """A common format strategy for chat model, which will format the
        input messages into a user message.

        Note this strategy maybe not suitable for all scenarios,
        and developers are encouraged to implement their own prompt
        engineering strategies.

        The following is an example:

        .. code-block:: python

            prompt1 = model.format(
                Msg("system", "You're a helpful assistant", role="system"),
                Msg("Bob", "Hi, how can I help you?", role="assistant"),
                Msg("user", "What's the date today?", role="user")
            )

            prompt2 = model.format(
                Msg("Bob", "Hi, how can I help you?", role="assistant"),
                Msg("user", "What's the date today?", role="user")
            )

        The prompt will be as follows:

        .. code-block:: python

            # prompt1
            [
                {
                    "role": "system",
                    "content": "You're a helpful assistant"
                },
                {
                    "role": "user",
                    "content": (
                        "## Conversation History\\n"
                        "Bob: Hi, how can I help you?\\n"
                        "user: What's the date today?"
                    )
                }
            ]

            # prompt2
            [
                {
                    "role": "user",
                    "content": (
                        "## Conversation History\\n"
                        "Bob: Hi, how can I help you?\\n"
                        "user: What's the date today?"
                    )
                }
            ]


        Args:
            args (`Union[Msg, list[Msg], None]`):
                The input arguments to be formatted, where each argument
                should be a `Msg` object, or a list of `Msg` objects. The
                `None` object will be ignored.
            multi_agent_mode (`bool`, defaults to `True`):
                Formatting the messages in multi-agent mode or not. If false,
                the messages will be formatted in chat mode, where only a user
                and an assistant roles are involved.

        Returns:
            `List[dict]`:
                The formatted messages.
        """
        if multi_agent_mode:
            return DashScopeFormatter.format_multi_agent(*args)
        return DashScopeFormatter.format_chat(*args)

    def format_tools_json_schemas(
        self,
        schemas: dict[str, dict],
    ) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        the model API provider expects.

        Example:
            An example of the input schemas parsed from the service toolkit

            ..code-block:: json

                {
                    "bing_search": {
                        "type": "function",
                        "function": {
                            "name": "bing_search",
                            "description": "Search the web using Bing.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query.",
                                    }
                                },
                                "required": ["query"],
                            }
                        }
                    }
                }

        Args:
            schemas (`dict[str, dict]`):
                The tools JSON schemas parsed from the service toolkit module,
                which can be accessed by `service_toolkit.json_schemas`.

        Returns:
            `list[dict]`:
                The formatted JSON schemas of the tool functions.
        """
        return DashScopeFormatter.format_tools_json_schemas(schemas)


class DashScopeImageSynthesisWrapper(DashScopeWrapperBase):
    """The model wrapper for DashScope Image Synthesis API, refer to
    https://help.aliyun.com/zh/dashscope/developer-reference/quick-start-1

    Response:
        - Refer to
        https://help.aliyun.com/zh/dashscope/developer-reference/api-details-9?spm=a2c4g.11186623.0.0.7108fa70Op6eqF

        .. code-block:: json

            {
                "status_code": 200,
                "request_id": "b54ffeb8-6212-9dac-808c-b3771cba3788",
                "code": null,
                "message": "",
                "output": {
                    "task_id": "996523eb-034d-459b-ac88-b340b95007a4",
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "url": "RESULT_URL1"
                        },
                        {
                            "url": "RESULT_URL2"
                        },
                    ],
                    "task_metrics": {
                        "TOTAL": 2,
                        "SUCCEEDED": 2,
                        "FAILED": 0
                    }
                },
                "usage": {
                    "image_count": 2
                }
            }

    """

    model_type: str = "dashscope_image_synthesis"

    def __call__(
        self,
        prompt: str,
        save_local: bool = False,
        **kwargs: Any,
    ) -> ModelResponse:
        """
         Args:
             prompt (`str`):
                 The prompt string to generate images from.
             save_local: (`bool`, default `False`):
                 Whether to save the generated images locally, and replace
                 the returned image url with the local path.
             **kwargs (`Any`):
                 The keyword arguments to DashScope Image Synthesis API,
                 e.g. `n`, `size`, etc. Please refer to
                 https://help.aliyun.com/zh/dashscope/developer-reference/api-details-9
        for more detailed arguments.

         Returns:
             `ModelResponse`:
                 A list of image urls in image_urls field and the
                 raw response in raw field.

         Note:
             `parse_func`, `fault_handler` and `max_retries` are reserved
             for `_response_parse_decorator` to parse and check the
             response generated by model wrapper. Their usages are listed
             as follows:
                 - `parse_func` is a callable function used to parse and
                 check the response generated by the model, which takes
                 the response as input.
                 - `max_retries` is the maximum number of retries when the
                 `parse_func` raise an exception.
                 - `fault_handler` is a callable function which is called
                 when the response generated by the model is invalid after
                 `max_retries` retries.
        """
        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: forward to generate response
        response = dashscope.ImageSynthesis.call(
            model=self.model_name,
            prompt=prompt,
            api_key=self.api_key,
            **kwargs,
        )
        if response.status_code != HTTPStatus.OK:
            error_msg = (
                f" Request id: {response.request_id},"
                f" Status code: {response.status_code},"
                f" error code: {response.code},"
                f" error message: {response.message}."
            )
            raise RuntimeError(error_msg)

        # step3: record the model api invocation if needed
        self._save_model_invocation(
            arguments={
                "model": self.model_name,
                "prompt": prompt,
                **kwargs,
            },
            response=response,
        )

        # step4: update monitor accordingly
        self.monitor.update_image_tokens(
            model_name=self.model_name,
            image_count=response.usage.image_count,
            resolution=kwargs.get("size", "1024*1024"),
        )

        # step5: return response
        images = response.output["results"]
        # Get image urls as a list
        urls = [_["url"] for _ in images]

        if save_local:
            file_manager = FileManager.get_instance()
            # Return local url if save_local is True
            urls = [file_manager.save_image(_) for _ in urls]
        return ModelResponse(image_urls=urls, raw=response)


class DashScopeTextEmbeddingWrapper(DashScopeWrapperBase):
    """The model wrapper for DashScope Text Embedding API.

    Response:
        - Refer to
        https://help.aliyun.com/zh/dashscope/developer-reference/text-embedding-api-details?spm=a2c4g.11186623.0.i3

        .. code-block:: json

            {
                "status_code": 200, // 200 indicate success otherwise failed.
                "request_id": "fd564688-43f7-9595-b986", // The request id.
                "code": "", // If failed, the error code.
                "message": "", // If failed, the error message.
                "output": {
                    "embeddings": [ // embeddings
                        {
                            "embedding": [ // one embedding output
                                -3.8450357913970947, ...,
                            ],
                            "text_index": 0 // the input index.
                        }
                    ]
                },
                "usage": {
                    "total_tokens": 3 // the request tokens.
                }
            }

    """

    model_type: str = "dashscope_text_embedding"

    def __call__(
        self,
        texts: Union[list[str], str],
        **kwargs: Any,
    ) -> ModelResponse:
        """Embed the messages with DashScope Text Embedding API.

        Args:
            texts (`list[str]` or `str`):
                The messages used to embed.
            **kwargs (`Any`):
                The keyword arguments to DashScope Text Embedding API,
                e.g. `text_type`. Please refer to
                https://help.aliyun.com/zh/dashscope/developer-reference/api-details-15
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                A list of embeddings in embedding field and the raw
                response in raw field.

        Note:
            `parse_func`, `fault_handler` and `max_retries` are reserved
            for `_response_parse_decorator` to parse and check the response
            generated by model wrapper. Their usages are listed as follows:
                - `parse_func` is a callable function used to parse and
                check the response generated by the model, which takes the
                response as input.
                - `max_retries` is the maximum number of retries when the
                `parse_func` raise an exception.
                - `fault_handler` is a callable function which is called
                when the response generated by the model is invalid after
                `max_retries` retries.
        """
        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: forward to generate response
        response = dashscope.TextEmbedding.call(
            input=texts,
            model=self.model_name,
            api_key=self.api_key,
            **kwargs,
        )

        if response.status_code != HTTPStatus.OK:
            error_msg = (
                f" Request id: {response.request_id},"
                f" Status code: {response.status_code},"
                f" error code: {response.code},"
                f" error message: {response.message}."
            )
            raise RuntimeError(error_msg)

        # step3: record the model api invocation if needed
        self._save_model_invocation(
            arguments={
                "model": self.model_name,
                "input": texts,
                **kwargs,
            },
            response=response,
        )

        # step4: update monitor accordingly
        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
            prompt_tokens=response.usage.get("total_tokens"),
            total_tokens=response.usage.get("total_tokens"),
        )

        # step5: return response
        return ModelResponse(
            embedding=[_["embedding"] for _ in response.output["embeddings"]],
            raw=response,
        )


class DashScopeMultiModalWrapper(DashScopeWrapperBase):
    """The model wrapper for DashScope Multimodal API, refer to
    https://help.aliyun.com/zh/dashscope/developer-reference/tongyi-qianwen-vl-api

    Response:
        - Refer to
        https://help.aliyun.com/zh/dashscope/developer-reference/tongyi-qianwen-vl-plus-api?spm=a2c4g.11186623.0.0.7fde1f5atQSalN

        .. code-block:: json

            {
                "status_code": 200,
                "request_id": "a0dc436c-2ee7-93e0-9667-c462009dec4d",
                "code": "",
                "message": "",
                "output": {
                    "text": null,
                    "finish_reason": null,
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "text": "这张图片显..."
                                    }
                                ]
                            }
                        }
                    ]
                },
                "usage": {
                    "input_tokens": 1277,
                    "output_tokens": 81,
                    "image_tokens": 1247
                }
            }

    """

    model_type: str = "dashscope_multimodal"

    def __call__(
        self,
        messages: list,
        **kwargs: Any,
    ) -> ModelResponse:
        """Model call for DashScope MultiModal API.

        Args:
            messages (`list`):
                A list of messages to process.
            **kwargs (`Any`):
                The keyword arguments to DashScope MultiModal API,
                e.g. `stream`. Please refer to
                https://help.aliyun.com/zh/dashscope/developer-reference/tongyi-qianwen-vl-plus-api
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                The response text in text field, and the raw response in
                raw field.

        Note:
            If involving image links, then the messages should be of the
            following form:

            .. code-block:: python

                messages = [
                    {
                        "role": "system",
                        "content": [
                            {"text": "You are a helpful assistant."},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"text": "What does this picture depict？"},
                            {"image": "http://example.com/image.jpg"},
                        ],
                    },
                ]

            Therefore, you should input a list matching the content value
            above.
            If only involving words, just input them.
        """
        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: forward to generate response
        response = dashscope.MultiModalConversation.call(
            model=self.model_name,
            messages=messages,
            api_key=self.api_key,
            **kwargs,
        )
        # Unhandled code path here
        # response could be a generator , if stream is yes
        # suggest add a check here
        if response.status_code != HTTPStatus.OK:
            error_msg = (
                f" Request id: {response.request_id},"
                f" Status code: {response.status_code},"
                f" error code: {response.code},"
                f" error message: {response.message}."
            )
            raise RuntimeError(error_msg)

        # step3: record the model api invocation if needed
        input_tokens = response.usage.get("input_tokens", 0)
        image_tokens = response.usage.get("image_tokens", 0)
        output_tokens = response.usage.get("output_tokens", 0)

        formatted_usage = ChatUsage(
            prompt_tokens=input_tokens + image_tokens,
            completion_tokens=output_tokens,
        )

        self._save_model_invocation(
            arguments={
                "model": self.model_name,
                "messages": messages,
                **kwargs,
            },
            response=response,
            usage=formatted_usage,
        )

        # step4: update monitor accordingly
        # TODO: update the tokens
        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
            **formatted_usage.usage.model_dump(),
        )

        # step5: return response
        content = response.output["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = content[0]["text"]

        return ModelResponse(
            text=content,
            raw=response,
        )

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> list[dict]:
        """Format the messages for DashScope Multimodal API.

        The multimodal API has the following requirements:

        - The roles of messages must alternate between "user" and "assistant".
        - The message with the role "system" should be the first message
         in the list.
        - If the system message exists, then the second message must
         have the role "user".
        - The last message in the list should have the role "user".
        - In each message, more than one figure is allowed.

        With the above requirements, we format the messages as follows:

        - If the first message is a system message, then we will keep it as
         system prompt.
        - We merge all messages into a conversation history prompt in a
         single message with the role "user".
         - When there are multiple figures in the given messages, we will
          attach it to the user message by order. Note if there are
          multiple figures, this strategy may cause misunderstanding for
          the model. For advanced solutions, developers are encouraged to
          implement their own prompt engineering strategies.

        The following is an example:

        .. code-block:: python

            prompt = model.format(
                Msg(
                    "system",
                    "You're a helpful assistant",
                    role="system", url="figure1"
                ),
                Msg(
                    "Bob",
                    "How about this picture?",
                    role="assistant", url="figure2"
                ),
                Msg(
                    "user",
                    "It's wonderful! How about mine?",
                    role="user", image="figure3"
                )
            )

        The prompt will be as follows:

        .. code-block:: json

            [
                {
                    "role": "system",
                    "content": [
                        {"text": "You are a helpful assistant"},
                        {"image": "figure1"}
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {"image": "figure2"},
                        {"image": "figure3"},
                        {
                            "text": (
                                "## Conversation History\\n"
                                "Bob: How about this picture?\\n"
                                "user: It's wonderful! How about mine?"
                            )
                        },
                    ]
                }
            ]

        Note:
            In multimodal API, the url of local files should be prefixed with
            "file://", which will be attached in this format function.

        Args:
            args (`Union[Msg, list[Msg], None]`):
                The input arguments to be formatted, where each argument
                should be a `Msg` object, or a list of `Msg` objects. The
                `None` input will be ignored.
            multi_agent_mode (`bool`, defaults to `True`):
                Formatting the messages in multi-agent mode or not. If false,
                the messages will be formatted in chat mode, where only a user
                and an assistant roles are involved.

        Returns:
            `list[dict]`:
                The formatted messages.
        """

        if multi_agent_mode:
            return DashScopeFormatter.format_multi_agent(*args)
        return DashScopeFormatter.format_chat(*args)
