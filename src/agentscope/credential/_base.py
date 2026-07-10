# -*- coding: utf-8 -*-
"""The credential base class."""
from typing import TYPE_CHECKING, Type

from pydantic import BaseModel, Field

from .._utils._common import _generate_id

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase, ModelCard
    from ..tts import TTSModelBase
    from ..tts._tts_model_card import TTSModelCard


class CredentialBase(BaseModel):
    """The credential base class."""

    id: str = Field(
        default_factory=_generate_id,
        description="The credential id",
    )

    name: str = Field(
        default="",
        description="User-facing display name for this credential.",
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

    @classmethod
    def get_chat_model_classes(cls) -> list[Type["ChatModelBase"]]:
        """Return all chat model classes supported by this credential.

        Providers that expose multiple API surfaces (e.g. OpenAI Chat
        Completions vs Responses API) should override this to return all
        applicable classes. The default returns a single-element list from
        :meth:`get_chat_model_class`.

        Returns:
            `list[Type[ChatModelBase]]`:
                The chat model classes.
        """
        return [cls.get_chat_model_class()]

    @classmethod
    def get_tts_model_classes(cls) -> list[Type["TTSModelBase"]]:
        """Return the TTS model classes supported by this credential.

        Subclasses that support TTS should override this to return one or
        more :class:`TTSModelBase` subclasses. The default returns an empty
        list (provider does not support TTS).

        Returns:
            `list[Type[TTSModelBase]]`:
                The TTS model classes, or an empty list.
        """
        return []

    @classmethod
    def list_tts_models(cls) -> list["TTSModelCard"]:
        """List the candidate TTS models available under this credential.

        Returns:
            `list[TTSModelCard]`:
                A list of TTS model cards, or empty if TTS is not supported.
        """
        cards: list["TTSModelCard"] = []
        for tts_cls in cls.get_tts_model_classes():
            cards.extend(tts_cls.list_models())
        return cards

    @classmethod
    def list_models(cls) -> list["ModelCard"]:
        """List the candidate chat models that are available under this
        credential. Merges model cards from all classes returned by
        :meth:`get_chat_model_classes`.

        Returns:
            `list[ModelCard]`:
                A list of candidate models described by their model cards.
        """
        cards: list["ModelCard"] = []
        for model_cls in cls.get_chat_model_classes():
            cards.extend(model_cls.list_models())
        return cards

    @classmethod
    def get_embedding_model_class(cls) -> Type["EmbeddingModelBase"] | None:
        """Return the :class:`EmbeddingModelBase` subclass that consumes
        this credential, or ``None`` if this provider does not support
        embedding models.

        Subclasses that have a matching embedding implementation should
        override this method. The default returns ``None``.

        Returns:
            `Type[EmbeddingModelBase] | None`:
                The embedding model class, or ``None``.
        """
        return None
