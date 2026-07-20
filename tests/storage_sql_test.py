# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""Round-trip and semantic tests for :class:`AsyncSQLAlchemyStorage`.

Runs against an in-memory SQLite database via ``sqlite+aiosqlite``
so the whole suite is self-contained — no external server needed.
The intent is to exercise every ``StorageBase`` method the Redis
backend implements, mirroring the shape (though not always the
literal assertions) of the Redis backend's tests so both backends
stay behavioural equivalents.
"""
from contextlib import AsyncExitStack
from datetime import datetime, timedelta
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import SecretStr

from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    ScheduleData,
    ScheduleRecord,
    SessionConfig,
    SessionSource,
    AsyncSQLAlchemyStorage,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.credential import DashScopeCredential
from agentscope.message import AssistantMsg, UserMsg


def _agent_record(user_id: str, name: str = "agent-x") -> AgentRecord:
    """Build a minimal but complete :class:`AgentRecord`."""
    return AgentRecord(
        user_id=user_id,
        data=AgentData(
            name=name,
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _session_config() -> SessionConfig:
    """Build a minimal :class:`SessionConfig`."""
    return SessionConfig(
        workspace_id="ws-1",
        name="s",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4o",
            parameters={},
        ),
    )


def _kb_record(user_id: str, name: str = "kb") -> KnowledgeBaseRecord:
    """Build a KB record with a default embedding config."""
    return KnowledgeBaseRecord(
        user_id=user_id,
        data=KnowledgeBaseData(
            name=name,
            description="",
            embedding_model_config=EmbeddingModelConfig(
                type="openai_credential",
                credential_id="cred-1",
                model="text-embedding-3-small",
                dimensions=8,
            ),
            collection_name="kb-x",
        ),
    )


def _kd_record(
    user_id: str,
    knowledge_base_id: str,
    filename: str = "f.txt",
) -> KnowledgeDocumentRecord:
    """Build a fresh (``pending`` / unclaimed) document record."""
    return KnowledgeDocumentRecord(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        data=KnowledgeDocumentData(
            filename=filename,
            size=42,
            blob_uri=f"local://{filename}",
        ),
    )


def _schedule_record(user_id: str, agent_id: str) -> ScheduleRecord:
    """Build a schedule record."""
    return ScheduleRecord(
        user_id=user_id,
        agent_id=agent_id,
        data=ScheduleData(
            name="daily",
            cron_expression="0 9 * * *",
            chat_model_config=ChatModelConfig(
                type="openai",
                credential_id="cred-1",
                model="gpt-4o",
                parameters={},
            ),
        ),
    )


class AsyncSQLAlchemyStorageTest(IsolatedAsyncioTestCase):
    """End-to-end tests for :class:`AsyncSQLAlchemyStorage` over
    in-memory SQLite."""

    async def asyncSetUp(self) -> None:
        # ``:memory:`` gives a private DB per connection; using a
        # shared-cache URI would too, but per-test isolation is what
        # we want here.
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            AsyncSQLAlchemyStorage(
                "sqlite+aiosqlite:///:memory:",
                create_tables=True,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    async def test_credentials_round_trip(self) -> None:
        """Upsert / list / get / delete + owner scoping."""
        cred = DashScopeCredential(api_key=SecretStr("sk-1"))
        cid = await self.storage.upsert_credential("user-1", cred)

        listed = await self.storage.list_credentials("user-1")
        self.assertEqual([c.id for c in listed], [cid])
        self.assertEqual(listed[0].data["api_key"], "sk-1")

        fetched = await self.storage.get_credential("user-1", cid)
        self.assertEqual(fetched.id, cid)

        # Cross-user isolation
        self.assertEqual(await self.storage.list_credentials("user-2"), [])
        self.assertIsNone(await self.storage.get_credential("user-2", cid))

        # Delete + double-delete
        self.assertTrue(await self.storage.delete_credential("user-1", cid))
        self.assertFalse(await self.storage.delete_credential("user-1", cid))

    async def test_upsert_credential_is_owner_scoped(self) -> None:
        """A preset id owned by another user is never read or clobbered."""
        import sqlalchemy.exc

        victim = DashScopeCredential(api_key=SecretStr("victim-key"))
        cid = await self.storage.upsert_credential("user-1", victim)
        before = await self.storage.get_credential("user-1", cid)

        # Same owner + preset id → in-place update, created_at preserved.
        rotated = DashScopeCredential(api_key=SecretStr("rotated"), id=cid)
        self.assertEqual(
            await self.storage.upsert_credential("user-1", rotated),
            cid,
        )
        after = await self.storage.get_credential("user-1", cid)
        self.assertEqual(after.data["api_key"], "rotated")
        self.assertEqual(after.created_at, before.created_at)

        # Attacker (user-2) presenting the victim's id must not touch the
        # victim's row: the global-id INSERT collides and raises, and the
        # victim's data + ownership stay intact.
        attack = DashScopeCredential(api_key=SecretStr("attacker"), id=cid)
        with self.assertRaises(sqlalchemy.exc.IntegrityError):
            await self.storage.upsert_credential("user-2", attack)

        victim_now = await self.storage.get_credential("user-1", cid)
        self.assertEqual(victim_now.data["api_key"], "rotated")
        self.assertIsNone(await self.storage.get_credential("user-2", cid))
        self.assertEqual(await self.storage.list_credentials("user-2"), [])

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    async def test_agents_round_trip_and_source_filter(self) -> None:
        """``list_agents`` filters out ``source='team'`` workers."""
        user_agent = _agent_record("user-1", "usr")
        team_agent = _agent_record("user-1", "team-worker")
        team_agent.source = "team"

        await self.storage.upsert_agent("user-1", user_agent)
        await self.storage.upsert_agent("user-1", team_agent)

        listed = await self.storage.list_agents("user-1")
        self.assertEqual([a.id for a in listed], [user_agent.id])
        # But direct get works for the team-spawned worker
        self.assertEqual(
            (await self.storage.get_agent("user-1", team_agent.id)).id,
            team_agent.id,
        )

    async def test_delete_agent_cascades_sessions_and_schedules(self) -> None:
        """Deleting an agent removes its sessions + schedules."""
        agent = _agent_record("user-1")
        await self.storage.upsert_agent("user-1", agent)
        session = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=agent.id,
            config=_session_config(),
        )
        schedule = _schedule_record("user-1", agent.id)
        await self.storage.upsert_schedule("user-1", schedule)

        self.assertTrue(await self.storage.delete_agent("user-1", agent.id))
        self.assertEqual(
            await self.storage.list_sessions("user-1", agent.id),
            [],
        )
        self.assertIsNone(
            await self.storage.get_session(
                "user-1",
                agent.id,
                session.id,
            ),
        )
        self.assertIsNone(
            await self.storage.get_schedule("user-1", schedule.id),
        )

    async def test_upsert_replaces_in_place_and_keeps_created_at(self) -> None:
        """Re-upserting the same id updates via the atomic upsert path.

        Exercises the ``ON CONFLICT DO UPDATE`` branch: the second
        write must overwrite the mutable columns, preserve the original
        ``created_at``, and never create a duplicate row.
        """
        agent = _agent_record("user-1", "v1")
        await self.storage.upsert_agent("user-1", agent)
        first = await self.storage.get_agent("user-1", agent.id)

        agent.data.name = "v2"
        await self.storage.upsert_agent("user-1", agent)
        second = await self.storage.get_agent("user-1", agent.id)

        self.assertEqual(second.data.name, "v2")
        self.assertEqual(second.created_at, first.created_at)
        self.assertGreaterEqual(second.updated_at, first.updated_at)
        # Exactly one row — the conflict updated rather than inserted.
        self.assertEqual(len(await self.storage.list_agents("user-1")), 1)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def test_sessions_upsert_and_state_update(self) -> None:
        """Create + update + state-only update."""
        agent = _agent_record("user-1")
        await self.storage.upsert_agent("user-1", agent)

        # Create
        session = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=agent.id,
            config=_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id="sch-1",
        )
        self.assertEqual(session.source, SessionSource.SCHEDULE)

        # Update (same session_id) — config swap
        new_config = _session_config()
        new_config.name = "renamed"
        updated = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=agent.id,
            config=new_config,
            session_id=session.id,
        )
        self.assertEqual(updated.id, session.id)
        self.assertEqual(updated.config.name, "renamed")
        self.assertEqual(updated.created_at, session.created_at)

        # State-only update
        from agentscope.state import AgentState

        new_state = AgentState()
        await self.storage.update_session_state(
            "user-1",
            agent.id,
            session.id,
            new_state,
        )
        fetched = await self.storage.get_session(
            "user-1",
            agent.id,
            session.id,
        )
        self.assertEqual(fetched.state.model_dump(), new_state.model_dump())

        # By-schedule listing
        listed = await self.storage.list_sessions_by_schedule(
            "user-1",
            "sch-1",
        )
        self.assertEqual([s.id for s in listed], [session.id])

        # set_session_team_id
        await self.storage.set_session_team_id(
            "user-1",
            session.id,
            "team-9",
        )
        fetched = await self.storage.get_session(
            "user-1",
            agent.id,
            session.id,
        )
        self.assertEqual(fetched.team_id, "team-9")

    async def test_update_session_state_missing_raises(self) -> None:
        """Updating an absent session raises :class:`KeyError`."""
        from agentscope.state import AgentState

        with self.assertRaises(KeyError):
            await self.storage.update_session_state(
                "user-1",
                "agent-x",
                "no-such-session",
                AgentState(),
            )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def test_messages_upsert_and_pagination(self) -> None:
        """Same-id upsert replaces; distinct ids append; pagination
        yields chronological order."""
        m1 = UserMsg(name="u", content="hello")
        m2 = AssistantMsg(name="a", content="hi")

        await self.storage.upsert_message("user-1", "sess-1", m1)
        await self.storage.upsert_message("user-1", "sess-1", m2)

        # Replace m1 in place (build a fresh Msg with the same id so
        # Pydantic re-validates the content list rather than accepting
        # a raw-string assignment on the model).
        m1_updated = UserMsg(id=m1.id, name="u", content="hola")
        await self.storage.upsert_message("user-1", "sess-1", m1_updated)

        listed, has_more = await self.storage.list_messages(
            "user-1",
            "sess-1",
        )
        self.assertEqual([m.id for m in listed], [m1.id, m2.id])
        self.assertFalse(has_more)
        self.assertEqual(listed[0].content, m1_updated.content)

        fetched = await self.storage.get_message("user-1", "sess-1", m2.id)
        self.assertEqual(fetched.id, m2.id)

        # Cursor-based pagination: the latest page of one message is the
        # newest (m2), with older messages still available.
        latest, has_more = await self.storage.list_messages(
            "user-1",
            "sess-1",
            limit=1,
        )
        self.assertEqual([m.id for m in latest], [m2.id])
        self.assertTrue(has_more)

        # Walking backwards with ``before`` yields the previous page.
        older, has_more = await self.storage.list_messages(
            "user-1",
            "sess-1",
            limit=1,
            before=m2.id,
        )
        self.assertEqual([m.id for m in older], [m1.id])
        self.assertFalse(has_more)

        # An unknown cursor yields an empty page.
        self.assertEqual(
            await self.storage.list_messages(
                "user-1",
                "sess-1",
                before="does-not-exist",
            ),
            ([], False),
        )

        # The legacy ``offset`` keyword is ignored but warns.
        with self.assertWarns(DeprecationWarning):
            await self.storage.list_messages(
                "user-1",
                "sess-1",
                offset=1,
            )

    async def test_messages_max_width_ids(self) -> None:
        """Composite key stores ids a concatenated key couldn't hold.

        Two 64-char ids would be 129 chars once joined as
        ``session_id:msg_id`` — overflowing the old ``String(96)``
        synthetic primary key.  The composite ``(session_id, msg_id)``
        key round-trips them without truncation.
        """
        long_session = "s" * 64
        long_msg_id = "m" * 64
        msg = UserMsg(id=long_msg_id, name="u", content="hi")
        await self.storage.upsert_message("user-1", long_session, msg)

        fetched = await self.storage.get_message(
            "user-1",
            long_session,
            long_msg_id,
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, long_msg_id)
        listed, _has_more = await self.storage.list_messages(
            "user-1",
            long_session,
        )
        self.assertEqual([m.id for m in listed], [long_msg_id])

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    async def test_teams_upsert_get_list_delete(self) -> None:
        """Team cascade: created-role member is fully deleted, invited-role
        keeps their agent."""
        # Two agents — one that will be "created" for the team,
        # another that will be "invited".
        created_agent = _agent_record("user-1", "created")
        created_agent.source = "team"
        invited_agent = _agent_record("user-1", "invited")

        await self.storage.upsert_agent("user-1", created_agent)
        await self.storage.upsert_agent("user-1", invited_agent)

        # Sessions for both, plus the leader session.
        leader = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=created_agent.id,
            config=_session_config(),
        )
        created_session = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=created_agent.id,
            config=_session_config(),
        )
        invited_session = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=invited_agent.id,
            config=_session_config(),
        )
        surviving_session = await self.storage.upsert_session(
            user_id="user-1",
            agent_id=invited_agent.id,
            config=_session_config(),
        )

        team = TeamRecord(
            user_id="user-1",
            session_id=leader.id,
            data=TeamData(
                name="team",
                members=[
                    TeamMember(
                        owner_id="user-1",
                        agent_id=created_agent.id,
                        session_id=created_session.id,
                        role="created",
                    ),
                    TeamMember(
                        owner_id="user-1",
                        agent_id=invited_agent.id,
                        session_id=invited_session.id,
                        role="invited",
                    ),
                ],
            ),
        )
        await self.storage.upsert_team("user-1", team)

        self.assertEqual(
            [t.id for t in await self.storage.list_teams("user-1")],
            [team.id],
        )

        # Delete team.
        self.assertTrue(await self.storage.delete_team("user-1", team.id))
        self.assertIsNone(await self.storage.get_team("user-1", team.id))

        # Created member: agent + session are gone.
        self.assertIsNone(
            await self.storage.get_agent("user-1", created_agent.id),
        )
        # Invited member: agent survives, only the invited session is gone.
        self.assertIsNotNone(
            await self.storage.get_agent("user-1", invited_agent.id),
        )
        self.assertIsNone(
            await self.storage.get_session(
                "user-1",
                invited_agent.id,
                invited_session.id,
            ),
        )
        self.assertIsNotNone(
            await self.storage.get_session(
                "user-1",
                invited_agent.id,
                surviving_session.id,
            ),
        )

    # ------------------------------------------------------------------
    # Knowledge base + documents + lease CAS
    # ------------------------------------------------------------------

    async def test_kb_and_documents_round_trip(self) -> None:
        """KB CRUD; document CRUD; cascading KB delete."""
        kb = _kb_record("user-1")
        await self.storage.upsert_knowledge_base("user-1", kb)

        self.assertEqual(
            [k.id for k in await self.storage.list_knowledge_bases("user-1")],
            [kb.id],
        )

        doc_a = _kd_record("user-1", kb.id, "a.txt")
        doc_b = _kd_record("user-1", kb.id, "b.txt")
        await self.storage.upsert_knowledge_document("user-1", doc_a)
        await self.storage.upsert_knowledge_document("user-1", doc_b)

        listed = await self.storage.list_knowledge_documents("user-1", kb.id)
        self.assertEqual(
            sorted(d.id for d in listed),
            sorted([doc_a.id, doc_b.id]),
        )

        # Cascade delete removes documents.
        self.assertTrue(
            await self.storage.delete_knowledge_base("user-1", kb.id),
        )
        self.assertEqual(
            await self.storage.list_knowledge_documents("user-1", kb.id),
            [],
        )

    async def test_document_kb_foreign_key_is_enforced(self) -> None:
        """The document→KB FK is live (PRAGMA on): cascade + rejection.

        ``delete_knowledge_base`` no longer deletes documents in Python
        — it relies on ``ON DELETE CASCADE``. This only passes if SQLite
        foreign-key enforcement is actually enabled, so the test doubles
        as a guard that the ``PRAGMA foreign_keys=ON`` wiring works.
        """
        from sqlalchemy.exc import IntegrityError

        kb = _kb_record("user-1")
        await self.storage.upsert_knowledge_base("user-1", kb)
        doc = _kd_record("user-1", kb.id)
        await self.storage.upsert_knowledge_document("user-1", doc)

        # Native cascade: dropping the KB row removes the document row.
        await self.storage.delete_knowledge_base("user-1", kb.id)
        self.assertEqual(
            await self.storage.list_knowledge_documents("user-1", kb.id),
            [],
        )

        # Enforcement: a document pointing at a missing KB is rejected.
        orphan = _kd_record("user-1", "no-such-kb")
        with self.assertRaises(IntegrityError):
            await self.storage.upsert_knowledge_document("user-1", orphan)

    async def test_document_status_and_lease_cas(self) -> None:
        """``update_knowledge_document_status`` + lease CAS semantics."""
        kb = _kb_record("user-1")
        await self.storage.upsert_knowledge_base("user-1", kb)
        doc = _kd_record("user-1", kb.id)
        await self.storage.upsert_knowledge_document("user-1", doc)

        # Status transition + error/chunk_count payload update
        await self.storage.update_knowledge_document_status(
            "user-1",
            kb.id,
            doc.id,
            "error",
            error="boom",
            chunk_count=0,
        )
        fetched = await self.storage.get_knowledge_document(
            "user-1",
            kb.id,
            doc.id,
        )
        self.assertEqual(fetched.status, "error")
        self.assertEqual(fetched.data.error, "boom")

        # Reset to pending for the lease dance.
        await self.storage.update_knowledge_document_status(
            "user-1",
            kb.id,
            doc.id,
            "pending",
        )

        now = datetime.now()
        ttl = timedelta(minutes=5)
        # First worker wins.
        self.assertTrue(
            await self.storage.acquire_knowledge_document_lease(
                "user-1",
                kb.id,
                doc.id,
                "worker-A",
                ttl,
                now,
            ),
        )
        # Second worker loses because the lease is fresh.
        self.assertFalse(
            await self.storage.acquire_knowledge_document_lease(
                "user-1",
                kb.id,
                doc.id,
                "worker-B",
                ttl,
                now,
            ),
        )
        # Renew from the holder works, from a stranger fails.
        self.assertTrue(
            await self.storage.renew_knowledge_document_lease(
                "user-1",
                kb.id,
                doc.id,
                "worker-A",
                ttl,
                now,
            ),
        )
        self.assertFalse(
            await self.storage.renew_knowledge_document_lease(
                "user-1",
                kb.id,
                doc.id,
                "worker-B",
                ttl,
                now,
            ),
        )
        # Release from a stranger is a no-op; from the holder clears it.
        await self.storage.release_knowledge_document_lease(
            "user-1",
            kb.id,
            doc.id,
            "worker-B",
        )
        fetched = await self.storage.get_knowledge_document(
            "user-1",
            kb.id,
            doc.id,
        )
        self.assertEqual(fetched.processing_node, "worker-A")

        await self.storage.release_knowledge_document_lease(
            "user-1",
            kb.id,
            doc.id,
            "worker-A",
        )
        fetched = await self.storage.get_knowledge_document(
            "user-1",
            kb.id,
            doc.id,
        )
        self.assertIsNone(fetched.processing_node)
        self.assertIsNone(fetched.lease_expires_at)

    async def test_expired_lease_and_pending_sweep(self) -> None:
        """``list_..._with_expired_lease`` + ``..._pending_since`` filters."""
        kb = _kb_record("user-1")
        await self.storage.upsert_knowledge_base("user-1", kb)

        # Doc 1: expired lease, non-terminal → should show up
        d1 = _kd_record("user-1", kb.id, "d1.txt")
        await self.storage.upsert_knowledge_document("user-1", d1)
        now = datetime.now()
        await self.storage.acquire_knowledge_document_lease(
            "user-1",
            kb.id,
            d1.id,
            "worker-A",
            timedelta(seconds=1),
            now - timedelta(hours=1),  # ancient
        )

        # Doc 2: still pending, no lease → not caught by expired filter
        d2 = _kd_record("user-1", kb.id, "d2.txt")
        await self.storage.upsert_knowledge_document("user-1", d2)

        # Doc 3: terminal → never returned
        d3 = _kd_record("user-1", kb.id, "d3.txt")
        await self.storage.upsert_knowledge_document("user-1", d3)
        await self.storage.acquire_knowledge_document_lease(
            "user-1",
            kb.id,
            d3.id,
            "worker-B",
            timedelta(seconds=1),
            now - timedelta(hours=1),
        )
        await self.storage.update_knowledge_document_status(
            "user-1",
            kb.id,
            d3.id,
            "ready",
        )

        expired = (
            await self.storage.list_knowledge_documents_with_expired_lease(
                now,
            )
        )
        self.assertEqual([d.id for d in expired], [d1.id])

        pending = await self.storage.list_knowledge_documents_pending_since(
            now + timedelta(minutes=1),
        )
        # d1 and d2 are both still 'pending' (acquiring a lease does
        # not transition status by itself); d3 is 'ready' so excluded.
        self.assertEqual(
            sorted(d.id for d in pending),
            sorted([d1.id, d2.id]),
        )

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    async def test_schedules_round_trip(self) -> None:
        """Basic schedule CRUD + list_all across users."""
        s1 = _schedule_record("user-1", "agent-1")
        s2 = _schedule_record("user-2", "agent-2")
        await self.storage.upsert_schedule("user-1", s1)
        await self.storage.upsert_schedule("user-2", s2)

        self.assertEqual(
            sorted(x.id for x in await self.storage.list_schedules("user-1")),
            [s1.id],
        )
        self.assertEqual(
            sorted(x.id for x in await self.storage.list_all_schedules()),
            sorted([s1.id, s2.id]),
        )

        self.assertTrue(
            await self.storage.delete_schedule("user-1", s1.id),
        )
        self.assertFalse(
            await self.storage.delete_schedule("user-1", s1.id),
        )


class AsyncSQLAlchemyStorageAutoMigrateTest(IsolatedAsyncioTestCase):
    """Boot via ``auto_migrate=True`` and confirm the schema is live.

    Uses a file-backed SQLite database (not ``:memory:``) so the
    Alembic-driven ``upgrade head`` run inside
    :meth:`AsyncSQLAlchemyStorage.__aenter__`
    and the subsequent record-write use the same physical database —
    ``:memory:`` gives a private DB per connection, which would let
    Alembic build tables that the storage's engine can't see.
    """

    async def test_auto_migrate_creates_schema(self) -> None:
        """After ``auto_migrate=True`` the tables exist and CRUD works."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "as.db")
            url = f"sqlite+aiosqlite:///{db_path}"

            async with AsyncSQLAlchemyStorage(
                url,
                auto_migrate=True,
            ) as storage:
                agent = _agent_record("user-1")
                await storage.upsert_agent("user-1", agent)
                fetched = await storage.get_agent("user-1", agent.id)
                self.assertEqual(fetched.id, agent.id)


