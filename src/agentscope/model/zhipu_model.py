# -*- coding: utf-8 -*-
"""Model wrapper for ZhipuAI model"""
from abc import ABC
from typing import Union, Any, List, Optional, Generator

from loguru import logger

from ._model_usage import ChatUsage
from ._model_utils import _verify_text_content_in_openai_delta_response
from .model import ModelWrapperBase, ModelResponse
from ..formatter import CommonFormatter
from ..message import Msg

try:
    import zhipuai
except ImportError:
    zhipuai = None


class ZhipuAIWrapperBase(ModelWrapperBase, ABC):
    """The model wrapper for ZhipuAI API."""

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        client_args: dict = None,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the zhipuai client.
        To init the ZhipuAi client, the api_key is required.
        Other client args include base_url and timeout.
        The base_url is set to https://open.bigmodel.cn/api/paas/v4
        if not specified. The timeout arg is set for http request timeout.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in ZhipuAI API.
            api_key (`str`, default `None`):
                The API key for ZhipuAI API. If not specified, it will
                be read from the environment variable.
            client_args (`dict`, default `None`):
                The extra keyword arguments to initialize the ZhipuAI client.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in zhipuai api generation,
                e.g. `temperature`, `seed`.
        """

        if model_name is None:
            model_name = config_name
            logger.warning("model_name is not set, use config_name instead.")

        super().__init__(config_name=config_name, model_name=model_name)

        if zhipuai is None:
            raise ImportError(
                "Cannot find zhipuai package in current python environment.",
            )

        self.model_name = model_name
        self.generate_args = generate_args or {}

        self.client = zhipuai.ZhipuAI(
            api_key=api_key,
            **(client_args or {}),
        )


class ZhipuAIChatWrapper(ZhipuAIWrapperBase):
    """The model wrapper for ZhipuAI's chat API.

    Response:
        - From https://maas.aminer.cn/dev/api#glm-4

        .. code-block:: json

            {
                "created": 1703487403,
                "id": "8239375684858666781",
                "model": "glm-4",
                "request_id": "8239375684858666781",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {
                            "content": "Drawing blueprints with ...",
                            "role": "assistant"
                        }
                    }
                ],
                "usage": {
                    "completion_tokens": 217,
                    "prompt_tokens": 31,
                    "total_tokens": 248
                }
            }

    """

    model_type: str = "zhipuai_chat"

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        stream: bool = False,
        client_args: dict = None,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the zhipuai client.
        To init the ZhipuAi client, the api_key is required.
        Other client args include base_url and timeout.
        The base_url is set to https://open.bigmodel.cn/api/paas/v4
        if not specified. The timeout arg is set for http request timeout.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in ZhipuAI API.
            api_key (`str`, default `None`):
                The API key for ZhipuAI API. If not specified, it will
                be read from the environment variable.
            stream (`bool`, default `False`):
                Whether to enable stream mode.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in zhipuai api generation,
                e.g. `temperature`, `seed`.
        """

        super().__init__(
            config_name=config_name,
            model_name=model_name,
            api_key=api_key,
            client_args=client_args,
            generate_args=generate_args,
        )

        self.stream = stream

    def __call__(
        self,
        messages: list,
        stream: Optional[bool] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Processes a list of messages to construct a payload for the ZhipuAI
        API call. It then makes a request to the ZhipuAI API and returns the
        response. This method also updates monitoring metrics based on the
        API response.

        Args:
            messages (`list`):
                A list of messages to process.
            stream (`Optional[bool]`, default `None`):
                Whether to enable stream mode. If not specified, it will
                use the stream mode set in the constructor.
            **kwargs (`Any`):
                The keyword arguments to ZhipuAI chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please refer to
                https://open.bigmodel.cn/dev/api
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                The response text in text field, and the raw response in
                raw field.

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
        """

        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: checking messages
        if not isinstance(messages, list):
            raise ValueError(
                "ZhipuAI `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for ZhipuAI API.",
            )

        # step3: forward to generate response
        if stream is None:
            stream = self.stream

        kwargs.update(
            {
                "model": self.model_name,
                "messages": messages,
                "stream": stream,
            },
        )

        response = self.client.chat.completions.create(**kwargs)

        if stream:

            def generator() -> Generator[str, None, None]:
                """The generator of response text"""
                text = ""
                last_chunk = {}
                for chunk in response:
                    chunk = chunk.model_dump()
                    if _verify_text_content_in_openai_delta_response(chunk):
                        text += chunk["choices"][0]["delta"]["content"]
                        yield text
                    last_chunk = chunk

                # Update the last chunk to save locally
                if last_chunk.get("choices", []) in [None, []]:
                    last_chunk["choices"] = [{}]

                last_chunk["choices"][0]["message"] = {
                    "role": "assistant",
                    "content": text,
                }

                self._save_model_invocation_and_update_monitor(
                    kwargs,
                    last_chunk,
                )

            return ModelResponse(
                stream=generator(),
            )

        else:
            response = response.model_dump()

            self._save_model_invocation_and_update_monitor(kwargs, response)

            # Return response
            return ModelResponse(
                text=response["choices"][0]["message"]["content"],
                raw=response,
            )

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: dict,
    ) -> None:
        """Save the model invocation and update the monitor accordingly.

        Args:
            kwargs (`dict`):
                The keyword arguments used in model invocation
            response (`dict`):
                The response from model API
        """
        usage = response.get("usage", None)
        if usage:
            formatted_usage = ChatUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        else:
            formatted_usage = None

        self._save_model_invocation(
            arguments=kwargs,
            response=response,
            usage=formatted_usage,
        )

        if formatted_usage:
            self.monitor.update_text_and_embedding_tokens(
                model_name=self.model_name,
                **formatted_usage.usage.model_dump(),
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
                `None` will be ignored.
            multi_agent_mode (`bool`, defaults to `True`):
                Formatting the messages in multi-agent mode or not. If false,
                the messages will be formatted in chat mode, where only a user
                and an assistant roles are involved.

        Returns:
            `List[dict]`:
                The formatted messages.
        """
        if multi_agent_mode:
            return CommonFormatter.format_multi_agent(*args)
        return CommonFormatter.format_chat(*args)


class ZhipuAIEmbeddingWrapper(ZhipuAIWrapperBase):
    """The model wrapper for ZhipuAI embedding API.

    Example Response:

        .. code-block:: json

            {
                "model": "embedding-2",
                "data": [
                    {
                        "embedding": [ (a total of 1024 elements)
                            -0.02675454691052437,
                            0.019060475751757622,
                            ......
                            -0.005519774276763201,
                            0.014949671924114227
                        ],
                        "index": 0,
                        "object": "embedding"
                    }
                ],
                "object": "list",
                "usage": {
                    "completion_tokens": 0,
                    "prompt_tokens": 4,
                    "total_tokens": 4
                }
            }

    """

    model_type: str = "zhipuai_embedding"

    def __call__(
        self,
        texts: str,
        **kwargs: Any,
    ) -> ModelResponse:
        """Embed the messages with ZhipuAI embedding API.

        Args:
            texts (`str`):
                The messages used to embed.
            **kwargs (`Any`):
                The keyword arguments to ZhipuAI embedding API,
                e.g. `encoding_format`, `user`. Please refer to
                https://open.bigmodel.cn/dev/api#text_embedding
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                A list of embeddings in embedding field and the
                raw response in raw field.

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
        """
        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: forward to generate response
        response = self.client.embeddings.create(
            input=texts,
            model=self.model_name,
        )

        # step3: record the model api invocation if needed
        self._save_model_invocation(
            arguments={
                "model": self.model_name,
                "input": texts,
                **kwargs,
            },
            response=response.model_dump(),
        )

        # step4: update monitor accordingly
        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        # step5: return response
        response_json = response.model_dump()
        return ModelResponse(
            embedding=[_["embedding"] for _ in response_json["data"]],
            raw=response_json,
        )
