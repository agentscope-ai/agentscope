# -*- coding: utf-8 -*-
"""Pydantic ``_RecordBase`` ↔ SQLAlchemy row conversion helpers.

Round-trip contract (the "no duplication" invariant enforced here):

- On write (``_from_record``) the record is dumped once with
  ``model_dump(mode="json")``, the envelope keys (``id`` /
  ``created_at`` / ``updated_at``) and every field listed in the
  row class' ``_indexed_fields`` are popped out into the promoted
  columns, and whatever remains is stored verbatim in ``payload``.
- On read (``_to_record``) the promoted columns are merged back into
  the payload dict and the whole thing is fed through
  ``model_validate`` — which, thanks to the ``mode="before"``
  validators on records like :class:`KnowledgeDocumentRecord` /
  :class:`KnowledgeBaseRecord`, also transparently absorbs any
  legacy on-disk shapes.

Both functions are dialect-agnostic — they only touch pydantic and
plain Python dicts.  All dialect specifics live in :mod:`_storage`.
"""
from typing import TYPE_CHECKING, TypeVar

from .._model._base import _RecordBase

if TYPE_CHECKING:
    from ._tables import _JsonRecordMixin


R = TypeVar("R", bound=_RecordBase)

# Envelope fields promoted by :class:`_JsonRecordMixin`; also popped
# from every record dump before it hits ``payload``.
_ENVELOPE_FIELDS = ("id", "created_at", "updated_at")


def _from_record(
    row_cls: "type[_JsonRecordMixin]",
    record: _RecordBase,
) -> "_JsonRecordMixin":
    """Project *record* onto a fresh instance of *row_cls*.

    Args:
        row_cls (`type[_JsonRecordMixin]`):
            The concrete ``*Row`` class to instantiate.  Must expose
            ``_indexed_fields`` naming the promoted columns.
        record (`_RecordBase`):
            The pydantic record to persist.  Its ``id``,
            ``created_at`` and ``updated_at`` are taken as-is (the
            caller is responsible for refreshing ``updated_at``
            before the call, mirroring the Redis storage semantics).

    Returns:
        `_JsonRecordMixin`:
            A row instance whose promoted columns and ``payload``
            column together carry every field of the record with no
            duplication.
    """
    dump = record.model_dump(mode="json")
    for field in _ENVELOPE_FIELDS:
        dump.pop(field, None)
    column_values: dict = {}
    for field in row_cls.get_indexed_fields():
        column_values[field] = dump.pop(field, None)
    return row_cls(
        id=record.id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        payload=dump,
        **column_values,
    )


def _to_record(
    row: "_JsonRecordMixin",
    record_cls: type[R],
) -> R:
    """Reconstruct a record from *row*.

    Merges the promoted columns back into ``payload`` and delegates
    to :meth:`~pydantic.BaseModel.model_validate` so any
    ``mode='before'`` validators on the record (legacy-shape
    migrators, defaults, etc.) still fire.

    Args:
        row (`_JsonRecordMixin`):
            The SQLAlchemy row loaded from the database.
        record_cls (`type[R]`):
            The concrete :class:`_RecordBase` subclass to return.

    Returns:
        `R`:
            The reconstituted record.
    """
    obj: dict = dict(row.payload or {})
    obj["id"] = row.id
    obj["created_at"] = row.created_at
    obj["updated_at"] = row.updated_at
    for field in row.__class__.get_indexed_fields():
        obj[field] = getattr(row, field)
    return record_cls.model_validate(obj)
