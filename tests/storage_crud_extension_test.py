# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""A persisted feature adds one facade and backend metadata."""
import asyncio
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

import pytest

from agentscope.app.storage import RedisStorage, StorageBase
from agentscope.app.storage._model._base import _RecordBase


class NoteRecord(_RecordBase):
    """A test-only feature with an owner and a JSON payload."""

    user_id: str
    text: str


class NoteStorageMixin(StorageBase):
    """The feature's typed facade is defined once for both backends."""

    async def upsert_note(
        self,
        user_id: str,
        record: NoteRecord,
    ) -> NoteRecord:
        """Create or replace an owned note."""
        return await self._upsert_record(NoteRecord, user_id, record)

    async def get_note(self, user_id: str, note_id: str) -> NoteRecord | None:
        """Read one owned note."""
        return await self._get_record(NoteRecord, user_id, note_id)

    async def list_notes(self, user_id: str) -> list[NoteRecord]:
        """Read all notes owned by a user."""
        return await self._list_records(NoteRecord, user_id)

    async def delete_note(self, user_id: str, note_id: str) -> bool:
        """Delete an owned note."""
        return await self._delete_record(NoteRecord, user_id, note_id)


class NoteKeyConfig(RedisStorage.KeyConfig):
    """Use custom key templates rather than a built-in entity's keys."""

    note: str = "custom:{user_id}:note:{note_id}"
    note_index: str = "custom:{user_id}:notes"


