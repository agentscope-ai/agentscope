# -*- coding: utf-8 -*-
"""Dimension policy advertised by a knowledge base manager.

The policy tells the front-end which embedding-model dimensions are
acceptable when creating a new knowledge base.  It is the *capability*
side of the contract; the server still hard-validates every create
call against the same rules.

Three kinds are modelled:

- ``ANY`` — any positive dimension is acceptable.  The user picks
  freely from the list of supported dimensions on the chosen
  embedding model card.  This is the case for one-collection-per-KB
  isolation strategies.
- ``FIXED`` — the dimension is fixed by the server (e.g. a single
  shared collection sized to a specific dimension).  The front-end
  hides incompatible models / dimensions.
- ``LOCKED_BY_EXISTING`` — semantically identical to ``FIXED`` from
  the front-end's perspective, but communicates *why* the dimension
  is fixed: a previously created knowledge base in the same shared
  collection has pinned it.
"""
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from ....embedding import EmbeddingModelCard


class DimensionPolicyKind(str, Enum):
    """The kind of dimension policy the manager publishes."""

    ANY = "any"
    """Any positive dimension is acceptable.

    Used by isolation strategies that allocate a fresh collection per
    knowledge base (each one sized to its own embedding model)."""

    FIXED = "fixed"
    """A specific dimension is required by the manager configuration.

    Used by isolation strategies that share a single collection across
    knowledge bases — every record must agree on the dimension."""

    LOCKED_BY_EXISTING = "locked_by_existing"
    """A specific dimension was locked in by a previously created
    knowledge base.

    Identical to ``FIXED`` for clients; the distinction lets the UI
    show *why* the dimension is fixed (e.g. "the first KB pinned the
    dimension to 768 — switch isolation strategy to use 1536").
    """


class DimensionPolicy(BaseModel):
    """The dimension policy a manager exposes to the front-end."""

    kind: DimensionPolicyKind = Field(
        description="What constraint applies to the chosen dimension.",
    )
    """The kind of constraint."""

    dimension: int | None = Field(
        default=None,
        description=(
            "The required dimension when ``kind`` is ``FIXED`` or "
            "``LOCKED_BY_EXISTING``.  Always ``None`` for ``ANY``."
        ),
    )
    """The required dimension, or ``None`` when any dimension is fine."""

    @model_validator(mode="after")
    def _enforce_kind_dimension_invariant(self) -> "DimensionPolicy":
        """Reject states like ``ANY + dimension=768`` or ``FIXED + None``.

        Without this guard, downstream code silently produces wrong
        results (``ANY`` ignores a stray dimension) or crashes
        (``FIXED`` with ``None`` makes ``filter_card`` raise
        ``TypeError`` on ``target not in card.supported_dimensions``).
        """
        if self.kind is DimensionPolicyKind.ANY:
            if self.dimension is not None:
                raise ValueError(
                    "DimensionPolicy: kind=ANY requires dimension=None, "
                    f"got dimension={self.dimension!r}.",
                )
        else:
            if self.dimension is None or self.dimension <= 0:
                raise ValueError(
                    f"DimensionPolicy: kind={self.kind.value} requires a "
                    f"positive dimension, got dimension={self.dimension!r}.",
                )
        return self

    def accepts(self, dimensions: int) -> bool:
        """Check whether a candidate dimension satisfies this policy.

        Args:
            dimensions (`int`):
                The candidate output dimension.

        Returns:
            `bool`:
                ``True`` if the dimension is acceptable.
        """
        if self.kind is DimensionPolicyKind.ANY:
            return dimensions > 0
        return dimensions == self.dimension

    def filter_card(
        self,
        card: "EmbeddingModelCard",
    ) -> "EmbeddingModelCard | None":
        """Project an embedding model card through this policy.

        Used by the knowledge base router to pre-filter the embedding
        model catalogue exposed at KB-creation time:

        - ``ANY`` returns the card unchanged.
        - ``FIXED`` / ``LOCKED_BY_EXISTING``:

          - fixed-dim cards (``supported_dimensions is None``) are
            kept iff their default ``dimensions`` matches the locked
            value;
          - matryoshka cards are kept iff the locked dimension is in
            ``supported_dimensions``, and a copy is returned with
            ``supported_dimensions`` narrowed to the locked value and
            ``dimensions`` set accordingly.  This guarantees the
            front-end cannot pick an incompatible dimension.

        Args:
            card (`EmbeddingModelCard`):
                The candidate embedding model card.

        Returns:
            `EmbeddingModelCard | None`:
                The (possibly narrowed) card, or ``None`` if the card
                cannot satisfy this policy.
        """
        if self.kind is DimensionPolicyKind.ANY:
            return card
        target = self.dimension
        if card.supported_dimensions is None:
            return card if card.dimensions == target else None
        if target not in card.supported_dimensions:
            return None
        return card.model_copy(
            update={
                "dimensions": target,
                "supported_dimensions": [target],
            },
        )
