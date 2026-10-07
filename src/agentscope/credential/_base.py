# -*- coding: utf-8 -*-
"""The credential base class."""
from typing import TYPE_CHECKING, Any, Type

from pydantic import BaseModel, Field

from .._utils._common import _generate_id

if TYPE_CHECKING:
    from ..embedding import EmbeddingModelBase
    from ..model import ChatModelBase, ModelCard
    from ..realtime import RealtimeModelBase, RealtimeModelCard
    from ..tts import TTSModelBase
    from ..tts._tts_model_card import TTSModelCard


async def _http_get_json(url: str, headers: dict[str, str]) -> Any:
    """Perform a GET request and return the parsed JSON body.

    Single network seam for the remote-model listing helpers below, so
    tests can patch network access in one place.

    Args:
        url (`str`): The fully qualified URL to fetch.
        headers (`dict[str, str]`): Request headers.

    Returns:
        `Any`: The decoded JSON response body.

    Raises:
        Exception: Network or HTTP errors propagate to the caller.
    """
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def _list_openai_compatible_models(
    base_url: str,
    api_key: str,
) -> list[str]:
    """Query an OpenAI-compatible endpoint's ``GET /models`` listing.

    Args:
        base_url (`str`): The endpoint's OpenAI-compatible base URL.
        api_key (`str`): The bearer token sent as the API key.

    Returns:
        `list[str]`: Sorted, de-duplicated model IDs reported by the
        endpoint.
    """
    payload = await _http_get_json(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted(
        {
            entry["id"]
            for entry in entries
            if isinstance(entry, dict) and "id" in entry
        },
    )


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
    def get_realtime_model_classes(
        cls,
    ) -> list[Type["RealtimeModelBase"]]:
        """Return the realtime model classes supported by this credential.
        The default returns an empty list (provider does not support
        realtime)."""
        return []

    @classmethod
    def list_realtime_models(cls) -> list["RealtimeModelCard"]:
        """List the candidate realtime models under this credential."""
        cards: list["RealtimeModelCard"] = []
        for rt_cls in cls.get_realtime_model_classes():
            cards.extend(rt_cls.list_models())
        return cards

    @classmethod
    def list_models(cls) -> list["ModelCard"]:
        """List the candidate chat models that are available under this
        credential. The default implementation delegates to the
        :meth:`ChatModelBase.list_models` of the class returned by
        :meth:`get_chat_model_class`.

        Returns:
            `list[ModelCard]`:
                A list of candidate models described by their model cards.
        """
        return cls.get_chat_model_class().list_models()

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

    async def list_remote_models(self) -> list[str] | None:
        """Query the endpoint behind this credential for the models it
        actually serves.

        The service layer surfaces the result next to the static
        model-card catalog (:meth:`list_models`) so users can spot
        catalog entries the configured endpoint does not serve, and
        endpoint models missing from the catalog. The default returns
        ``None`` — this credential type has no supported remote listing
        (e.g. the provider SDK exposes no model-list API).

        Returns:
            `list[str] | None`:
                Sorted, de-duplicated raw model IDs reported by the
                remote endpoint, or ``None`` when unsupported for this
                credential type.

        Raises:
            Exception:
                Network or HTTP errors propagate to the caller; the
                service layer maps them to an upstream error response.
        """
        return None
