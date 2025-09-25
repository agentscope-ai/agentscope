# -*- coding: utf-8 -*-
"""The Anthropic model wrapper for AgentScope."""
from typing import Optional, Union, Generator, Any

from ._model_usage import ChatUsage
from ..formatters import AnthropicFormatter
from ..message import Msg, ToolUseBlock
from .model import ModelWrapperBase, ModelResponse


class AnthropicChatWrapper(ModelWrapperBase):
    """The Anthropic model wrapper for AgentScope."""

    model_type: str = "anthropic_chat"

    _supported_image_format: list[str] = ["jpeg", "png", "gif", "webp"]

    def __init__(
        self,
        model_name: str,
        config_name: Optional[str] = None,
        api_key: Optional[str] = None,
        stream: bool = False,
        client_kwargs: Optional[dict] = None,
    ) -> None:
        """Initialize the Anthropic model wrapper.

        Args:
            model_name (`str`):
                The name of the used model, e.g. `claude-3-5-sonnet-20241022`.
            config_name (`Optional[str]`, defaults to `None`):
                The name of the model configuration.
            api_key (`Optional[str]`, defaults to `None`):
                The API key for the Anthropic API.
            stream (`bool`, defaults to `False`):
                Enable streaming mode or not.
            client_kwargs (`Optional[dict]`, defaults to `None`):
                The additional keyword arguments for the anthropic client.
        """
        super().__init__(config_name, model_name)

        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "Please install the `anthropic` package by running "
                "`pip install anthropic`.",
            ) from e

        client_kwargs = client_kwargs or {}

        self.client = anthropic.Anthropic(
            api_key=api_key,
            **client_kwargs,
        )
        self.stream = stream

    def format(
        self,
        *args: Union[Msg, list[Msg], None],
        multi_agent_mode: bool = True,
    ) -> list[dict[str, object]]:
        """Format the messages for anthropic model input.

        Args:
            *args (`Union[Msg, list[Msg], None]`):
                The message(s) to be formatted. The `None` input will be
                ignored.
            multi_agent_mode (`bool`, defaults to `True`):
                Formatting the messages in multi-agent mode or not. If false,
                the messages will be formatted in chat mode, where only a user
                and an assistant roles are involved.

        Returns:
            `list[dict[str, object]]`:
                A list of formatted messages.
        """
        if multi_agent_mode:
            return AnthropicFormatter.format_multi_agent(*args)
        return AnthropicFormatter.format_chat(*args)

    def __call__(  # pylint: disable=too-many-branches too-many-statements
        self,
        messages: list[dict[str, Union[str, list[dict]]]],
        stream: Optional[bool] = None,
        max_tokens: int = 2048,
        tools: list[dict] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Call the Anthropic model.

        .. note:: The official Anthropic API supports system prompt by a
         separate argument "system". For the convenience of the users, we
         allow the system prompt to be the first message in the input messages.

        Args:
            messages (`list[dict[str, Union[str, list[dict]]]]`):
                A list of message dictionaries. Each dictionary should have
                'role' and 'content' keys.
            stream (`Optional[bool]`, defaults to `None`):
                Enable streaming mode or not.
            max_tokens (`int`, defaults to `2048`):
                The max tokens in generation.
            tools (`list[dict]`, defaults to `None`):
                The tool functions to be used in the model.
            **kwargs (`Any`):
                The additional keyword arguments for the model.

        Returns:
            `ModelResponse`:
                The model response.
        """
        # Check the input messages
        if isinstance(messages, list):
            if len(messages) == 0:
                raise ValueError("The input messages should not be empty.")

            for msg in messages:
                if not isinstance(msg, dict):
                    raise ValueError(
                        "The input messages should be a list of dictionaries, "
                        f"got {type(msg)}",
                    )
                if "role" not in msg or "content" not in msg:
                    raise ValueError(
                        f"Each message should have 'role' and 'content' keys, "
                        f"got {msg}",
                    )
                if msg["role"] not in ["assistant", "user", "system"]:
                    raise ValueError(
                        f"Invalid role {msg['role']}. The role must be one of "
                        f"['assistant', 'user', 'system']",
                    )

        else:
            raise ValueError(
                "The input messages should be a list of dictionaries, "
                f"got {type(messages)}",
            )

        # Check the stream
        if stream is None:
            stream = stream or self.stream

        # Prepare the keyword arguments
        kwargs.update(
            {
                "model": self.model_name,
                "stream": stream,
                "max_tokens": max_tokens,
            },
        )

        if tools:
            kwargs["tools"] = tools

        if tool_choice:
            kwargs["tool_choice"] = {
                "type": "tool",
                "name": tool_choice,
            }

        # Extract the system message
        if messages[0]["role"] == "system":
            if not isinstance(messages[0]["content"], str):
                raise ValueError(
                    "The content of the system message should be a string, "
                    f"got {type(messages[0]['content'])}",
                )

            kwargs["system"] = messages[0]["content"]
            messages = messages[1:]

        kwargs["messages"] = messages

        # Call the model
        response = self.client.messages.create(**kwargs)

        # Get the response according to the stream
        if stream:

            def generator() -> Generator[str, None, None]:
                # Used in model invocation recording
                gathered_response = {}

                text = ""
                current_block = {}
                for chunk in response:
                    chunk = chunk.model_dump()
                    chunk_type = chunk.get("type", None)

                    if chunk_type == "message_start":
                        gathered_response.update(**chunk["message"])

                    if chunk_type == "message_delta":
                        for key, cost in chunk.get("usage", {}).items():
                            gathered_response["usage"][key] = (
                                gathered_response["usage"].get(key, 0) + cost
                            )

                    if chunk_type == "content_block_start":
                        # Refresh the current block
                        current_block = chunk["content_block"]

                    if chunk_type == "content_block_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("type", None) == "text_delta":
                            # To recover the complete response with multiple
                            # blocks in its content field
                            current_block["text"] = current_block.get(
                                "text",
                                "",
                            ) + delta.get("text", "")
                            # Used for feedback
                            text += delta.get("text", "")
                            yield text

                        # TODO: Support tool calls in streaming mode

                    if chunk_type == "content_block_stop":
                        gathered_response["content"].append(current_block)

                self._save_model_invocation_and_update_monitor(
                    kwargs,
                    gathered_response,
                )

            return ModelResponse(
                stream=generator(),
            )

        else:
            response = response.model_dump()

            # Save the model invocation and update the monitor
            self._save_model_invocation_and_update_monitor(
                kwargs,
                response,
            )

            texts = []
            tool_calls = []
            # Gather text from content blocks
            for block in response.get("content", []):
                typ = block.get("type", None)
                if isinstance(block, dict) and typ == "text":
                    texts.append(block.get("text", ""))
                elif typ == "tool_use":
                    tool_calls.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=block.get("id"),
                            name=block.get("name"),
                            input=block.get("input", {}),
                        ),
                    )

            # Return the response
            return ModelResponse(
                text="\n".join(texts),
                raw=response,
                tool_calls=tool_calls if tool_calls else None,
            )

    def _save_model_invocation_and_update_monitor(
        self,
        kwargs: dict,
        response: dict,
    ) -> None:
        usage = response.get("usage", None)

        if usage is None:
            formatted_usage = None
        else:
            formatted_usage = ChatUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            )

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
        return AnthropicFormatter.format_tools_json_schemas(schemas)
