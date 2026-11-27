# -*- coding: utf-8 -*-
"""Resumable dispatch of a persisted knowledge-base reindex batch."""

import inspect
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .._bus_ops import enqueue_index_task

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import KnowledgeBaseRecord, StorageBase


def status_update_keywords(storage: "StorageBase") -> set[str]:
    """Read the explicitly declared status-update contract of a backend."""
    try:
        params = inspect.signature(
            storage.update_knowledge_document_status,
        ).parameters
    except (TypeError, ValueError):
        return set()
    return {
        p.name
        for p in params.values()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }


def supports_index_publication(storage: "StorageBase") -> bool:
    """Publishing a replacement requires both fields in the same write."""
    return {"expected_lease_token", "alternate_index"}.issubset(
        status_update_keywords(storage),
    )


async def resume_knowledge_base_reindex(
    storage: "StorageBase",
    message_bus: "MessageBus",
    record: "KnowledgeBaseRecord",
) -> "KnowledgeBaseRecord":
    """Resume dispatch while the caller holds the KB mutation lock.

    Persist each document's batch before enqueueing it. A retry skips
    documents already processed in that batch and re-enqueues pending
    ones, including a write whose original enqueue failed. Keep the
    batch pending until every document has been dispatched.
    """
    if not record.data.reindex_pending:
        return record
    # Without a batch ID there is no dispatch to resume. Legacy writers
    # could only start empty batches, independent of later uploads.
    batch = record.data.reindex_id
    if batch is None or not supports_index_publication(storage):
        record.data.reindex_pending = False
        return await storage.upsert_knowledge_base(record.user_id, record)
    documents = await storage.list_knowledge_documents(
        record.user_id,
        record.id,
    )
    for document in documents:
        if document.data.reindex_id != batch:
            document.status = "pending"
            document.data.pending_since = datetime.now(timezone.utc)
            document.processing_node = None
            document.lease_expires_at = None
            document.data.error = None
            document.data.begin_reindex(batch)
            await storage.upsert_knowledge_document(record.user_id, document)
        if document.status == "pending":
            await enqueue_index_task(
                message_bus,
                user_id=record.user_id,
                knowledge_base_id=record.id,
                document_id=document.id,
            )
    record.data.reindex_pending = False
    return await storage.upsert_knowledge_base(record.user_id, record)
