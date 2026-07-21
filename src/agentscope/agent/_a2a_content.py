# -*- coding: utf-8 -*-
"""Private A2A content mapping and artifact reduction helpers."""
from __future__ import annotations

import base64

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ..event import (
    AgentEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from ..message import Base64Source, DataBlock, TextBlock, URLSource

if TYPE_CHECKING:
    from a2a.types import Artifact, Part

    from ..message import ContentBlock


def _part_to_block(part: Part) -> ContentBlock:
    """Map one supported A2A Part to an AgentScope content block."""
    kind = part.WhichOneof("content")
    if kind == "text":
        return TextBlock(text=part.text)
    if kind == "raw":
        return DataBlock(
            source=Base64Source(
                data=base64.b64encode(part.raw).decode("ascii"),
                media_type=part.media_type or "application/octet-stream",
            ),
            name=part.filename or None,
        )
    if kind == "url":
        return DataBlock(
            source=URLSource(
                url=part.url,
                media_type=part.media_type or "application/octet-stream",
            ),
            name=part.filename or None,
        )
    raise ValueError(
        "A2AAgent supports text, raw, and URL parts; got unsupported "
        f"{kind or 'empty'} content.",
    )


def _block_to_part(block: ContentBlock, types: Any) -> Part:
    """Map one supported AgentScope block to an A2A Part."""
    if isinstance(block, TextBlock):
        return types.Part(text=block.text)
    if isinstance(block, DataBlock):
        if isinstance(block.source, Base64Source):
            try:
                raw = base64.b64decode(block.source.data, validate=True)
            except ValueError as error:
                raise ValueError(
                    "A2AAgent received invalid base64 input data.",
                ) from error
            return types.Part(
                raw=raw,
                media_type=block.source.media_type,
                filename=block.name or "",
            )
        if isinstance(block.source, URLSource):
            return types.Part(
                url=str(block.source.url),
                media_type=block.source.media_type,
                filename=block.name or "",
            )
    raise ValueError(
        "A2AAgent supports text and data input blocks; got "
        f"{block.type!r}.",
    )


def _block_value(block: ContentBlock) -> tuple[Any, ...]:
    """Return a stable content identity without the local block ID."""
    if isinstance(block, TextBlock):
        return ("text", block.text)
    if isinstance(block.source, Base64Source):
        return (
            "raw",
            block.source.data,
            block.source.media_type,
            block.name,
        )
    return (
        "url",
        str(block.source.url),
        block.source.media_type,
        block.name,
    )


def _can_append(existing: ContentBlock, incoming: ContentBlock) -> bool:
    """Whether an incoming chunk can extend an existing streamed block."""
    return (
        isinstance(existing, TextBlock)
        and isinstance(
            incoming,
            TextBlock,
        )
        or (
            isinstance(existing, DataBlock)
            and isinstance(incoming, DataBlock)
            and isinstance(existing.source, Base64Source)
            and isinstance(incoming.source, Base64Source)
            and existing.source.media_type == incoming.source.media_type
            and existing.name == incoming.name
        )
    )


def _append_block(existing: ContentBlock, incoming: ContentBlock) -> str:
    """Append a chunk and return the event delta representation."""
    if isinstance(existing, TextBlock) and isinstance(incoming, TextBlock):
        existing.text += incoming.text
        return incoming.text
    if (
        isinstance(existing, DataBlock)
        and isinstance(incoming, DataBlock)
        and isinstance(existing.source, Base64Source)
        and isinstance(incoming.source, Base64Source)
    ):
        existing_bytes = base64.b64decode(existing.source.data)
        incoming_bytes = base64.b64decode(incoming.source.data)
        existing.source.data = base64.b64encode(
            existing_bytes + incoming_bytes,
        ).decode("ascii")
        return incoming.source.data
    raise RuntimeError("Internal A2A block append mismatch.")


async def _emit_block(
    block: ContentBlock,
    reply_id: str,
    metadata: dict[str, Any],
    *,
    close: bool,
) -> AsyncGenerator[AgentEvent, None]:
    """Emit AgentScope events for one canonical content block."""
    if isinstance(block, TextBlock):
        yield TextBlockStartEvent(
            reply_id=reply_id,
            block_id=block.id,
            metadata=metadata,
        )
        if block.text:
            yield TextBlockDeltaEvent(
                reply_id=reply_id,
                block_id=block.id,
                delta=block.text,
                metadata=metadata,
            )
        if close:
            yield TextBlockEndEvent(
                reply_id=reply_id,
                block_id=block.id,
                metadata=metadata,
            )
        return

    source = block.source
    event_metadata = dict(metadata)
    event_metadata["a2a"] = dict(metadata.get("a2a", {}))
    event_metadata["a2a"].update(
        {
            "filename": block.name,
            "source_type": source.type,
        },
    )
    if isinstance(source, URLSource):
        event_metadata["a2a"]["url"] = str(source.url)
    yield DataBlockStartEvent(
        reply_id=reply_id,
        block_id=block.id,
        media_type=source.media_type,
        metadata=event_metadata,
    )
    if isinstance(source, Base64Source) and source.data:
        yield DataBlockDeltaEvent(
            reply_id=reply_id,
            block_id=block.id,
            data=source.data,
            media_type=source.media_type,
            metadata=event_metadata,
        )
    if close or isinstance(source, URLSource):
        yield DataBlockEndEvent(
            reply_id=reply_id,
            block_id=block.id,
            metadata=event_metadata,
        )


async def _emit_delta(
    block: ContentBlock,
    delta: str,
    reply_id: str,
    metadata: dict[str, Any],
) -> AsyncGenerator[AgentEvent, None]:
    """Emit an append delta for an already-started block."""
    if not delta:
        return
    if isinstance(block, TextBlock):
        yield TextBlockDeltaEvent(
            reply_id=reply_id,
            block_id=block.id,
            delta=delta,
            metadata=metadata,
        )
    else:
        assert isinstance(block.source, Base64Source)
        yield DataBlockDeltaEvent(
            reply_id=reply_id,
            block_id=block.id,
            data=delta,
            media_type=block.source.media_type,
            metadata=metadata,
        )


def _end_event(
    block: ContentBlock,
    reply_id: str,
    metadata: dict[str, Any],
) -> AgentEvent:
    """Build the appropriate end event for a streamed block."""
    if isinstance(block, TextBlock):
        return TextBlockEndEvent(
            reply_id=reply_id,
            block_id=block.id,
            metadata=metadata,
        )
    return DataBlockEndEvent(
        reply_id=reply_id,
        block_id=block.id,
        metadata=metadata,
    )


@dataclass
class _ArtifactState:
    """Canonical local view of one A2A artifact."""

    blocks: list[ContentBlock] = field(default_factory=list)
    open_block: ContentBlock | None = None
    suppress_events: bool = False
    closed: bool = False


class _ArtifactReducer:
    """Reduce A2A artifact updates into provisional events and final blocks."""

    def __init__(self) -> None:
        self._states: dict[str, _ArtifactState] = {}
        self.artifact_ids: list[str] = []

    @property
    def blocks(self) -> list[ContentBlock]:
        """Return canonical blocks in artifact arrival order."""
        return [
            block
            for artifact_id in self.artifact_ids
            for block in self._states[artifact_id].blocks
        ]

    async def apply(
        self,
        artifact: Artifact,
        reply_id: str,
        metadata: dict[str, Any],
        *,
        append: bool,
        last_chunk: bool,
        snapshot: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Apply one update or Task snapshot."""
        artifact_id = artifact.artifact_id
        if not artifact_id:
            raise RuntimeError("A2A artifacts must have an artifact ID.")
        incoming = [_part_to_block(part) for part in artifact.parts]
        state = self._states.get(artifact_id)
        event_metadata = dict(metadata)
        event_metadata["a2a"] = {
            **metadata.get("a2a", {}),
            "artifact_id": artifact_id,
        }

        if state is None:
            if append and not snapshot:
                raise RuntimeError(
                    "A2A artifact append update referenced an unknown "
                    f"artifact: {artifact_id!r}.",
                )
            state = _ArtifactState(blocks=incoming)
            self._states[artifact_id] = state
            self.artifact_ids.append(artifact_id)
            async for event in self._emit_initial(
                state,
                reply_id,
                event_metadata,
                close=last_chunk,
            ):
                yield event
            return

        if snapshot:
            async for event in self._apply_snapshot(
                state,
                incoming,
                reply_id,
                event_metadata,
                close=last_chunk,
            ):
                yield event
            return

        if not append:
            raise RuntimeError(
                "A2A artifact replacement after output is unsupported: "
                f"{artifact_id!r}.",
            )

        if state.closed:
            raise RuntimeError(
                "A2A artifact received an append update after its last "
                f"chunk: {artifact_id!r}.",
            )
        async for event in self._append(
            state,
            incoming,
            reply_id,
            event_metadata,
            close=last_chunk,
        ):
            yield event

    async def close(
        self,
        reply_id: str,
        metadata: dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Close every still-open provisional block."""
        for state in self._states.values():
            if state.open_block is not None and not state.suppress_events:
                yield _end_event(state.open_block, reply_id, metadata)
                state.open_block = None

    async def reconcile_completed(
        self,
        artifact_ids: set[str],
        reply_id: str,
        metadata: dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Make a completed Task's artifact list canonical."""
        removed_ids = [
            artifact_id
            for artifact_id in self.artifact_ids
            if artifact_id not in artifact_ids
        ]
        for artifact_id in removed_ids:
            state = self._states.pop(artifact_id)
            if state.open_block is not None and not state.suppress_events:
                yield _end_event(state.open_block, reply_id, metadata)
        self.artifact_ids = [
            artifact_id
            for artifact_id in self.artifact_ids
            if artifact_id in artifact_ids
        ]

    async def _emit_initial(
        self,
        state: _ArtifactState,
        reply_id: str,
        metadata: dict[str, Any],
        *,
        close: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Emit a newly observed artifact."""
        for index, block in enumerate(state.blocks):
            is_last = index == len(state.blocks) - 1
            keep_open = (
                is_last
                and not close
                and not isinstance(
                    getattr(block, "source", None),
                    URLSource,
                )
            )
            async for event in _emit_block(
                block,
                reply_id,
                metadata,
                close=not keep_open,
            ):
                yield event
            if keep_open:
                state.open_block = block
        state.closed = close

    async def _append(
        self,
        state: _ArtifactState,
        incoming: list[ContentBlock],
        reply_id: str,
        metadata: dict[str, Any],
        *,
        close: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Append new content to an existing artifact."""
        if not incoming:
            if close and state.open_block is not None:
                if not state.suppress_events:
                    yield _end_event(state.open_block, reply_id, metadata)
                state.open_block = None
            state.closed = close
            return
        first = incoming.pop(0)
        if state.blocks and _can_append(state.blocks[-1], first):
            delta = _append_block(state.blocks[-1], first)
            if not state.suppress_events:
                async for event in _emit_delta(
                    state.blocks[-1],
                    delta,
                    reply_id,
                    metadata,
                ):
                    yield event
        else:
            if state.open_block is not None and not state.suppress_events:
                yield _end_event(state.open_block, reply_id, metadata)
            state.open_block = None
            incoming.insert(0, first)

        if incoming and state.open_block is not None:
            if not state.suppress_events:
                yield _end_event(state.open_block, reply_id, metadata)
            state.open_block = None

        for index, block in enumerate(incoming):
            state.blocks.append(block)
            if not state.suppress_events:
                keep_open = (
                    index == len(incoming) - 1
                    and not close
                    and (
                        isinstance(block, TextBlock)
                        or isinstance(
                            getattr(block, "source", None),
                            Base64Source,
                        )
                    )
                )
                async for event in _emit_block(
                    block,
                    reply_id,
                    metadata,
                    close=not keep_open,
                ):
                    yield event
                if keep_open:
                    state.open_block = block

        if close and state.open_block is not None:
            if not state.suppress_events:
                yield _end_event(state.open_block, reply_id, metadata)
            state.open_block = None
        state.closed = close

    async def _apply_snapshot(
        self,
        state: _ArtifactState,
        incoming: list[ContentBlock],
        reply_id: str,
        metadata: dict[str, Any],
        *,
        close: bool,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Reconcile a canonical Task artifact snapshot."""
        if [_block_value(block) for block in state.blocks] == [
            _block_value(block) for block in incoming
        ]:
            if close and state.open_block is not None:
                yield _end_event(state.open_block, reply_id, metadata)
                state.open_block = None
            state.closed = close or state.closed
            return

        can_extend_last = (
            len(state.blocks) == len(incoming)
            and len(incoming) > 0
            and all(
                _block_value(old) == _block_value(new)
                for old, new in zip(state.blocks[:-1], incoming[:-1])
            )
            and _can_append(state.blocks[-1], incoming[-1])
        )
        if can_extend_last:
            old = state.blocks[-1]
            new = incoming[-1]
            if isinstance(old, TextBlock) and isinstance(new, TextBlock):
                suffix = new.text.removeprefix(old.text)
                is_extension = new.text.startswith(old.text)
            else:
                assert isinstance(old, DataBlock)
                assert isinstance(new, DataBlock)
                assert isinstance(old.source, Base64Source)
                assert isinstance(new.source, Base64Source)
                old_bytes = base64.b64decode(old.source.data)
                new_bytes = base64.b64decode(new.source.data)
                is_extension = new_bytes.startswith(old_bytes)
                suffix = base64.b64encode(
                    new_bytes[len(old_bytes) :],
                ).decode("ascii")
            if is_extension:
                if state.closed and suffix:
                    is_extension = False
            if is_extension:
                state.blocks[-1] = new
                new.id = old.id
                if suffix and not state.suppress_events:
                    async for event in _emit_delta(
                        new,
                        suffix,
                        reply_id,
                        metadata,
                    ):
                        yield event
                if close and state.open_block is not None:
                    yield _end_event(new, reply_id, metadata)
                    state.open_block = None
                state.closed = close
                return

        if state.open_block is not None and not state.suppress_events:
            yield _end_event(state.open_block, reply_id, metadata)
        state.blocks = incoming
        state.open_block = None
        state.suppress_events = True
        state.closed = close


__all__ = [
    "_ArtifactReducer",
    "_block_to_part",
    "_emit_block",
    "_part_to_block",
]
