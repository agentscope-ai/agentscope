# -*- coding: utf-8 -*-
"""The base class for classifier models."""
from abc import ABC, abstractmethod
from typing import Any, Mapping

from pydantic import BaseModel

from ._question import ClassifierQuestion
from ._response import ClassifierResponse
from ..credential import CredentialBase


class ClassifierModelBase(ABC):
    """Base class for models that return typed probabilistic decisions."""

    class Parameters(BaseModel):
        """Provider-specific classifier parameters."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        parameters: BaseModel | None = None,
    ) -> None:
        """Initialize a classifier model.

        Args:
            credential (`CredentialBase`):
                The credential used to authenticate with the provider.
            model (`str`):
                The classifier model name or alias.
            parameters (`BaseModel | None`, defaults to `None`):
                Provider-specific model parameters.
        """
        self.credential = credential
        self.model = model
        self.parameters = parameters or self.Parameters()

    @abstractmethod
    async def __call__(
        self,
        state: str | dict,
        questions: Mapping[str, ClassifierQuestion],
        **kwargs: Any,
    ) -> ClassifierResponse:
        """Evaluate the named questions against the shared input state.

        Args:
            state (`str | dict`):
                The text or JSON content shared by all questions.
            questions (`Mapping[str, ClassifierQuestion]`):
                The typed questions keyed by name.
            **kwargs (`Any`):
                Provider-specific per-call options.

        Returns:
            `ClassifierResponse`:
                The typed probabilistic answers keyed by question name.
        """
