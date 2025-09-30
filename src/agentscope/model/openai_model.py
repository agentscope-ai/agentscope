# -*- coding: utf-8 -*-
"""Model wrapper for OpenAI model"""
import json
from abc import ABC
from typing import (
    Union,
    Any,
    List,
    Optional,
    Generator,
)

from loguru import logger

from ._model_usage import ChatUsage
from ._model_utils import (
    _verify_text_content_in_openai_delta_response,
    _verify_text_content_in_openai_message_response,
)
from .model import ModelWrapperBase, ModelResponse
from ..formatter import OpenAIChatFormatter, CommonFormatter
from ..manager import FileManager
from ..message import Msg, ToolUseBlock
from ..utils.token_utils import get_openai_max_length


class OpenAIWrapperBase(ModelWrapperBase, ABC):
    """The model wrapper for OpenAI API.

    Response:
        - From https://platform.openai.com/docs/api-reference/chat/create

        .. code-block:: json

            {
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": "gpt-4o-mini",
                "system_fingerprint": "fp_44709d6fcb",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello there, how may I assist you?",
                        },
                        "logprobs": null,
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 12,
                    "total_tokens": 21
                }
            }

    """

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        organization: str = None,
        client_args: dict = None,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the openai client.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in OpenAI API.
            api_key (`str`, default `None`):
                The API key for OpenAI API. If not specified, it will
                be read from the environment variable `OPENAI_API_KEY`.
            organization (`str`, default `None`):
                The organization ID for OpenAI API. If not specified, it will
                be read from the environment variable `OPENAI_ORGANIZATION`.
            client_args (`dict`, default `None`):
                The extra keyword arguments to initialize the OpenAI client.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in openai api generation,
                e.g. `temperature`, `seed`.
        """

        if model_name is None:
            model_name = config_name
            logger.warning("model_name is not set, use config_name instead.")

        super().__init__(config_name=config_name, model_name=model_name)

        self.generate_args = generate_args or {}

        try:
            import openai
        except ImportError as e:
            raise ImportError(
                "Cannot find openai package, please install it by "
                "`pip install openai`",
            ) from e

        self.client = openai.OpenAI(
            api_key=api_key,
            organization=organization,
            **(client_args or {}),
        )

        # Set the max length of OpenAI model
        try:
            self.max_length = get_openai_max_length(self.model_name)
        except Exception as e:
            logger.warning(
                f"Fail to get max_length for {self.model_name}: " f"{e}",
            )
            self.max_length = None


class OpenAIChatWrapper(OpenAIWrapperBase):
    """The model wrapper for OpenAI's chat API."""

    model_type: str = "openai_chat"

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        api_key: str = None,
        organization: str = None,
        client_args: dict = None,
        stream: bool = False,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the openai client.

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in OpenAI API.
            api_key (`str`, default `None`):
                The API key for OpenAI API. If not specified, it will
                be read from the environment variable `OPENAI_API_KEY`.
            organization (`str`, default `None`):
                The organization ID for OpenAI API. If not specified, it will
                be read from the environment variable `OPENAI_ORGANIZATION`.
            client_args (`dict`, default `None`):
                The extra keyword arguments to initialize the OpenAI client.
            stream (`bool`, default `False`):
                Whether to enable stream mode.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in openai api generation,
                e.g. `temperature`, `seed`.
        """

        super().__init__(
            config_name=config_name,
            model_name=model_name,
            api_key=api_key,
            organization=organization,
            client_args=client_args,
            generate_args=generate_args,
            **kwargs,
        )

        self.stream = stream

    def __call__(
        self,
        messages: list[dict],
        stream: Optional[bool] = None,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Processes a list of messages to construct a payload for the OpenAI
        API call. It then makes a request to the OpenAI API and returns the
        response. This method also updates monitoring metrics based on the
        API response.

        Each message in the 'messages' list can contain text content and
        optionally an 'image_urls' key. If 'image_urls' is provided,
        it is expected to be a list of strings representing URLs to images.
        These URLs will be transformed to a suitable format for the OpenAI
        API, which might involve converting local file paths to data URIs.

        Args:
            messages (`list`):
                A list of messages to process.
            stream (`Optional[bool]`, defaults to `None`)
                Whether to enable stream mode, which will override the
                `stream` argument in the constructor if provided.
            tools (`Optional[list[dict]]`, defaults to `None`):
                The tool JSON schemas that the model can use.
            tool_choice (`Optional[str]`, defaults to `None`):
                The function name that force the model to use.
            **kwargs (`Any`):
                The keyword arguments to OpenAI chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please refer to
                https://platform.openai.com/docs/api-reference/chat/create
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
                "OpenAI `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for OpenAI API.",
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

        if tools:
            kwargs["tools"] = tools

        if tool_choice:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {
                    "name": tool_choice,
                },
            }

        if stream:
            kwargs["stream_options"] = {"include_usage": True}

        response = self.client.chat.completions.create(**kwargs)

        if stream:

            def generator() -> Generator[str, None, None]:
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
            self._save_model_invocation_and_update_monitor(
                kwargs,
                response,
            )

            if _verify_text_content_in_openai_message_response(
                response,
                allow_content_none=True,
            ):
                tool_calls = response["choices"][0]["message"].get(
                    "tool_calls",
                    None,
                )

                if tool_calls is not None:
                    tool_calls = [
                        ToolUseBlock(
                            type="tool_use",
                            id=_["id"],
                            name=_["function"]["name"],
                            input=json.loads(_["function"]["arguments"]),
                        )
                        for _ in tool_calls
                    ]

                # return response
                return ModelResponse(
                    text=response["choices"][0]["message"]["content"],
                    raw=response,
                    tool_calls=tool_calls,
                )
            else:
                raise RuntimeError(
                    f"Invalid response from OpenAI API: {response}",
                )

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: dict,
    ) -> None:
        """Save model invocation and update the monitor accordingly.

        Args:
            kwargs (`dict`):
                The keyword arguments used in model invocation
            response (`dict`):
                The response from model API
        """
        usage = response.get("usage", None)

        if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
            formatted_usage = ChatUsage(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        else:
            formatted_usage = None

        self._save_model_invocation(
            arguments=kwargs,
            response=response,
            usage=formatted_usage,
        )

        usage = response.get("usage", None)
        if usage is not None:
            self.monitor.update_text_and_embedding_tokens(
                model_name=self.model_name,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> List[dict]:
        """Format the input string and dictionary into the format that
        OpenAI Chat API required. If you're using a OpenAI-compatible model
        without a prefix "gpt-" in its name, the format method will
        automatically format the input messages into the required format.

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
            `List[dict]`:
                The formatted messages in the format that OpenAI Chat API
                required.
        """

        # Multi agent scenario
        if multi_agent_mode:
            # Format messages according to the model name
            if OpenAIChatFormatter.is_supported_model(self.model_name):
                return OpenAIChatFormatter._format(*args)

            return CommonFormatter.format_multi_agent(*args)

        # Chat scenario
        if OpenAIChatFormatter.is_supported_model(self.model_name):
            return OpenAIChatFormatter.format_chat(*args)

        return CommonFormatter.format_chat(*args)

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
        return OpenAIChatFormatter.format_tools_json_schemas(schemas)


class OpenAIDALLEWrapper(OpenAIWrapperBase):
    """The model wrapper for OpenAI's DALL·E API.

    Response:
        - Refer to https://platform.openai.com/docs/api-reference/images/create

        .. code-block:: json

            {
                "created": 1589478378,
                "data": [
                    {
                        "url": "https://..."
                    },
                    {
                        "url": "https://..."
                    }
                ]
            }

    """

    model_type: str = "openai_dall_e"

    _resolutions: list = [
        "1792*1024",
        "1024*1792",
        "1024*1024",
        "512*512",
        "256*256",
    ]

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
                The keyword arguments to OpenAI image generation API, e.g.
                `n`, `quality`, `response_format`, `size`, etc. Please refer to
                https://platform.openai.com/docs/api-reference/images/create
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                A list of image urls in image_urls field and the
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
        try:
            response = self.client.images.generate(
                model=self.model_name,
                prompt=prompt,
                **kwargs,
            )
        except Exception as e:
            logger.error(
                f"Failed to generate images for prompt '{prompt}': {e}",
            )
            raise e

        # step3: record the model api invocation if needed
        self._save_model_invocation(
            arguments={
                "model": self.model_name,
                "prompt": prompt,
                **kwargs,
            },
            response=response.model_dump(),
        )

        # step4: update monitor accordingly
        resolution = (
            kwargs.get("quality", "standard")
            + "-"
            + kwargs.get("size", "1024*1024")
        )
        self.monitor.update_image_tokens(
            model_name=self.model_name,
            resolution=resolution,
            image_count=kwargs.get("n", 1),
        )

        # step5: return response
        raw_response = response.model_dump()
        if "data" not in raw_response:
            if "error" in raw_response:
                error_msg = raw_response["error"]["message"]
            else:
                error_msg = raw_response
            logger.error(f"Error in OpenAI API call:\n{error_msg}")
            raise ValueError(f"Error in OpenAI API call:\n{error_msg}")
        images = raw_response["data"]
        # Get image urls as a list
        urls = [_["url"] for _ in images]

        file_manager = FileManager.get_instance()
        if save_local:
            # Return local url if save_local is True
            urls = [file_manager.save_image(_) for _ in urls]
        return ModelResponse(image_urls=urls, raw=raw_response)


class OpenAIEmbeddingWrapper(OpenAIWrapperBase):
    """The model wrapper for OpenAI embedding API.

    Response:
        - Refer to
        https://platform.openai.com/docs/api-reference/embeddings/create

        .. code-block:: json

            {
                "object": "list",
                "data": [
                    {
                        "object": "embedding",
                        "embedding": [
                            0.0023064255,
                            -0.009327292,
                            .... (1536 floats total for ada-002)
                            -0.0028842222,
                        ],
                        "index": 0
                    }
                ],
                "model": "text-embedding-ada-002",
                "usage": {
                    "prompt_tokens": 8,
                    "total_tokens": 8
                }
            }

    """

    model_type: str = "openai_embedding"

    def __call__(
        self,
        texts: Union[list[str], str],
        **kwargs: Any,
    ) -> ModelResponse:
        """Embed the messages with OpenAI embedding API.

        Args:
            texts (`list[str]` or `str`):
                The messages used to embed.
            **kwargs (`Any`):
                The keyword arguments to OpenAI embedding API,
                e.g. `encoding_format`, `user`. Please refer to
                https://platform.openai.com/docs/api-reference/embeddings
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
            **kwargs,
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
            total_tokens=response.usage.total_tokens,
        )

        # step5: return response
        response_json = response.model_dump()
        return ModelResponse(
            embedding=[_["embedding"] for _ in response_json["data"]],
            raw=response_json,
        )