class LegacyRecordShapeTest(IsolatedAsyncioTestCase):
    """The ``mode='before'`` validators absorb pre-refactor payloads.

    Records written before the KB-``data`` nesting / KD-lifecycle
    promotion refactor must still deserialise, so a database populated
    by an older build keeps round-tripping after an upgrade.
    """

    async def test_knowledge_base_flat_payload_migrates(self) -> None:
        """Flat KB fields fold into :attr:`KnowledgeBaseRecord.data`."""
        record = KnowledgeBaseRecord.model_validate(
            {
                "user_id": "u1",
                "name": "kb",
                "description": "d",
                "embedding_model_config": {
                    "type": "openai_credential",
                    "credential_id": "cred-1",
                    "model": "text-embedding-3-small",
                    "dimensions": 8,
                },
                "collection_name": "c",
            },
        )
        self.assertEqual(record.data.name, "kb")
        self.assertEqual(record.data.collection_name, "c")

    async def test_knowledge_document_lifecycle_fields_lift(self) -> None:
        """Legacy in-``data`` ``status`` / ``lease_expires_at`` lift up."""
        record = KnowledgeDocumentRecord.model_validate(
            {
                "user_id": "u1",
                "knowledge_base_id": "kb1",
                "data": {
                    "filename": "f.txt",
                    "size": 1,
                    "blob_uri": "local://f.txt",
                    "status": "ready",
                    "lease_expires_at": None,
                },
            },
        )
        self.assertEqual(record.status, "ready")
        # The fields moved to the top level and no longer shadow ``data``.
        self.assertNotIn("status", record.data.model_dump())
