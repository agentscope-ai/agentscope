# -*- coding: utf-8 -*-
"""Google Gemini model wrapper."""
import os
from abc import ABC
from collections.abc import Iterable
from typing import Sequence, Union, Any, List, Optional, Generator

from loguru import logger

from ._model_usage import ChatUsage
from ..formatter import GeminiFormatter
from ..message import Msg
from ..model import ModelWrapperBase, ModelResponse

try:
    import google.generativeai as genai

    # This package will be installed when the google-generativeai is installed
    import google.ai.generativelanguage as glm
except ImportError:
    genai = None
    glm = None


class GeminiWrapperBase(ModelWrapperBase, ABC):
    """The base class for Google Gemini model wrapper."""

    _generation_method = None
    """The generation method used in `__call__` function, which is used to
    filter model in `list_models` function."""

    def __init__(
        self,
        config_name: str,
        model_name: str,
        api_key: str = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the wrapper for Google Gemini model.

        Args:
            model_name (`str`):
                The name of the model.
            api_key (`str`, defaults to `None`):
                The api_key for the model. If it is not provided, it will be
                loaded from environment variable.
        """
        super().__init__(config_name=config_name, model_name=model_name)

        # Test if the required package is installed
        if genai is None:
            raise ImportError(
                "The google-generativeai package is not installed, "
                "please install it first.",
            )

        # Load the api_key from argument or environment variable
        api_key = api_key or os.environ.get("GOOGLE_API_KEY")

        if api_key is None:
            raise ValueError(
                "Google api_key must be provided or set as an "
                "environment variable.",
            )

        genai.configure(api_key=api_key, **kwargs)

        self.model_name = model_name

    def list_models(self) -> Sequence:
        """List all available model for this API calling."""
        support_models = list(genai.list_models())

        if self.generation_method is None:
            return support_models
        else:
            return [
                _
                for _ in support_models
                if self._generation_method in _.supported_generation_methods
            ]

    def __call__(self, *args: Any, **kwargs: Any) -> ModelResponse:
        """Processing input with the model."""
        raise NotImplementedError(
            f"Model Wrapper [{type(self).__name__}]"
            f" is missing the  the required `__call__`"
            f" method.",
        )


class GeminiChatWrapper(GeminiWrapperBase):
    """The wrapper for Google Gemini chat model, e.g. gemini-pro"""

    model_type: str = "gemini_chat"
    """The type of the model, which is used in model configuration."""

    generation_method = "generateContent"
    """The generation method used in `__call__` function."""

    def __init__(
        self,
        config_name: str,
        model_name: str,
        api_key: str = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the wrapper for Google Gemini model.

        Args:
            model_name (`str`):
                The name of the model.
            api_key (`str`, defaults to `None`):
                The api_key for the model. If it is not provided, it will be
                loaded from environment variable.
            stream (`bool`, defaults to `False`):
                Whether to use stream mode.
        """
        super().__init__(
            config_name=config_name,
            model_name=model_name,
            api_key=api_key,
            **kwargs,
        )

        self.stream = stream

        # Create the generative model
        self.model = genai.GenerativeModel(model_name, **kwargs)

    def __call__(
        self,
        contents: Union[Sequence, str],
        stream: Optional[bool] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Generate response for the given contents.

        Args:
            contents (`Union[Sequence, str]`):
                The content to generate response.
            stream (`Optional[bool]`, defaults to `None`)
                Whether to use stream mode.
            **kwargs:
                The additional arguments for generating response.

        Returns:
            `ModelResponse`:
                The response text in text field, and the raw response in raw
                field.
        """
        # step1: checking messages
        if isinstance(contents, Iterable):
            pass
        elif not isinstance(contents, str):
            logger.warning(
                "The input content is not a string or a list of "
                "messages, which may cause unexpected behavior.",
            )

        # Check if stream is provided
        if stream is None:
            stream = self.stream

        # step2: forward to generate response
        kwargs.update(
            {
                "contents": contents,
                "stream": stream,
            },
        )

        response = self.model.generate_content(**kwargs)

        if stream:

            def generator() -> Generator[str, None, None]:
                text = ""
                last_chunk = None
                for chunk in response:
                    text += self._extract_text_content_from_response(
                        contents,
                        chunk,
                    )
                    yield text
                    last_chunk = chunk

                # Update the last chunk
                last_chunk.candidates[0].content.parts[0].text = text

                self._save_model_invocation_and_update_monitor(
                    contents,
                    kwargs,
                    last_chunk,
                )

            return ModelResponse(
                stream=generator(),
            )

        else:
            self._save_model_invocation_and_update_monitor(
                contents,
                kwargs,
                response,
            )

            # step6: return response
            return ModelResponse(
                text=response.text,
                raw=response,
            )

    def _save_model_invocation_and_update_monitor(
        self,
        contents: Union[Sequence[Any], str],
        kwargs: dict,
        response: Any,
    ) -> None:
        """Save the model invocation and update the monitor accordingly."""
        # Update monitor accordingly
        if hasattr(response, "usage_metadata"):
            prompt_tokens = response.usage_metadata.prompt_token_count
            completion_tokens = response.usage_metadata.candidates_token_count
        else:
            prompt_tokens = self.model.count_tokens(contents).total_tokens
            completion_tokens = self.model.count_tokens(
                response.text,
            ).total_tokens

        formatted_usage = ChatUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        # Record the api invocation if needed
        self._save_model_invocation(
            arguments=kwargs,
            response=str(response),
            usage=formatted_usage,
        )

        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
            **formatted_usage.usage.model_dump(),
        )

    def _extract_text_content_from_response(
        self,
        contents: Union[Sequence[Any], str],
        response: Any,
    ) -> str:
        """Extract the text content from the response of gemini API

        Note: to avoid import error during type checking in python 3.11+, here
        we use `typing.Any` to avoid raising error

        Args:
            contents (`Union[Sequence[Any], str]`):
                The prompt contents
            response (`Any`):
                The response from gemini API

        Returns:
            `str`: The extracted string.
        """
        # Check for candidates and handle accordingly
        if (
            not response.candidates[0].content
            or not response.candidates[0].content.parts
            or not response.candidates[0].content.parts[0].text
        ):
            # If we cannot get the response text from the model
            finish_reason = response.candidates[0].finish_reason
            reasons = glm.Candidate.FinishReason

            if finish_reason == reasons.STOP:
                error_info = (
                    "Natural stop point of the model or provided stop "
                    "sequence."
                )
            elif finish_reason == reasons.MAX_TOKENS:
                error_info = (
                    "The maximum number of tokens as specified in the request "
                    "was reached."
                )
            elif finish_reason == reasons.SAFETY:
                error_info = (
                    "The candidate content was flagged for safety reasons."
                )
            elif finish_reason == reasons.RECITATION:
                error_info = (
                    "The candidate content was flagged for recitation reasons."
                )
            elif finish_reason in [
                reasons.FINISH_REASON_UNSPECIFIED,
                reasons.OTHER,
            ]:
                error_info = "Unknown error."
            else:
                error_info = "No information provided from Gemini API."

            raise ValueError(
                "The Google Gemini API failed to generate text response with "
                f"the following finish reason: {error_info}\n"
                f"YOUR INPUT: {contents}\n"
                f"RAW RESPONSE FROM GEMINI API: {response}\n",
            )

        return response.text

    @staticmethod
    def format(
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> List[dict]:
        """This function provide a basic prompting strategy for Gemini Chat
        API in multi-party conversation, which combines all input into a
        single string, and wrap it into a user message.

        We make the above decision based on the following constraints of the
        Gemini generate API:

        1. In Gemini `generate_content` API, the `role` field must be either
        `user` or `model`.

        2. If we pass a list of messages to the `generate_content` API,
        the `user` role must speak in the beginning and end of the
        messages, and `user` and `model` must alternative. This prevents
        us to build a multi-party conversations, where `model` may keep
        speaking in different names.

        The above information is updated to 2024/03/21. More information
        about the Gemini `generate_content` API can be found in
        https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/gemini

        Based on the above considerations, we decide to combine all messages
        into a single user message. This is a simple and straightforward
        strategy, if you have any better ideas, pull request and
        discussion are welcome in our GitHub repository
        https://github.com/agentscope/agentscope!

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
                A list with one user message.
        """
        if multi_agent_mode:
            return GeminiFormatter.format_multi_agent(*args)
        return GeminiFormatter.format_chat(*args)


class GeminiEmbeddingWrapper(GeminiWrapperBase):
    """The wrapper for Google Gemini embedding model,
    e.g. model/embedding-001

    Response:
        - Refer to https://ai.google.dev/api/embeddings?hl=zh-cn#response-body

        .. code-block:: json

            {
                "embeddings": [
                    {
                        object (ContentEmbedding)
                    }
                ]
            }

    """

    model_type: str = "gemini_embedding"
    """The type of the model, which is used in model configuration."""

    _generation_method = "embedContent"
    """The generation method used in `__call__` function."""

    def __call__(
        self,
        content: Union[Sequence[Msg], str],
        task_type: str = None,
        title: str = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Generate embedding for the given content. More detailed information
        please refer to
        https://ai.google.dev/tutorials/python_quickstart#use_embeddings

        Args:
            content (`Union[Sequence[Msg], str]`):
                The content to generate embedding.
            task_type (`str`, defaults to `None`):
                The type of the task.
            title (`str`, defaults to `None`):
                The title of the content.
            **kwargs:
                The additional arguments for generating embedding.

        Returns:
            `ModelResponse`:
                The response embedding in embedding field, and the raw response
                in raw field.
        """

        # step1: forward to generate response
        response = genai.embed_content(
            model=self.model_name,
            content=content,
            task_type=task_type,
            title=title,
            **kwargs,
        )

        # step2: record the api invocation if needed
        self._save_model_invocation(
            arguments={
                "content": content,
                "task_type": task_type,
                "title": title,
                **kwargs,
            },
            response=response,
        )

        # TODO: Up to 2024/07/26, the embedding model doesn't support to
        #  count tokens.
        # step3: update monitor accordingly
        self.monitor.update_text_and_embedding_tokens(
            model_name=self.model_name,
        )

        return ModelResponse(
            raw=response,
            embedding=response["embedding"],
        )
