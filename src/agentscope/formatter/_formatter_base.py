# -*- coding: utf-8 -*-
"""The base class for formatter."""
import collections
import re
from abc import abstractmethod, ABC
from typing import Union, Any, List

from .._utils._common import _save_base64_data
from ..message import Msg, TextBlock, ImageBlock, AudioBlock


class FormatterBase:
    """The base class for formatter."""

    supported_model_regexes: list[str]
    """The supported model regexes"""

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """Assert that the input is a list of Msg objects.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be validated.
        """
        if not isinstance(msgs, list):
            raise TypeError("Input must be a list of Msg objects.")

        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"Expected Msg object, got {type(msg)} instead.",
                )

    @staticmethod
    def convert_tool_result_to_string(
        output: str | List[TextBlock | ImageBlock | AudioBlock],
    ) -> str:
        """Turn the tool result list into a textual output to be compatible
        with the LLM API that doesn't support multimodal data.

        Args:
            output (`str | List[TextBlock | ImageBlock | AudioBlock]`):
                The output of the tool response, including text and multimodal
                data like images and audio.

        Returns:
            `str`:
                A string representation of the tool result, with text blocks
                concatenated and multimodal data represented by file paths
                or URLs.
        """

        if isinstance(output, str):
            return output

        textual_output = []
        for block in output:
            assert isinstance(block, dict) and "type" in block, (
                f"Invalid block: {block}, a TextBlock, ImageBlock, or "
                f"AudioBlock is expected."
            )
            if block["type"] == "text":
                textual_output.append(block["text"])

            elif block["type"] in ["image", "audio", "video"]:
                assert "source" in block, (
                    f"Invalid {block['type']} block: {block}, 'source' key "
                    "is required."
                )
                source = block["source"]
                # Save the image locally and return the file path
                if source["type"] == "url":
                    textual_output.append(
                        f"The returned {block['type']} can be found "
                        f"at: {source['url']}",
                    )

                elif source["type"] == "base64":
                    path_temp_file = _save_base64_data(
                        source["media_type"],
                        source["data"],
                    )
                    textual_output.append(
                        f"The returned {block['type']} can be found "
                        f"at: {path_temp_file}",
                    )

                else:
                    raise ValueError(
                        f"Invalid image source: {block['source']}, "
                        "expected 'url' or 'base64'.",
                    )

            else:
                raise ValueError(
                    f"Unsupported block type: {block['type']}, "
                    "expected 'text', 'image', 'audio', or 'video'.",
                )

        if len(textual_output) == 1:
            return textual_output[0]

        else:
            return "\n".join("- " + _ for _ in textual_output)

    @classmethod
    def is_supported_model(cls, model_name: str) -> bool:
        """Check if the provided model_name is supported by the formatter."""
        for regex in cls.supported_model_regexes:
            if re.match(regex, model_name):
                return True
        return False

    @classmethod
    def format_auto(
        cls,
        msgs: Union[Msg, list[Msg]],
    ) -> list[dict]:
        """This function will decide which format function to use between
        `format_chat` and `format_multi_agent` based on the roles and names in
        the input messages.

        - If only "user" and "assistant" (or "system") roles are present and
         only two names are present, then `format_chat` will be used.
        - If more than two names are involved, then `format_multi_agent` will
         be used.
        """
        names_mapping = collections.defaultdict(list)
        for msg in msgs:
            names_mapping[msg.role].append(msg.name)

        if len(names_mapping["user"]) + len(names_mapping["assistant"]) <= 2:
            return cls.format_chat(msgs)
        else:
            return cls.format_multi_agent(msgs)

    @classmethod
    @abstractmethod
    def format_chat(cls, *args: Any, **kwargs: Any) -> list[dict]:
        """Format the messages in chat scenario, where only one user and
        one assistant are involved."""

    @classmethod
    @abstractmethod
    def format_multi_agent(cls, *args: Any, **kwargs: Any) -> list[dict]:
        """Format the messages in multi-agent scenario, where multiple agent
        are involved."""

    @classmethod
    def _format_chat_for_common_models(
        cls,
        *msgs: Union[Msg, list[Msg], None],
        require_alternative: bool = False,
        require_user_last: bool = False,
    ) -> list[dict]:
        """Format the messages for common LLMs in chat scenario, where only
        user and assistant are involved.

        When `require_alternative` or `require_user_last` is set to `True`, but
        the input messages do not meet the requirement, we will use the
        strategy in `format_multi_agent_for_common_models` instead, which
        gather all messages into one system message(if provided) and one user
        message.

        Args:
            msgs (`Union[Msg, list[Msg], None]`):
                The message(s) to be formatted. The `None` input will be
                ignored.
            require_alternative (`bool`, optional):
                If the model API requires the roles to be "user" and "model"
                alternatively.
            require_user_last (`bool`, optional):
                Whether the user message should be placed at the end. Defaults
                to `False`.
        """
        msgs = cls.check_and_flat_messages(*msgs)

        if require_alternative:
            meet_alternative = True
            for i in range(len(msgs) - 1):
                if msgs[i].role == msgs[i + 1].role:
                    meet_alternative = False
                    break
            if not meet_alternative:
                # If not meet the requirement, we use the multi-agent format
                # instead, which will combine all messages into one.
                return cls.format_multi_agent(msgs)

        if require_user_last:
            if msgs[-1].role != "user":
                return cls.format_multi_agent(msgs)

        formatted_msgs = []
        for msg in msgs:
            formatted_msgs.append(
                {
                    "role": msg.role,
                    "content": msg.get_text_content(),
                },
            )
        return formatted_msgs

    @classmethod
    def _format_multi_agent_for_common_models(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """A common format strategy for chat model, which will format the
        input messages into a system message (if provided) and a user message.

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
            msgs (`Union[Msg, list[Msg], None]`):
                The input arguments to be formatted, where each argument
                should be a `Msg` object, or a list of `Msg` objects. The
                `None` input will be ignored.

        Returns:
            `List[dict]`:
                The formatted messages.
        """
        if len(msgs) == 0:
            raise ValueError(
                "At least one message should be provided. An empty message "
                "list is not allowed.",
            )

        # Parse all information into a list of messages
        input_msgs = cls.check_and_flat_messages(*msgs)

        # record dialog history as a list of strings
        dialogue = []
        sys_prompt = None
        for i, msg in enumerate(input_msgs):
            if i == 0 and msg.role == "system":
                # if system prompt is available, place it at the beginning
                sys_prompt = msg.get_text_content()

            else:
                # Merge all messages into a conversation history prompt
                text_content = msg.get_text_content()
                if text_content is not None:
                    dialogue.append(
                        f"{msg.name}: {msg.get_text_content()}",
                    )

        content_components = []

        # The conversation history is added to the user message if not empty
        if len(dialogue) > 0:
            content_components.extend(["## Conversation History"] + dialogue)

        messages = [
            {
                "role": "user",
                "content": "\n".join(content_components),
            },
        ]

        # Add system prompt at the beginning if provided
        if sys_prompt is not None:
            messages = [{"role": "system", "content": sys_prompt}] + messages

        return messages

    @staticmethod
    def check_and_flat_messages(
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[Msg]:
        """Check the input messages."""
        input_msgs = []
        for _ in msgs:
            if _ is None:
                continue
            if isinstance(_, Msg):
                input_msgs.append(_)
            elif isinstance(_, list) and all(isinstance(__, Msg) for __ in _):
                input_msgs.extend(_)
            else:
                raise TypeError(
                    f"The input should be a Msg object or a list "
                    f"of Msg objects, got {type(_)}.",
                )
        return input_msgs

    @classmethod
    def format_tools_json_schemas(cls, schemas: dict[str, dict]) -> list[dict]:
        """Format the JSON schemas of the tool functions to the format that
        API provider expects."""
        raise NotImplementedError(
            f"The method `format_tools_json_schemas` is not implemented yet "
            f"in {cls.__name__}.",
        )
