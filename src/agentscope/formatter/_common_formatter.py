# -*- coding: utf-8 -*-
"""The formatter for common model."""

from typing import Union

from ..formatter import FormatterBase
from ..message import Msg


class CommonFormatter(FormatterBase):
    """The formatter for common model APIs, e.g. ZhipuAI API, DashScope API,
    etc."""

    @classmethod
    def format_chat(
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
        return cls._format_chat_for_common_models(
            *msgs,
            require_alternative=require_alternative,
            require_user_last=require_user_last,
        )

    @classmethod
    def format_multi_agent(
        cls,
        *msgs: Union[Msg, list[Msg], None],
    ) -> list[dict]:
        """Format the multi-agent messages, where more than two agents are
        involved."""
        return cls._format_multi_agent_for_common_models(*msgs)

    @classmethod
    def is_supported_model(cls, model_name: str) -> bool:
        """Check if the model is supported by the formatter."""
        raise NotImplementedError(
            "The CommonFormatter is used a backend solution for common chat "
            "LLMs, so it does not support the `is_supported_model` method.",
        )
