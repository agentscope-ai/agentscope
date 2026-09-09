# -*- coding: utf-8 -*-
"""The realtime model base class."""
import inspect
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import BaseModel

from ._events import ModelEvent
from ._model_card import RealtimeModelCard
from ..credential import CredentialBase
from ..message import ToolResultBlock


class ModelDisconnectedError(ConnectionError):
    """The provider closed the session; the next user audio reconnects."""


class TruncationSupport(StrEnum):
    """How the provider lets an interrupted turn be corrected. A protocol
    property, constant across the models of one API."""

    NONE = "none"
    SERVER = "server"
    """The provider corrects itself; ``truncate`` is a no-op."""

    EXPLICIT = "explicit"
    """The provider accepts an explicit truncate frame."""


class RealtimeModelBase(ABC):
    """A bidirectional realtime model: audio in, audio and tool calls out.

    Facts live in three places: class attributes for what the adapter
    implements, the model card for what the model is, and properties for
    what is a constant on one provider and a parameter on another.
    """

    class Parameters(BaseModel):
        """Provider-specific tuneables, surfaced to the UI through the
        card's ``parameter_schema``."""

    # ------------------------------------------------------------------
    # Adapter facts — constant across every model of this API
    # ------------------------------------------------------------------

    type: str = ""
    """Identifies the adapter; stamped onto every card it lists so a stored
    config can be mapped back to this class."""

    truncation: TruncationSupport = TruncationSupport.NONE
    """Whether and how an interrupted turn can be corrected."""

    supports_text_input: bool = False
    """Whether a text turn can be injected mid-session."""

    def __init__(
        self,
        model_name: str,
        credential: CredentialBase,
        parameters: "RealtimeModelBase.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the realtime model.

        Args:
            model_name (`str`):
                The model name, e.g. ``"qwen3-omni-flash-realtime"``.
            credential (`CredentialBase`):
                The credential used to authenticate against the provider.
            parameters (`RealtimeModelBase.Parameters | None`, optional):
                Provider-specific parameters; defaults are used if omitted.
            model_card (`RealtimeModelCard | None`, optional):
                The model card. When ``None`` it is looked up by
                *model_name* among the cards shipped with this class, so
                that constructing a model by name alone still yields the
                right sample rates and limits.

        Raises:
            `ValueError`: If no card is given and none matches *model_name*.
        """
        self.model_name = model_name
        self.credential = credential
        self.parameters = parameters or self.Parameters()
        self.card = model_card or self._find_card(model_name)

    @classmethod
    def _find_card(cls, model_name: str) -> RealtimeModelCard:
        """Look up the shipped card for *model_name*."""
        for card in cls.list_models():
            if card.name == model_name:
                return card
        raise ValueError(
            f"No realtime model card found for {model_name!r} in "
            f"{cls.__name__}. Pass `model_card` explicitly for a model "
            f"that ships no card.",
        )

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list[RealtimeModelCard]:
        """List the candidate models of this adapter.

        Args:
            custom_yaml_dir (`str | None`, optional):
                Directory to scan. Defaults to the ``_models`` directory
                next to the concrete subclass's source file.

        Returns:
            `list[RealtimeModelCard]`: The cards found, oldest error
                skipped with a warning.
        """
        yaml_dir = (
            Path(custom_yaml_dir)
            if custom_yaml_dir
            else Path(inspect.getfile(cls)).parent / "_models"
        )
        cards = RealtimeModelCard.list_from_directory(yaml_dir, cls.Parameters)
        for card in cards:
            card.model_type = cls.type
        return cards

    # ------------------------------------------------------------------
    # Facts that are constant on some providers and tuneable on others
    # ------------------------------------------------------------------

    @property
    def input_sample_rate(self) -> int:
        """The PCM rate :meth:`push_audio` expects, in Hz."""
        return self.card.input_sample_rate

    @property
    def output_sample_rate(self) -> int:
        """The PCM rate of the audio in :class:`AudioDelta`, in Hz."""
        return self.card.output_sample_rate

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open the session.

        Args:
            instructions (`str`):
                System instructions.
            tools (`list[dict] | None`, optional):
                Tool JSON schemas. Ignored unless the card declares
                ``supports_tools``.
            **kwargs (`Any`):
                Extra provider fields. ``turn_detection_disabled=True``
                asks the provider not to detect turns because the caller
                runs its own VAD; a resumption handle may also be passed.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the session and release the connection."""

    @abstractmethod
    async def events(self) -> AsyncIterator[ModelEvent]:
        """Yield model events until the session ends."""
        raise NotImplementedError(f"{type(self).__name__}.events")
        yield  # pylint: disable=unreachable

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    @abstractmethod
    async def push_audio(self, pcm: bytes) -> None:
        """Feed user audio: PCM16 mono at :attr:`input_sample_rate`."""

    @abstractmethod
    async def push_text(self, text: str) -> None:
        """Feed a text user turn.

        Raises:
            `NotImplementedError`: If :attr:`supports_text_input` is
                ``False``.
        """

    @abstractmethod
    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Feed the result of a tool the caller executed."""

    @abstractmethod
    async def commit_turn(self) -> None:
        """Declare the user turn finished. Only called when the caller owns
        endpointing, i.e. the session was opened with turn detection off."""

    # ------------------------------------------------------------------
    # Response control
    # ------------------------------------------------------------------

    @abstractmethod
    async def request_response(self) -> None:
        """Ask the model to start generating a reply."""

    @abstractmethod
    async def cancel_response(self) -> None:
        """Stop the active reply without closing the session."""

    @abstractmethod
    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """Rewrite an assistant turn to what the user actually heard. A
        no-op unless :attr:`truncation` is ``EXPLICIT``.

        Args:
            item_id (`str`):
                The interrupted assistant item.
            played_ms (`int`):
                Milliseconds of its audio that reached the speaker.
            played_text (`str`):
                The corresponding prefix of its spoken text.
        """
