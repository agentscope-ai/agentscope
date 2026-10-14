# -*- coding: utf-8 -*-
"""The OpenAI Decisions API classifier implementation."""
import json
from time import perf_counter
from typing import Any, Mapping

import openai
from pydantic import ConfigDict

from .._base import ClassifierModelBase
from .._question import (
    BinaryQuestion,
    ChoiceQuestion,
    ClassifierQuestion,
    ScoreQuestion,
)
from .._response import (
    BinaryAnswer,
    ChoiceAnswer,
    ClassifierAnswer,
    ClassifierResponse,
    RefusalAnswer,
    ScoreAnswer,
)
from .._usage import ClassifierUsage
from ...credential import OpenAICredential
from ...message import Base64Source, DataBlock, TextBlock


def _to_sdk_question(name: str, question: ClassifierQuestion) -> dict:
    """Translate a named classifier question into a Decisions question."""
    instructions = question.instructions or name
    match question:
        case BinaryQuestion():
            if question.criteria is not None:
                for outcome, description in question.criteria.model_dump(
                    exclude_none=True,
                ).items():
                    instructions += f"\n{outcome.capitalize()}: {description}"
            return {
                "type": "predicate",
                "name": name,
                "instructions": instructions,
            }
        case ChoiceQuestion():
            return {
                "type": "choice",
                "name": name,
                "instructions": instructions,
                "choices": [
                    {"value": value, **({"description": desc} if desc else {})}
                    for value, desc in question.criteria.items()
                ],
            }
        case ScoreQuestion():
            return {
                "type": "score",
                "name": name,
                "instructions": instructions,
                "levels": [{"label": label} for label in question.criteria],
            }
    raise TypeError(f"Unsupported classifier question: {type(question)}.")


def _from_sdk_answer(answer: Any) -> ClassifierAnswer:
    """Translate a Decisions answer without changing its probabilities."""
    match answer.type:
        case "predicate":
            return BinaryAnswer(probability=answer.probability)
        case "choice":
            return ChoiceAnswer(
                choice=answer.choice,
                confidence=answer.confidence,
                probabilities={
                    p.value: p.probability for p in answer.probabilities
                },
            )
        case "score":
            return ScoreAnswer(
                score=answer.score,
                confidence=answer.confidence,
                legend={p.value: p.label for p in answer.probabilities},
                probabilities={
                    p.value: p.probability for p in answer.probabilities
                },
            )
        case "refusal":
            return RefusalAnswer()
    raise ValueError(f"Unsupported OpenAI answer type: {answer.type!r}.")


def _to_sdk_input(state: str | dict | list[TextBlock | DataBlock]) -> Any:
    """Convert text, JSON, or ordered text/image blocks to Decisions input."""
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return json.dumps(state, ensure_ascii=False)

    content = []
    for block in state:
        if isinstance(block, TextBlock):
            content.append({"type": "input_text", "text": block.text})
        elif (
            isinstance(block, DataBlock)
            and isinstance(block.source, Base64Source)
            and block.source.media_type.startswith("image/")
        ):
            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        f"data:{block.source.media_type};base64,"
                        f"{block.source.data}"
                    ),
                },
            )
        else:
            raise ValueError(
                "Decisions input supports TextBlock and image DataBlock "
                "with Base64Source only.",
            )
    return [{"role": "user", "content": content}]


class OpenAIClassifierModel(ClassifierModelBase):
    """A classifier backed by the OpenAI Decisions API.

    Requires ``openai>=3.26.0``. Binary questions map to predicates; choice
    and score questions retain their alternatives and ordered levels.
    When instructions are omitted, the question name is used instead.
    Binary criteria are appended to the instructions as true/false outcomes.

    Refusals are returned as :class:`RefusalAnswer` for the affected question,
    alongside any other answers from the same request.
    """

    class Parameters(ClassifierModelBase.Parameters):
        """Provider-specific classifier parameters."""

        model_config = ConfigDict(extra="forbid")

    def __init__(
        self,
        credential: OpenAICredential,
        model: str = "gpt-6-luna",
        parameters: "OpenAIClassifierModel.Parameters | None" = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """Initialize the classifier and its reusable OpenAI client.

        Args:
            credential (`OpenAICredential`):
                API key, optional organization and custom endpoint.
            model (`str`, defaults to ``"gpt-6-luna"``):
                The model name accepted by the Decisions endpoint.
            parameters (`OpenAIClassifierModel.Parameters | None`):
                Provider-specific classifier parameters.
            timeout (`float`, defaults to `30.0`):
                Per-request timeout in seconds.
            max_retries (`int`, defaults to `2`):
                Maximum retries after the initial request, handled by the SDK.
        """
        super().__init__(credential, model, parameters)
        self.client = openai.AsyncOpenAI(
            api_key=credential.api_key.get_secret_value(),
            organization=credential.organization,
            base_url=credential.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        if not hasattr(self.client, "decisions"):
            raise ImportError(
                "OpenAIClassifierModel requires openai>=3.26.0. "
                "Upgrade with `pip install 'openai>=3.26.0'`.",
            )

    async def __call__(
        self,
        state: str | dict | list[TextBlock | DataBlock],
        questions: Mapping[str, ClassifierQuestion],
        **kwargs: Any,
    ) -> ClassifierResponse:
        """Evaluate questions about text, JSON, or inline images.

        Args:
            state (`str | dict | list[TextBlock | DataBlock]`):
                Shared evidence. Dictionaries are serialized as JSON text.
                Content lists preserve order and support text plus image
                blocks with :class:`Base64Source`. URL sources and other
                media types are not supported.
            questions (`Mapping[str, ClassifierQuestion]`):
                Typed questions keyed by their names.
            **kwargs (`Any`):
                Options forwarded to ``client.decisions.create``, such as
                ``safety_identifier``, ``extra_headers``, and ``extra_body``.

        Returns:
            `ClassifierResponse`:
                Answers and refusals keyed by question name, with usage.
        """
        start_time = perf_counter()
        response = await self.client.decisions.create(
            model=self.model,
            input=_to_sdk_input(state),
            questions=[
                _to_sdk_question(name, q) for name, q in questions.items()
            ],
            **kwargs,
        )
        return ClassifierResponse(
            model=response.model,
            content={a.name: _from_sdk_answer(a) for a in response.answers},
            usage=ClassifierUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=perf_counter() - start_time,
            ),
        )
