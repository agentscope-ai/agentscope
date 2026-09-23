# -*- coding: utf-8 -*-
"""Route each reply to a chat model chosen by a classifier."""
import json
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Sequence, TYPE_CHECKING

from ._base import MiddlewareBase
from .._logging import logger
from ..classifier import ChoiceAnswer, ChoiceQuestion, ClassifierModelBase
from ..event import ReplyStartEvent
from ..message import Msg, SystemMsg, UserMsg
from ..model import ChatModelBase

if TYPE_CHECKING:
    from ..agent import Agent


_DEFAULT_INSTRUCTIONS = (
    "Select the most suitable chat model for responding to the user input."
)


@dataclass
class ChatModelCandidate:
    """A named chat model and the criteria for selecting it."""

    name: str
    """The stable name returned by the classifier."""

    model: ChatModelBase
    """The chat model used when this candidate is selected."""

    description: str
    """When this candidate should be selected."""


class ModelRouterMiddleware(MiddlewareBase):
    """Select the chat model of each reply by classifying the user input.

    The decision is made once per reply and kept when the reply resumes,
    e.g. after a user confirmation. When the routing model fails or picks
    an unknown candidate, the agent keeps its own model.
    """

    def __init__(
        self,
        classifier_model: ClassifierModelBase | ChatModelBase,
        candidates: Sequence[ChatModelCandidate],
        instructions: str = _DEFAULT_INSTRUCTIONS,
    ) -> None:
        """Initialize the model router.

        Args:
            classifier_model (`ClassifierModelBase | ChatModelBase`):
                The classifier or chat model used to select a candidate.
            candidates (`Sequence[ChatModelCandidate]`):
                The uniquely named chat model candidates.
            instructions (`str`):
                The instructions of the routing question.
        """
        names = [candidate.name for candidate in candidates]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate chat model candidates: {names}")

        self.classifier_model = classifier_model
        self._models = {_.name: _.model for _ in candidates}
        criteria = {_.name: _.description for _ in candidates}
        self._question = ChoiceQuestion(
            instructions=instructions,
            criteria=criteria,
        )
        self._prompt = (
            f"{instructions}\n\n"
            "Select exactly one candidate using these criteria:\n"
            f"{json.dumps(criteria, ensure_ascii=False, indent=2)}"
        )
        self._schema = {
            "type": "object",
            "properties": {"choice": {"type": "string", "enum": names}},
            "required": ["choice"],
            "additionalProperties": False,
        }

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Swap ``agent.model`` for the selected candidate during the reply."""
        key = await self.get_middleware_key()
        original_model = agent.model
        # A resumed reply keeps its route, a new one is routed on its start
        routed = agent.state.middle_context.get(key, {})
        agent.model = self._models.get(
            routed.get(agent.state.reply_id),
            original_model,
        )
        try:
            async for event in next_handler(**input_kwargs):
                if isinstance(event, ReplyStartEvent):
                    name = await self._route(
                        self._latest_user_text(input_kwargs["inputs"]),
                    )
                    agent.state.middle_context[key] = {event.reply_id: name}
                    agent.model = self._models.get(name, original_model)
                yield event
        finally:
            agent.model = original_model

    async def _route(self, text: str | None) -> str | None:
        """Return the selected candidate name, `None` to keep the agent's."""
        if not text:
            return None
        try:
            if isinstance(self.classifier_model, ClassifierModelBase):
                res = await self.classifier_model(
                    state=text,
                    questions={"chat_model": self._question},
                )
                answer = res.content["chat_model"]
                name = (
                    answer.choice if isinstance(answer, ChoiceAnswer) else None
                )
            else:
                res = await self.classifier_model.generate_structured_output(
                    messages=[
                        SystemMsg(name="system", content=self._prompt),
                        UserMsg(name="user", content=text),
                    ],
                    structured_model=self._schema,
                )
                name = res.content["choice"]
        except Exception as error:
            logger.warning(
                "Chat model routing failed, keeping the agent's model: %s",
                error,
            )
            return None
        if name not in self._models:
            logger.warning(
                "Routing model selected unknown candidate %r, keeping the "
                "agent's model",
                name,
            )
            return None
        logger.debug("Routed the reply to chat model candidate %r", name)
        return name

    @staticmethod
    def _latest_user_text(inputs: object) -> str | None:
        """The text of the latest user message, if any."""
        msgs = inputs if isinstance(inputs, list) else [inputs]
        for msg in reversed(msgs):
            if isinstance(msg, Msg) and msg.role == "user":
                return msg.get_text_content()
        return None
