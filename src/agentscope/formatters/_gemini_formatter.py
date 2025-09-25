# -*- coding: utf-8 -*-
"""The gemini formatter class."""
from typing import Union

from ._formatter_base import FormatterBase
from ..message import Msg


class GeminiFormatter(FormatterBase):
    """The formatter for Gemini model."""

    supported_model_regexes: list[str] = [
        "gemini-.*",
    ]

    @classmethod
    def format_chat(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in chat scenario, where only a user and an
        assistant is involved (maybe with system in the beginning)."""

        msgs = cls.check_and_flat_messages(*msgs)

        formatted_msgs = []
        for msg in msgs:
            if msg is None:
                continue
            if msg.role in ["user", "system"]:
                formatted_msgs.append(
                    {
                        "role": "user",
                        "parts": msg.get_text_content(),
                    },
                )
            elif msg.role == "assistant":
                formatted_msgs.append(
                    {
                        "role": "model",
                        "parts": msg.get_text_content(),
                    },
                )
        return formatted_msgs

    @classmethod
    def format_multi_agent(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the messages in multi-agent scenario, where multiple agents
        are involved.

        Requirements of Gemini generate API:
        1. In Gemini `generate_content` API, the `role` field must be either
        `"user"` or `"model"` (In our test, `"assistant"` also works).
        2. If the role of the last message is "model", the gemini model will
        treat it as a continuation task.

        The above information is updated to 2025/03/14. More information
        about the Gemini `generate_content` API can be found in
        https://googleapis.github.io/python-genai/#

        Based on the above considerations, we decide to combine all messages
        into a single user message. This is a simple and straightforward
        strategy, if you have any better ideas, pull request and
        discussion are welcome in our GitHub repository
        https://github.com/agentscope/agentscope!
        """
        if len(msgs) == 0:
            raise ValueError(
                "At least one message should be provided. An empty message "
                "list is not allowed.",
            )

        input_msgs = cls.check_and_flat_messages(*msgs)

        # record dialog history as a list of strings
        sys_prompt = None
        dialogue = []
        for i, unit in enumerate(input_msgs):
            if i == 0 and unit.role == "system":
                # system prompt
                sys_prompt = unit.get_text_content()
            else:
                # Merge all messages into a conversation history prompt
                text_content = unit.get_text_content()
                if text_content is not None:
                    dialogue.append(
                        f"{unit.name}: {text_content}",
                    )

        prompt_components = []
        if sys_prompt is not None:
            if not sys_prompt.endswith("\n"):
                sys_prompt += "\n"
            prompt_components.append(sys_prompt)

        if len(dialogue) > 0:
            prompt_components.extend(["## Conversation History"] + dialogue)

        user_prompt = "\n".join(prompt_components)

        messages = [
            {
                "role": "user",
                "parts": [
                    user_prompt,
                ],
            },
        ]

        return messages
