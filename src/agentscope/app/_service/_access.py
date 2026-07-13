# -*- coding: utf-8 -*-
"""Resource access service — cross-owner reads with viewer-relative views."""
from __future__ import annotations

from typing import Any, Literal, TypeVar, overload

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from ..access import (
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from ..storage import (
    AgentRecord,
    CredentialRecord,
    KnowledgeBaseRecord,
    StorageBase,
)


# ---------------------------------------------------------------------
# Views — viewer-relative projections of storage records.
# ---------------------------------------------------------------------


class AgentView(AgentRecord):
    """Agent record + viewer-relative ``editable``.

    Subclasses :class:`AgentRecord` so the wire format is a strict
    superset of the historical response (one extra top-level field);
    old clients ignore ``editable`` transparently.
    """

    editable: bool = Field(
        description=(
            "Whether the current viewer may PATCH/DELETE this agent."
        ),
    )


class CredentialView(CredentialRecord):
    """Credential record + viewer-relative ``editable``.

    ``data`` inherits :class:`CredentialRecord.data` in shape, but the
    service masks it for shared credentials before constructing the
    view: only ``type`` and ``name`` survive. Runtime paths that need
    the raw payload call :meth:`ResourceAccessService.resolve_credential`
    instead, which returns the underlying record.
    """

    editable: bool = Field(
        description=(
            "Whether the current viewer may PATCH/DELETE this credential."
        ),
    )


class KnowledgeBaseView(KnowledgeBaseRecord):
    """Knowledge base record + viewer-relative ``editable``.

    Subclasses :class:`KnowledgeBaseRecord` so the wire format is a
    strict superset of the record (one extra top-level field), matching
    :class:`AgentView` and :class:`CredentialView`.
    """

    editable: bool = Field(
        description=(
            "Whether the current viewer may modify this knowledge base."
        ),
    )


# ---------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------


_T = TypeVar("_T", bound=BaseModel)


class ResourceAccessService:
    """Resolve resources visible to a viewer.

    Combines owner-scoped storage reads with cross-owner refs from
    :class:`ResourceAccessPolicyBase`. Routers use it for list/get and
    edit checks; runtime services use it to resolve shared credentials,
    agents, and knowledge bases without depending on FastAPI dependencies.
    """

    def __init__(
        self,
        storage: StorageBase,
        policy: ResourceAccessPolicyBase,
    ) -> None:
        """Initialize the service.

        Args:
            storage: Owner-scoped storage backend.
            policy: Cross-owner access policy.
        """
        self._storage = storage
        self._policy = policy

    # ------------------------------------------------------------------
    # list_resource
    # ------------------------------------------------------------------

    @overload
    async def list_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.CREDENTIAL],
    ) -> list[CredentialView]:
        ...

    @overload
    async def list_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.AGENT],
    ) -> list[AgentView]:
        ...

    @overload
    async def list_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.KNOWLEDGE_BASE],
    ) -> list[KnowledgeBaseView]:
        ...

    async def list_resource(
        self,
        viewer_id: str,
        kind: ResourceKind,
    ) -> list[BaseModel]:
        """List resources of ``kind`` visible to ``viewer_id``.

        The result merges the viewer's own records (always ``editable``)
        with any cross-owner records granted by
        :class:`ResourceAccessPolicyBase`. Credentials shared to the
        viewer have their ``data`` payload masked in the view;
        :meth:`resolve_credential` should be used when the raw payload
        is required for runtime provider calls.
        """
        if kind is ResourceKind.CREDENTIAL:
            own = await self._storage.list_credentials(viewer_id)
        elif kind is ResourceKind.AGENT:
            own = [
                record
                for record in await self._storage.list_agents(viewer_id)
                if record.source != "team"
            ]
        else:
            own = await self._storage.list_knowledge_bases(viewer_id)

        views: list[BaseModel] = [
            self._build_view(record, viewer_id, True) for record in own
        ]
        seen = {(record.user_id, record.id) for record in own}

        for ref in await self._list_refs(viewer_id, kind):
            key = (ref.owner_id, ref.resource_id)
            if key in seen:
                continue
            record = await self._get_owned(
                kind,
                ref.owner_id,
                ref.resource_id,
            )
            if record is None:
                continue
            # Team workers are not shareable; owner reads still see them
            # via ``resolve_agent`` (used by ChatService).
            if isinstance(record, AgentRecord) and record.source == "team":
                continue
            views.append(
                self._build_view(
                    record,
                    viewer_id,
                    ref.permission == ResourcePermission.EDIT,
                ),
            )
            seen.add(key)
        return views

    # ------------------------------------------------------------------
    # get_resource
    # ------------------------------------------------------------------

    @overload
    async def get_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.CREDENTIAL],
        resource_id: str,
    ) -> CredentialView:
        ...

    @overload
    async def get_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.AGENT],
        resource_id: str,
    ) -> AgentView:
        ...

    @overload
    async def get_resource(
        self,
        viewer_id: str,
        kind: Literal[ResourceKind.KNOWLEDGE_BASE],
        resource_id: str,
    ) -> KnowledgeBaseView:
        ...

    async def get_resource(
        self,
        viewer_id: str,
        kind: ResourceKind,
        resource_id: str,
    ) -> BaseModel:
        """Get a visible resource by id or raise ``404``.

        Returns the viewer-relative view so the caller does not have to
        query editability separately (see the KB detail-page case). For
        the viewer's own resource the view is derived directly from
        storage; for a shared resource the policy ref supplies the
        ``permission`` used to compute ``editable``. Credential ``data``
        is masked for shared entries — use :meth:`resolve_credential`
        for runtime provider calls.
        """
        own = await self._get_owned(kind, viewer_id, resource_id)
        if own is not None:
            # Owner-side reads bypass the ``team`` filter on purpose:
            # runtime paths legitimately load the owner's ``source ==
            # "team"`` agents, and we want a single call site.
            return self._build_view(own, viewer_id, True)

        for ref in await self._list_refs(viewer_id, kind):
            if ref.resource_id != resource_id:
                continue
            record = await self._get_owned(
                kind,
                ref.owner_id,
                ref.resource_id,
            )
            if record is None:
                continue
            if isinstance(record, AgentRecord) and record.source == "team":
                continue
            return self._build_view(
                record,
                viewer_id,
                ref.permission == ResourcePermission.EDIT,
            )
        raise self._not_found(kind, resource_id)

    # ------------------------------------------------------------------
    # Runtime-only helpers
    # ------------------------------------------------------------------

    async def resolve_credential(
        self,
        viewer_id: str,
        credential_id: str,
    ) -> CredentialRecord:
        """Resolve a credential for runtime use — returns the raw record.

        Unlike :meth:`get_resource`, the returned record is NOT masked:
        the caller is expected to feed the ``data`` payload into a
        provider client. Restrict use to trusted runtime paths
        (chat / embedding / TTS model construction).
        """
        record = await self._storage.get_credential(viewer_id, credential_id)
        if record is not None:
            return record

        for ref in await self._list_refs(viewer_id, ResourceKind.CREDENTIAL):
            if ref.resource_id != credential_id:
                continue
            record = await self._storage.get_credential(
                ref.owner_id,
                ref.resource_id,
            )
            if record is not None:
                return record
        raise self._not_found(ResourceKind.CREDENTIAL, credential_id)

    async def resolve_agent(
        self,
        viewer_id: str,
        agent_id: str,
    ) -> AgentRecord:
        """Resolve an agent for runtime use — returns the raw record.

        Owner reads include team workers (needed by ChatService when
        running a worker session); cross-owner refs only resolve
        ``source == "user"`` agents since team workers are not
        shareable.
        """
        record = await self._storage.get_agent(viewer_id, agent_id)
        if record is not None:
            return record

        for ref in await self._list_refs(viewer_id, ResourceKind.AGENT):
            if ref.resource_id != agent_id:
                continue
            record = await self._storage.get_agent(
                ref.owner_id,
                ref.resource_id,
            )
            if record is not None and record.source != "team":
                return record
        raise self._not_found(ResourceKind.AGENT, agent_id)

    async def resolve_knowledge_base(
        self,
        viewer_id: str,
        knowledge_base_id: str,
    ) -> KnowledgeBaseRecord:
        """Resolve a knowledge base for runtime use — returns the raw
        record. Used by the RAG middleware and the KB service to look
        up the vector-store collection tied to a shared KB."""
        record = await self._storage.get_knowledge_base(
            viewer_id,
            knowledge_base_id,
        )
        if record is not None:
            return record

        for ref in await self._list_refs(
            viewer_id,
            ResourceKind.KNOWLEDGE_BASE,
        ):
            if ref.resource_id != knowledge_base_id:
                continue
            record = await self._storage.get_knowledge_base(
                ref.owner_id,
                ref.resource_id,
            )
            if record is not None:
                return record
        raise self._not_found(
            ResourceKind.KNOWLEDGE_BASE,
            knowledge_base_id,
        )

    # ------------------------------------------------------------------
    # Edit resolution
    # ------------------------------------------------------------------

    async def resolve_for_edit(
        self,
        viewer_id: str,
        kind: ResourceKind,
        resource_id: str,
    ) -> tuple[str, Any]:
        """Resolve ``(owner_id, record)`` for a mutation.

        Routers use this before PATCH/DELETE to keep the resource_id
        aware of shared editors: an owner writes under their own
        user_id, but a shared editor writes back through the owning
        user's storage key. The returned ``record`` is the raw storage
        record (not a masked view) so callers can inspect and modify
        it before persisting.

        Raises:
            HTTPException:
                * 404 if the resource is not visible to ``viewer_id``
                  (matches the historical owner-only 404).
                * 403 if the resource is visible but ``viewer_id`` only
                  has ``READ`` permission on it.
        """
        own = await self._get_owned(kind, viewer_id, resource_id)
        if own is not None:
            return viewer_id, own

        for ref in await self._list_refs(viewer_id, kind):
            if ref.resource_id != resource_id:
                continue
            record = await self._get_owned(
                kind,
                ref.owner_id,
                ref.resource_id,
            )
            if record is None:
                continue
            if isinstance(record, AgentRecord) and record.source == "team":
                continue
            if ref.permission != ResourcePermission.EDIT:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"{kind.value.replace('_', ' ').title()} "
                        f"'{resource_id}' is read-only for this viewer."
                    ),
                )
            return ref.owner_id, record
        raise self._not_found(kind, resource_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_owned(
        self,
        kind: ResourceKind,
        owner_id: str,
        resource_id: str,
    ) -> Any:
        """Owner-scoped read on the storage backend, dispatched by kind."""
        if kind is ResourceKind.CREDENTIAL:
            return await self._storage.get_credential(owner_id, resource_id)
        if kind is ResourceKind.AGENT:
            return await self._storage.get_agent(owner_id, resource_id)
        return await self._storage.get_knowledge_base(owner_id, resource_id)

    def _build_view(
        self,
        record: Any,
        viewer_id: str,
        editable: bool,
    ) -> BaseModel:
        """Project a raw storage record onto a viewer-relative view.

        Credential ``data`` is masked when the viewer is not the owner
        so the wire format never leaks the shared secret; other kinds
        pass through unchanged apart from the added ``editable`` flag.
        """
        if isinstance(record, CredentialRecord):
            payload = record.model_dump()
            if record.user_id != viewer_id:
                payload["data"] = {
                    k: payload["data"][k]
                    for k in ("type", "name")
                    if k in payload["data"]
                }
            payload["editable"] = editable
            return CredentialView.model_validate(payload)
        if isinstance(record, AgentRecord):
            return AgentView.model_validate(
                {**record.model_dump(), "editable": editable},
            )
        return KnowledgeBaseView.model_validate(
            {**record.model_dump(), "editable": editable},
        )

    async def _list_refs(
        self,
        viewer_id: str,
        kind: ResourceKind,
    ) -> list[ResourceRef]:
        """Return policy refs for ``kind`` and drop mismatched refs."""
        refs = await self._policy.list_accessible(
            viewer_id,
            kind,
            self._storage,
        )
        return [ref for ref in refs if ref.kind == kind]

    @staticmethod
    def _not_found(kind: ResourceKind, resource_id: str) -> HTTPException:
        """Build a consistent not-found exception for visible resources."""
        label = kind.value.replace("_", " ")
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label.title()} '{resource_id}' not found.",
        )
