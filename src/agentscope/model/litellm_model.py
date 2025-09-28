# -*- coding: utf-8 -*-
"""Model wrapper based on litellm https://docs.litellm.ai/docs/"""
from abc import ABC
from typing import Union, Any, List, Optional, Generator

from loguru import logger

from ._model_usage import ChatUsage
from ._model_utils import _verify_text_content_in_openai_delta_response
from .model import ModelWrapperBase, ModelResponse
from ..formatter import CommonFormatter
from ..message import Msg


class LiteLLMWrapperBase(ModelWrapperBase, ABC):
    """The model wrapper based on LiteLLM API."""

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """
        To use the LiteLLM wrapper, environment variables must be set.
        Different model_name could be using different environment variables.
        For example:
            - for model_name: "gpt-3.5-turbo", you need to set "OPENAI_API_KEY"
            ```
            os.environ["OPENAI_API_KEY"] = "your-api-key"
            ```
            - for model_name: "claude-2", you need to set "ANTHROPIC_API_KEY"
            - for Azure OpenAI, you need to set "AZURE_API_KEY",
            "AZURE_API_BASE", "AZURE_API_VERSION"
        You should refer to the docs in https://docs.litellm.ai/docs/

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in OpenAI API.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in litellm api generation,
                e.g. `temperature`, `seed`.
                For generate_args, please refer to
                https://docs.litellm.ai/docs/completion/input
                for more details.

        """

        if model_name is None:
            model_name = config_name
            logger.warning("model_name is not set, use config_name instead.")

        super().__init__(config_name=config_name, model_name=model_name)

        self.generate_args = generate_args or {}


class LiteLLMChatWrapper(LiteLLMWrapperBase):
    """The model wrapper based on litellm chat API.

    Note:
        - litellm requires the users to set api key in their environment
        - Different LLMs requires different environment variables

    Example:
        - For OpenAI model, set "OPENAI_API_KEY"
        - For model like "claude-2", set "ANTHROPIC_API_KEY"
        - For Azure OpenAI model, you need to set "AZURE_API_KEY",
        "AZURE_API_BASE" and "AZURE_API_VERSION"
        - Refer to the docs in https://docs.litellm.ai/docs/ .


    Response:
        - From https://docs.litellm.ai/docs/completion/output

        .. code-block:: json

            {
                'choices': [
                    {
                        'finish_reason': str,  # String: 'stop'
                        'index': int,  # Integer: 0
                        'message': {  # Dictionary [str, str]
                            'role': str,  # String: 'assistant'
                            'content': str  # String: "default message"
                        }
                    }
                ],
                'created': str,  # String: None
                'model': str,  # String: None
                'usage': {  # Dictionary [str, int]
                    'prompt_tokens': int,  # Integer
                    'completion_tokens': int,  # Integer
                    'total_tokens': int  # Integer
                }
            }

    """

    model_type: str = "litellm_chat"

    def __init__(
        self,
        config_name: str,
        model_name: str = None,
        stream: bool = False,
        generate_args: dict = None,
        **kwargs: Any,
    ) -> None:
        """
        To use the LiteLLM wrapper, environment variables must be set.
        Different model_name could be using different environment variables.
        For example:
            - for model_name: "gpt-3.5-turbo", you need to set "OPENAI_API_KEY"
            ```
            os.environ["OPENAI_API_KEY"] = "your-api-key"
            ```
            - for model_name: "claude-2", you need to set "ANTHROPIC_API_KEY"
            - for Azure OpenAI, you need to set "AZURE_API_KEY",
            "AZURE_API_BASE", "AZURE_API_VERSION"
        You should refer to the docs in https://docs.litellm.ai/docs/

        Args:
            config_name (`str`):
                The name of the model config.
            model_name (`str`, default `None`):
                The name of the model to use in OpenAI API.
            stream (`bool`, default `False`):
                Whether to enable stream mode.
            generate_args (`dict`, default `None`):
                The extra keyword arguments used in litellm api generation,
                e.g. `temperature`, `seed`.
                For generate_args, please refer to
                https://docs.litellm.ai/docs/completion/input
                for more details.

        """

        super().__init__(
            config_name=config_name,
            model_name=model_name,
            generate_args=generate_args,
            **kwargs,
        )

        self.stream = stream

    def __call__(
        self,
        messages: list,
        stream: Optional[bool] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """
        Args:
            messages (`list`):
                A list of messages to process.
            stream (`Optional[bool]`, default `None`):
                Whether to enable stream mode. If not set, the stream mode
                will be set to the value in the initialization.
            **kwargs (`Any`):
                The keyword arguments to litellm chat completions API,
                e.g. `temperature`, `max_tokens`, `top_p`, etc. Please refer to
                https://docs.litellm.ai/docs/completion/input
                for more detailed arguments.

        Returns:
            `ModelResponse`:
                The response text in text field, and the raw response in
                raw field.
        """

        # step1: prepare keyword arguments
        kwargs = {**self.generate_args, **kwargs}

        # step2: checking messages
        if not isinstance(messages, list):
            raise ValueError(
                "LiteLLM `messages` field expected type `list`, "
                f"got `{type(messages)}` instead.",
            )
        if not all("role" in msg and "content" in msg for msg in messages):
            raise ValueError(
                "Each message in the 'messages' list must contain a 'role' "
                "and 'content' key for LiteLLM API.",
            )

        # Import litellm only when it is used
        try:
            import litellm
        except ImportError as e:
            raise ImportError(
                "Cannot find litellm in current environment, please "
                "install it by `pip install litellm`.",
            ) from e

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

        # Add stream_options to obtain the usage information
        if stream:
            kwargs["stream_options"] = {"include_usage": True}

        response = litellm.completion(**kwargs)

        if stream:

            def generator() -> Generator[str, None, None]:
                text = ""
                last_chunk = {}
                for chunk in response:
                    # In litellm, the content maybe `None` for the last second
                    # chunk
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

            # return response
            return ModelResponse(
                text=response["choices"][0]["message"]["content"],
                raw=response,
            )

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: dict,
    ) -> None:
        """Save the model invocation and update the monitor accordingly."""
        usage = response.get("usage", None)

        if usage:
            formatted_usage = ChatUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        else:
            formatted_usage = None

        # step4: record the api invocation if needed
        self._save_model_invocation(
            arguments=kwargs,
            response=response,
            usage=formatted_usage,
        )

        # step5: update monitor accordingly
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
                `None` input will be ignored.
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