class StorageCRUDExtensionTest(IsolatedAsyncioTestCase):
    """New entities share CRUD while retaining owner and storage semantics."""

    async def test_sql(self) -> None:
        """The SQL feature adds a row mapping, with no CRUD implementation."""
        sa = pytest.importorskip("sqlalchemy")
        pytest.importorskip("aiosqlite")
        from sqlalchemy.orm import Mapped, mapped_column

        from agentscope.app.storage import AsyncSQLAlchemyStorage
        from agentscope.app.storage._sql._tables import _JsonRecordMixin

        class NoteRow(_JsonRecordMixin):
            """Keep test tables out of the production metadata."""

            __tablename__ = "extension_test_notes"
            metadata = sa.MetaData()
            user_id: Mapped[str] = mapped_column(sa.String(255))
            text: Mapped[str] = mapped_column(sa.String(255))
            _indexed_fields = ("user_id", "text")

        class NoteSQLStorage(NoteStorageMixin, AsyncSQLAlchemyStorage):
            """Only the row declaration is specific to SQL."""

            _record_rows = {
                **AsyncSQLAlchemyStorage._record_rows,
                NoteRecord: NoteRow,
            }

        with TemporaryDirectory() as directory:
            url = f"sqlite+aiosqlite:///{Path(directory) / 'notes.db'}"
            async with NoteSQLStorage(url) as storage:
                async with storage._engine.begin() as connection:
                    await connection.run_sync(NoteRow.metadata.create_all)
                await self._check_contract(storage)
                original = NoteRecord(id="owned", user_id="alice", text="keep")
                await storage.upsert_note("alice", original)
                with self.assertRaises(PermissionError):
                    await storage.upsert_note(
                        "bob",
                        NoteRecord(id="owned", user_id="bob", text="replace"),
                    )
                self.assertEqual(
                    await storage.get_note("alice", "owned"),
                    original,
                )
                self.assertIsNone(await storage.get_note("bob", "owned"))
                original.text = "updated by owner"
                await storage.upsert_note("alice", original)
                self.assertEqual(
                    await storage.get_note("alice", "owned"),
                    original,
                )

                barrier = asyncio.Barrier(2)

                async def create(record: NoteRecord) -> NoteRecord:
                    await barrier.wait()
                    return await storage.upsert_note(record.user_id, record)

                records = [
                    NoteRecord(id="contended", user_id=owner, text=owner)
                    for owner in ("alice", "bob")
                ]
                results = await asyncio.gather(
                    *(create(record) for record in records),
                    return_exceptions=True,
                )
                self.assertEqual(
                    sum(
                        isinstance(result, PermissionError)
                        for result in results
                    ),
                    1,
                )
                winners = [
                    result
                    for result in results
                    if isinstance(result, NoteRecord)
                ]
                self.assertEqual(len(winners), 1)
                winner = winners[0]
                self.assertEqual(
                    await storage.get_note(winner.user_id, winner.id),
                    winner,
                )
        self.assertNotIn(NoteRecord, AsyncSQLAlchemyStorage._record_rows)

    async def test_redis(self) -> None:
        """The Redis feature adds key metadata, with no CRUD implementation."""
        fakeredis = pytest.importorskip("fakeredis.aioredis")

        class NoteRedisStorage(NoteStorageMixin, RedisStorage):
            """Only the key declaration is specific to Redis."""

            _record_keys = {
                **RedisStorage._record_keys,
                NoteRecord: ("note", "note_index", "note_id"),
            }

        async with fakeredis.FakeRedis(decode_responses=True) as client:
            async with NoteRedisStorage(
                connection_pool=client.connection_pool,
                key_config=NoteKeyConfig(),
                key_ttl=30,
            ) as storage:
                await self._check_contract(storage)
                for owner in ("alice", "bob"):
                    await storage.upsert_note(
                        owner,
                        NoteRecord(id="shared-id", user_id=owner, text=owner),
                    )
                for owner in ("alice", "bob"):
                    own = await storage.get_note(owner, "shared-id")
                    self.assertEqual(own.user_id, owner)
                    self.assertEqual(own.text, owner)
                stale = NoteRecord(user_id="alice", text="expires")
                await storage.upsert_note("alice", stale)
                key = f"custom:alice:note:{stale.id}"
                self.assertGreater(await client.ttl(key), 0)
                self.assertLessEqual(await client.ttl(key), 30)
                self.assertTrue(
                    await client.sismember("custom:alice:notes", stale.id),
                )
                await client.delete(key)
                self.assertIsNone(await storage.get_note("alice", stale.id))
                self.assertNotIn(
                    stale.id,
                    {note.id for note in await storage.list_notes("alice")},
                )
        self.assertNotIn(NoteRecord, RedisStorage._record_keys)

    async def _check_contract(self, storage: NoteStorageMixin) -> None:
        """Run exactly the same owned-record assertions on both backends."""
        self.assertEqual(await storage.list_notes("alice"), [])
        self.assertIsNone(await storage.get_note("alice", "missing"))
        self.assertFalse(await storage.delete_note("alice", "missing"))
        first = NoteRecord(user_id="alice", text="first")
        second = NoteRecord(user_id="alice", text="second")
        other = NoteRecord(user_id="bob", text="private")
        for record in (first, second, other):
            saved = await storage.upsert_note(record.user_id, record)
            self.assertEqual(saved, record)
            self.assertEqual(
                await storage.get_note(record.user_id, record.id),
                saved,
            )
        self.assertEqual(
            {note.id for note in await storage.list_notes("alice")},
            {first.id, second.id},
        )
        self.assertEqual(await storage.list_notes("bob"), [other])
        self.assertIsNone(await storage.get_note("bob", first.id))
        self.assertIsNone(await storage.get_note("alice", other.id))

        replacement = first.model_copy(
            update={
                "text": "changed",
                "created_at": first.created_at + timedelta(days=1),
            },
        )
        await storage.upsert_note("alice", replacement)
        updated = await storage.get_note("alice", first.id)
        self.assertIsInstance(updated, NoteRecord)
        self.assertEqual(updated.text, "changed")
        self.assertEqual(updated.created_at, first.created_at)
        self.assertEqual(replacement.created_at, first.created_at)

        mismatched = NoteRecord(user_id="bob", text="rejected")
        with self.assertRaises(ValueError):
            await storage.upsert_note("alice", mismatched)
        self.assertIsNone(await storage.get_note("alice", mismatched.id))
        self.assertIsNone(await storage.get_note("bob", mismatched.id))
        self.assertFalse(await storage.delete_note("bob", first.id))
        self.assertEqual(await storage.get_note("alice", first.id), updated)
        self.assertTrue(await storage.delete_note("alice", first.id))
        self.assertFalse(await storage.delete_note("alice", first.id))
        self.assertIsNone(await storage.get_note("alice", first.id))
        self.assertEqual(await storage.list_notes("alice"), [second])
        self.assertEqual(await storage.list_notes("bob"), [other])

        class SpecializedNote(NoteRecord):
            """Record subclasses use the facade's declared storage mapping."""

        specialized = SpecializedNote(user_id="alice", text="specialized")
        await storage.upsert_note("alice", specialized)
        fetched = await storage.get_note("alice", specialized.id)
        self.assertEqual(fetched.model_dump(), specialized.model_dump())

    async def test_legacy_backend_needs_no_new_abstract_methods(self) -> None:
        """External backends implementing the old API remain instantiable."""
        primitives = {
            "_upsert_record",
            "_get_record",
            "_list_records",
            "_delete_record",
        }
        self.assertFalse(primitives & StorageBase.__abstractmethods__)

        async def old_method(
            _self: StorageBase,
            *_args: Any,
            **_kwargs: Any,
        ) -> None:
            """Stand in for an existing backend's public methods."""

        legacy_type = type(
            "LegacyStorage",
            (StorageBase,),
            {name: old_method for name in StorageBase.__abstractmethods__},
        )
        storage = legacy_type()  # pylint: disable=abstract-class-instantiated
        record = NoteRecord(user_id="alice", text="unsupported")
        with self.assertRaises(NotImplementedError):
            await storage._upsert_record(NoteRecord, "alice", record)
        with self.assertRaises(NotImplementedError):
            await storage._get_record(NoteRecord, "alice", record.id)
        with self.assertRaises(NotImplementedError):
            await storage._list_records(NoteRecord, "alice")
        with self.assertRaises(NotImplementedError):
            await storage._delete_record(NoteRecord, "alice", record.id)
