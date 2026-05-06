# -*- coding: utf-8 -*-
"""The chat model base class."""
import json
from abc import abstractmethod
from copy import deepcopy
from typing import AsyncGenerator, Any, TYPE_CHECKING, Type

import jsonschema
from pydantic import BaseModel

from ._model_response import ChatResponse, StructuredResponse
from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..formatter import FormatterBase
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    URLSource,
    Base64Source,
    UserMsg,
)

if TYPE_CHECKING:
    from ..tool import ToolChoice
else:
    ToolChoice = Any

_TOOL_CHOICE_LITERAL_MODES = {"auto", "none", "required"}


class ChatModelBase:
    """Base class for chat models."""

    model_name: str
    """The model name"""

    stream: bool
    """Is the model output streaming or not"""

    max_retries: int
    """Maximum number of retries on failure"""

    context_length: int
    """The context length of the model, which will be used in context
    compression."""

    fallback_model_name: str | None
    """Fallback model name to use after all retries fail"""

    formatter: FormatterBase
    """The API formatter that format the messages into the required format for
    the underlying API."""

    def __init__(
        self,
        model_name: str,
        stream: bool,
        context_length: int,
        formatter: FormatterBase,
        max_retries: int = 0,
        fallback_model_name: str | None = None,
    ) -> None:
        """Initialize the chat model base class.

        Args:
            model_name (`str`):
                The name of the model
            stream (`bool`):
                Whether the model output is streaming or not
            context_length (`int`):
                The context length of the model, which will be used
                in context compression.
            formatter (`FormatterBase`):
                Formatter for message preprocessing.
            max_retries (`int`, optional):
                Maximum number of retries on failure. Defaults to 0.
            fallback_model_name (`str | None`, optional):
                Fallback model name to use after all retries fail.
        """
        self.model_name = model_name
        self.stream = stream
        self.context_length = context_length
        self.max_retries = max_retries
        self.fallback_model_name = fallback_model_name
        self.formatter = formatter

    async def __call__(
        self,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the model with retry and fallback logic.

        Formats messages using formatter if available, then attempts
        to call the model up to max_retries + 1 times. If all attempts
        fail and a fallback model is configured, retries with that model.

        Args:
            messages (`list[Msg]`):
                The messages to send to the model.
            tools (`list[dict] | None`, optional):
                The tools available to the model.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice mode or function name.
            **kwargs:
                Additional keyword arguments passed to the underlying API.
        """

        last_error: Exception | None = None

        for model_name in self._models_to_try():
            for attempt in range(self.max_retries + 1):
                try:
                    return await self._call_api(
                        model_name,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                        **kwargs,
                    )
                except Exception as e:
                    last_error = e
                    if attempt < self.max_retries:
                        logger.warning(
                            "Attempt %d failed for model %s: %s. Retrying...",
                            attempt + 1,
                            model_name,
                            str(e),
                        )
                    else:
                        logger.warning(
                            "All %d attempt(s) failed for model %s.",
                            self.max_retries + 1,
                            model_name,
                        )

        if last_error is not None:
            raise last_error
        raise RuntimeError("No models to try")

    def _models_to_try(self) -> list[str]:
        """Return the ordered list of model names to try."""
        models = [self.model_name]
        if self.fallback_model_name:
            models.append(self.fallback_model_name)
        return models

    @abstractmethod
    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the underlying API. Subclasses must implement this method.

        Args:
            model_name (`str`):
                The model name to use for this call.
            messages (`list[Msg]`):
                The messages to send to the model.
            tools (`list[dict] | None`, optional):
                The tools available to the model.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice mode or function name.
            **kwargs:
                Additional keyword arguments for the underlying API.
        """

    def _validate_tool_choice(
        self,
        tool_choice: ToolChoice | None,
        tools: list[dict] | None,
    ) -> None:
        """Validate tool_choice parameter.

        Args:
            tool_choice (`ToolChoice | None`):
                Tool choice dict with 'mode' and optional 'tools' fields.
            tools (`list[dict] | None`):
                Available tools list

        Raises:
            `ValueError`:
                If tool_choice is not a valid dict, or mode/tools values
                are invalid.
        """
        if tool_choice is None:
            return

        if not isinstance(tool_choice, dict):
            raise ValueError(
                f"tool_choice must be a dict, got {type(tool_choice)}",
            )

        mode = tool_choice.get("mode")
        if mode is None:
            raise ValueError(
                "tool_choice must contain a 'mode' field.",
            )
        if not isinstance(mode, str):
            raise ValueError(
                f"tool_choice 'mode' must be a str, got {type(mode)}",
            )

        available_functions = [
            tool["function"]["name"] for tool in (tools or [])
        ]

        tool_names = tool_choice.get("tools")
        if tool_names is not None:
            if not isinstance(tool_names, list):
                raise ValueError(
                    f"tool_choice 'tools' field must be a list, "
                    f"got {type(tool_names)}",
                )
            for name in tool_names:
                if name not in available_functions:
                    raise ValueError(
                        f"Invalid tool name '{name}' in tool_choice.tools. "
                        f"Available tools: "
                        f"{', '.join(sorted(available_functions))}",
                    )

        if mode not in _TOOL_CHOICE_LITERAL_MODES:
            # mode is a specific tool name — validate it exists
            # Fall back to all available tools when tool_names is empty or None
            validation_scope = (
                tool_names if tool_names else available_functions
            )
            if mode not in validation_scope:
                raise ValueError(
                    f"Invalid tool name '{mode}' in tool_choice.mode. "
                    + (
                        f"Available tools in tool_choice.tools: "
                        f"{', '.join(sorted(tool_names))}"
                        if tool_names is not None
                        else f"Available tools: "
                        f"{', '.join(sorted(available_functions))}"
                    ),
                )

    async def count_tokens(
        self,
        messages: list[Msg],
        tools: list[dict] | None,
    ) -> int:
        """A quick and unified method to estimate the token count of the
        model input by dividing the total input size in bytes by 4.

        Note a standard way to count the tokens is first formatting the input
        messages into the API required format, then use the tokenizer of the
        underlying API to count the tokens.

        Subclasses may override this method to provide a more accurate
        implementation tailored to their specific tokenizer.

        Args:
            messages (`list[Msg]`):
                The messages to send to the model.
            tools (`list[dict] | None`):
                The tools available to the model.

        Returns:
            `int`:
                The number of tokens in the model.
        """
        cnt = 0

        acc_texts = []
        data_blocks = []
        for msg in messages:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    acc_texts.append(block.text)

                elif isinstance(block, ThinkingBlock):
                    acc_texts.append(block.thinking)

                elif isinstance(block, ToolCallBlock):
                    acc_texts.append(block.input)

                elif isinstance(block, ToolResultBlock):
                    if isinstance(block.output, str):
                        acc_texts.append(block.output)
                    elif isinstance(block.output, list):
                        for item in block.output:
                            if isinstance(item, TextBlock):
                                acc_texts.append(item.text)
                            elif isinstance(item, DataBlock):
                                data_blocks.append(item)

                elif isinstance(block, DataBlock):
                    data_blocks.append(block)

                else:
                    logger.warning(
                        "Unknown block type %s in token counting, skipping.",
                        type(block),
                    )

        # Count the tokens of the tool JSON schemas
        if tools:
            acc_texts.append(json.dumps(tools, ensure_ascii=False))

        # Add the multimodal tokens
        for block in data_blocks:
            if isinstance(block.source, URLSource):
                # We don't download the content here to avoid blocking
                acc_texts.append(str(block.source.url))
            elif isinstance(block.source, Base64Source):
                cnt += len(block.source.data) // 4

        # Count the text tokens
        acc_text = "".join(acc_texts)
        cnt += int(len(acc_text.encode("utf-8")) / 4 + 0.5)

        return cnt

    async def generate_structured_output(
        self,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Generate required structured output by the given model.

        Note this function also shares the fallback model and max retries
        settings with the `__call__` method.

        Args:
            messages (`list[Msg]`):
                The context for LLM to generate the structured output.
            structured_model (`Type[BaseModel] | dict`):
                A Pydantic model or a dict of JSON schemas.

        Returns:
            `StructuredResponse`:
                The structured response generated by the model.
        """

        if len(messages) == 0:
            raise ValueError(
                "The input messages cannot be empty for the "
                "`generate_structured_output` method.",
            )

        last_error: Exception | None = None

        for model_name in self._models_to_try():
            for attempt in range(self.max_retries + 1):
                try:
                    return await self._call_api_with_structured_output(
                        model_name,
                        messages=messages,
                        structured_model=structured_model,
                        **kwargs,
                    )
                except Exception as e:
                    last_error = e
                    if attempt < self.max_retries:
                        logger.warning(
                            "Attempt %d failed for model %s: %s. Retrying...",
                            attempt + 1,
                            model_name,
                            str(e),
                        )
                    else:
                        logger.warning(
                            "All %d attempt(s) failed for model %s.",
                            self.max_retries + 1,
                            model_name,
                        )

        if last_error is not None:
            raise last_error
        raise RuntimeError("No models to try")

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        **kwargs: Any,
    ) -> StructuredResponse:
        """This function constructs a 'generate_structured_output' tool to
        help LLM generate structured output as a compromise for LLM APIs that
        don't support structured output.

        If your subclasses inherit from `ChatModelBase` and the underlying
        API supports structured output, you can override this method to
        provide a more accurate implementation.

        Note this method use the "required" tool choice to force LLM to call
        the 'generate_structured_output' method, and adds instructions into
        the input messages. However, LLM APIs that doesn't support
        "required" tool choice may still fail (e.g. generate text output and
        ignore the tool call, or fail in validation).
        """

        if isinstance(structured_model, dict):
            input_schema = structured_model
        else:
            input_schema = structured_model.model_json_schema()

        func_name = "generate_structured_output"
        instruction = (
            "<system-reminder>Now you **MUST** call the tool named "
            f"'{func_name}' to generate the structured output required "
            "by the user. DON'T do anything else.</system-reminder>"
        )

        copied_messages = deepcopy(messages)
        # Insert instruction to ensure llm is correctly guided
        if copied_messages[-1].role == "user":
            # Insert a user message to the last
            copied_messages[-1].content = copied_messages[
                -1
            ].get_content_blocks() + [TextBlock(text=instruction)]
        else:
            copied_messages.append(
                UserMsg(name="user", content=[TextBlock(text=instruction)]),
            )

        res = await self._call_api(
            model_name=model_name,
            messages=copied_messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "description": "Call this function to generate "
                        "structured output required by "
                        "the user.",
                        "parameters": input_schema,
                    },
                },
            ],
            tool_choice=ToolChoice(
                mode=func_name,
            ),
            **kwargs,
        )

        completed_response: ChatResponse | None = None
        if self.stream:
            async for chunk in res:
                if chunk.is_last:
                    completed_response = chunk
        else:
            completed_response = res

        if completed_response is None:
            raise RuntimeError(
                f"Failed to get the completed response from model "
                f"{model_name}.",
            )

        structured_output: dict[str, Any] | None = None
        for _ in completed_response.content:
            if isinstance(_, ToolCallBlock) and _.name == func_name:
                structured_output = _json_loads_with_repair(
                    _.input,
                    input_schema,
                )
                break

        if structured_output is None:
            raise RuntimeError(
                "Failed to generate structured output for model.",
            )

        # Validate the output
        if isinstance(structured_model, dict):
            jsonschema.validate(structured_output, structured_model)

        elif issubclass(structured_model, BaseModel):
            structured_model.model_validate(structured_output)

        else:
            raise ValueError(
                "The structured_model is expected to be a subclass of "
                "Pydantic.BaseModel or a dict, "
                f"but got {type(structured_model)}.",
            )

        return StructuredResponse(
            id=completed_response.id,
            created_at=completed_response.created_at,
            content=structured_output,
            usage=completed_response.usage,
        )
