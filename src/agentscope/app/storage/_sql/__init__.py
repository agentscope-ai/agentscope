# -*- coding: utf-8 -*-
"""Async SQLAlchemy storage backend.

The only public symbol is :class:`SqlStorage`; every other module in
this package is an implementation detail (tables, mappers, engine
helpers, Alembic scaffolding) named with a leading underscore so
:mod:`agentscope.app.storage` can re-export it without leaking
internals.
"""
from ._storage import SqlStorage

__all__ = ["SqlStorage"]
