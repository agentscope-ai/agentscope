# -*- coding: utf-8 -*-
"""Internal utilities for the model module."""
import base64
from typing import Any, Self, TypeAlias

from pydantic import Field

from ._model_response import ChatResponse, FinishedReason
from ._model_usage import ChatUsage
from .._logging import logger
from ..message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    URLSource,
)


class _AccTextBlock(TextBlock):
    """The accumulating counterpart of ``TextBlock``, whose ``text`` field
    holds the incremental fragments until they are joined in ``build``."""

    text: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: TextBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`TextBlock`):
                The delta block to collect.
        """
        self.text.append(block.text)

    def build(self) -> TextBlock:
        """Join the fragments into a plain ``TextBlock``."""
        return TextBlock(**{**self.model_dump(), "text": "".join(self.text)})


class _AccThinkingBlock(ThinkingBlock):
    """The accumulating counterpart of ``ThinkingBlock``, whose ``thinking``
    field holds the incremental fragments until they are joined in
    ``build``."""

    thinking: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: ThinkingBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`ThinkingBlock`):
                The delta block to collect.
        """
        self.thinking.append(block.thinking)
        # Provider-specific extras (e.g. Anthropic's ``signature``) are
        # often only emitted on the closing delta of the block.
        for key, value in (block.model_extra or {}).items():
            if value is not None:
                setattr(self, key, value)

    def build(self) -> ThinkingBlock:
        """Join the fragments into a plain ``ThinkingBlock``."""
        return ThinkingBlock(
            **{**self.model_dump(), "thinking": "".join(self.thinking)},
        )


class _AccToolCallBlock(ToolCallBlock):
    """The accumulating counterpart of ``ToolCallBlock``, whose ``input``
    field holds the incremental JSON fragments until they are joined in
    ``build``."""

    input: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: ToolCallBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`ToolCallBlock`):
                The delta block to collect.
        """
        self.input.append(block.input)
        # Most providers carry ``name`` on the opening delta only, but some
        # send an empty one first and fill the name in afterwards.
        if not self.name and block.name:
            self.name = block.name

    def build(self) -> ToolCallBlock:
        """Join the fragments into a plain ``ToolCallBlock``."""
        return ToolCallBlock(
            **{**self.model_dump(), "input": "".join(self.input)},
        )


class _AccBase64Source(Base64Source):
    """The accumulating counterpart of ``Base64Source`` for audio streams,
    whose ``data`` field holds the raw decoded bytes of each delta.

    Base64 strings cannot be concatenated directly: a delta whose byte
    length is not a multiple of three is padded with ``=``, and everything
    after that padding is silently dropped on decoding. So each delta is
    decoded on arrival and the bytes are encoded once in ``build``.
    """

    data: list[bytes] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, source: Base64Source) -> None:
        """Decode one delta source and collect its raw bytes.

        Args:
            source (`Base64Source`):
                The delta source to collect.
        """
        if source.data:
            self.data.append(base64.b64decode(source.data))

    def build(self) -> Base64Source:
        """Join the raw bytes and base64-encode them once."""
        return Base64Source(
            data=base64.b64encode(b"".join(self.data)).decode("ascii"),
            media_type=self.media_type,
        )


class _AccDataBlock(DataBlock):
    """The accumulating counterpart of ``DataBlock``.

    Only ``audio/*`` is a streamable delta whose concatenation is
    well-defined. Other media types and URL sources are standalone assets,
    so the latest delta replaces the previous one.
    """

    source: (  # type: ignore[assignment]
        _AccBase64Source | Base64Source | URLSource
    )

    def append(self, block: DataBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`DataBlock`):
                The delta block to collect.
        """
        if block.name is not None:
            self.name = block.name

        source = block.source
        if (
            isinstance(self.source, _AccBase64Source)
            and isinstance(source, Base64Source)
            and source.media_type == self.source.media_type
        ):
            self.source.append(source)

        elif isinstance(source, Base64Source) and source.media_type.startswith(
            "audio/",
        ):
            # A new audio stream starts here, either because this is the
            # first delta or because the media type changed mid-stream.
            self.source = _AccBase64Source(
                data=[],
                media_type=source.media_type,
            )
            self.source.append(source)

        else:
            # Standalone asset: the latest delta replaces the previous one
            self.source = source

    def build(self) -> DataBlock:
        """Join the fragments into a plain ``DataBlock``."""
        source = self.source
        return DataBlock(
            **{
                **self.model_dump(),
                "source": (
                    source.build()
                    if isinstance(source, _AccBase64Source)
                    else source
                ),
            },
        )


_AccBlock: TypeAlias = (
    _AccTextBlock | _AccThinkingBlock | _AccToolCallBlock | _AccDataBlock
)


class _StreamAccumulator:
    """Accumulates the streaming ``ChatResponse`` deltas of one model call.

    Each block keeps its deltas in a fragment list that is joined exactly
    once in ``build``, which makes the accumulation O(n) in the total
    payload size. The ``ChatResponse.append_chat_response`` it replaces
    grows the blocks with ``block.field += delta`` instead, which is
    O(n^2) for large payloads (e.g. tool call arguments of 100k+ chars)
    and can block the event loop long enough to drop the connection.
    """

    def __init__(self) -> None:
        """Initialize an empty accumulator."""
        # Keyed by block id, the insertion order is the block order
        self._blocks: dict[str, _AccBlock] = {}

        self.id: str | None = None
        """The id of the latest delta, if any."""

        self.usage: ChatUsage | None = None
        """The usage of the latest delta that reported one, if any."""

        self.finished_reason: FinishedReason = FinishedReason.COMPLETED
        """The finished reason to report in ``build``."""

    def append_chat_response(self, chat_response: ChatResponse) -> Self:
        """Collect one delta chunk in constant time per block.

        Args:
            chat_response (`ChatResponse`):
                The streaming delta chunk to collect.
        """
        for block in chat_response.content:
            acc = self._blocks.get(block.id)
            if acc is not None and acc.type != block.type:
                logger.warning(
                    "Block %s changed its type from %s to %s during "
                    "streaming, dropping the accumulated fragments.",
                    block.id,
                    acc.type,
                    block.type,
                )
                acc = None

            if acc is None:
                # Seed the accumulator with every field of the delta, so
                # that the ones it does not accumulate are carried over.
                fields = block.model_dump()
                if isinstance(block, TextBlock):
                    acc = _AccTextBlock(**{**fields, "text": []})
                elif isinstance(block, ThinkingBlock):
                    acc = _AccThinkingBlock(**{**fields, "thinking": []})
                elif isinstance(block, ToolCallBlock):
                    acc = _AccToolCallBlock(**{**fields, "input": []})
                else:
                    # ``source`` is not emptied here, ``append`` replaces
                    # it with a byte accumulator for audio streams.
                    acc = _AccDataBlock(**fields)
                self._blocks[block.id] = acc

            # The type check above guarantees the accumulator and the
            # delta block are of the same kind.
            acc.append(block)  # type: ignore[arg-type]

        if chat_response.usage:
            self.usage = chat_response.usage

        return self

    def build(self) -> ChatResponse:
        """Join the fragments of every block into a final response."""
        kwargs: dict[str, Any] = {}
        if self.id is not None:
            kwargs["id"] = self.id

        return ChatResponse(
            content=[_.build() for _ in self._blocks.values()],
            is_last=True,
            usage=self.usage,
            finished_reason=self.finished_reason,
            **kwargs,
        )
