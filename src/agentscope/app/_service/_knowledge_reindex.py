# -*- coding: utf-8 -*-
"""Resumable dispatch of a persisted knowledge-base reindex batch."""
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .._bus_ops import enqueue_index_task

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import KnowledgeBaseRecord, StorageBase


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
    documents = await storage.list_knowledge_documents(
        record.user_id,
        record.id,
    )
    for document in documents:
        if document.data.reindex_id != record.data.reindex_id:
            document.status = "pending"
            document.data.pending_since = datetime.now(timezone.utc)
            document.processing_node = None
            document.lease_expires_at = None
            document.data.error = None
            document.data.chunk_count = 0
            document.data.reindex_id = record.data.reindex_id
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
