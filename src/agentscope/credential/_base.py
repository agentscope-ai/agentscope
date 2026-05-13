# -*- coding: utf-8 -*-
"""The credential base class."""
import uuid
from typing import TYPE_CHECKING, Type

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..model import ChatModelBase, ModelCard


class CredentialBase(BaseModel):
    """The credential base class."""

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="The credential id",
    )

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the :class:`ChatModelBase` subclass that consumes this
        credential. Subclasses must override this method to return the
        corresponding chat model class.

        Returns:
            `Type[ChatModelBase]`:
                The chat model class that uses this credential.
        """
        raise NotImplementedError(
            f"{cls.__name__} must implement ``get_chat_model_class``.",
        )

    def list_models(self) -> list["ModelCard"]:
        """List the candidate chat models that are available under this
        credential. The default implementation delegates to the
        :meth:`ChatModelBase.list_models` of the class returned by
        :meth:`get_chat_model_class`.

        Returns:
            `list[ModelCard]`:
                A list of candidate models described by their model cards.
        """
        return self.get_chat_model_class().list_models()
