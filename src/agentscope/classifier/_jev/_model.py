# -*- coding: utf-8 -*-
"""The TypeSafe Jev classifier model implementation."""
from time import perf_counter
from typing import Any, Mapping

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
    ScoreAnswer,
)
from .._usage import ClassifierUsage
from ...credential import TypeSafeCredential


def _to_sdk_question(question: ClassifierQuestion) -> Any:
    """Translate an AgentScope question to a TypeSafe question."""
    from typesafe_sdk import Choice, Noul, Score

    match question:
        case BinaryQuestion():
            # The binary question maps to TypeSafe's Noul primitive
            criteria = question.criteria and question.criteria.model_dump(
                exclude_unset=True,
            )
            return Noul(instructions=question.instructions, criteria=criteria)
        case ChoiceQuestion():
            return Choice(
                instructions=question.instructions,
                criteria=question.criteria,
            )
        case ScoreQuestion():
            return Score(
                instructions=question.instructions,
                criteria=question.criteria,
            )


def _from_sdk_answer(answer: Any) -> ClassifierAnswer:
    """Translate a TypeSafe answer to an AgentScope answer."""
    match answer.type:
        case "noul":
            return BinaryAnswer(probability=answer.noul)
        case "choice":
            return ChoiceAnswer(
                choice=answer.choice,
                confidence=answer.confidence,
                probabilities=dict(answer.probabilities),
            )
        case "score":
            return ScoreAnswer(
                score=answer.score,
                confidence=answer.confidence,
                legend=dict(answer.legend),
                probabilities=dict(answer.probabilities),
            )
    raise ValueError(f"Unsupported TypeSafe answer type: {answer.type!r}.")


class JevClassifierModel(ClassifierModelBase):
    """A classifier backed by TypeSafe's Jev System One API."""

    class Parameters(ClassifierModelBase.Parameters):
        """Provider-specific Jev model parameters."""

        model_config = ConfigDict(extra="forbid")

    def __init__(
        self,
        credential: TypeSafeCredential,
        model: str = "jev-latest",
        parameters: "JevClassifierModel.Parameters | None" = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_delay: float = 0.5,
    ) -> None:
        """Initialize the Jev classifier.

        Args:
            credential (`TypeSafeCredential`):
                The TypeSafe API credential.
            model (`str`, defaults to ``"jev-latest"``):
                The Jev model name or alias.
            parameters (`JevClassifierModel.Parameters | None`):
                Provider-specific model parameters.
            timeout (`float`, defaults to `30.0`):
                Per-request timeout in seconds.
            max_retries (`int`, defaults to `2`):
                Maximum retries after the initial request, done by the SDK.
            retry_delay (`float`, defaults to `0.5`):
                Initial exponential backoff delay in seconds.
        """
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as error:
            raise ImportError(
                "JevClassifierModel requires a compatible optional "
                "'typesafe-sdk' dependency. Install it with "
                "`pip install 'agentscope[classifier-jev]'`.",
            ) from error

        super().__init__(credential, model, parameters)
        self.client = AsyncTypeSafeClient(
            api_key=credential.api_key.get_secret_value(),
            base_url=credential.base_url,
            model=model,
            timeout=timeout,
            retry=RetryPolicy(
                max_retries=max_retries,
                backoff_initial=retry_delay,
            ),
        )

    async def __call__(
        self,
        state: str | dict,
        questions: Mapping[str, ClassifierQuestion],
        **kwargs: Any,
    ) -> ClassifierResponse:
        """Call the TypeSafe System One API and normalize its response.

        Args:
            state (`str | dict`):
                The text or JSON content to classify.
            questions (`Mapping[str, ClassifierQuestion]`):
                The typed questions keyed by name.
            **kwargs (`Any`):
                Per-call TypeSafe options, e.g. ``extra_headers`` and
                ``extra_body``.

        Returns:
            `ClassifierResponse`:
                The typed probabilistic answers keyed by question name.
        """
        start_time = perf_counter()
        response = await self.client.system_one(
            state=state,
            questions={
                name: _to_sdk_question(question)
                for name, question in questions.items()
            },
            model=self.model,
            **kwargs,
        )
        return ClassifierResponse(
            model=response.model,
            content={
                name: _from_sdk_answer(answer)
                for name, answer in response.answers.items()
            },
            usage=ClassifierUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                time=perf_counter() - start_time,
            ),
        )
