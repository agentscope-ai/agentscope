# -*- coding: utf-8 -*-
"""Resource access policy primitives for cross-owner resource reads.

This module defines the extension point used by the app service layer to
decide whether a viewer can access resources owned by another user. The
default policy denies all cross-owner access, preserving the historical
owner-isolated behavior.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, Field

from ..storage import StorageBase


class ResourceKind(StrEnum):
    """Resource kinds that can be resolved through the access policy."""

    CREDENTIAL = "credential"
    """A stored credential record."""

    AGENT = "agent"
    """A stored agent configuration."""

    KNOWLEDGE_BASE = "knowledge_base"
    """A stored knowledge base."""


class ResourcePermission(StrEnum):
    """Permission level granted for a referenced resource.

    Values:
        READ: The viewer can see and use the resource. Credentials remain
            masked in API responses and can only be used by runtime code.
        EDIT: The viewer can read/use the resource and also mutate it. This
            is equivalent to co-owner access for the specific resource.

    ``EDIT`` implies ``READ``.
    """

    READ = "read"
    """Read/use access."""

    EDIT = "edit"
    """Read/use access plus mutation rights."""


class ResourceRef(BaseModel):
    """Reference to a resource owned by a specific user.

    Policies return ``ResourceRef`` objects to describe cross-owner resources
    that a viewer may access. Routers and runtime services then load the
    concrete record through the existing owner-scoped storage APIs using
    ``(owner_id, resource_id)``.
    """

    kind: ResourceKind = Field(
        description="Resource kind: credential, agent, or knowledge_base.",
    )
    owner_id: str = Field(
        description=(
            "Resource owner user id, matching the storage record's user_id."
        ),
    )
    resource_id: str = Field(
        description=(
            "Resource id under the owner, such as credential_id or agent_id."
        ),
    )
    permission: ResourcePermission = Field(
        default=ResourcePermission.READ,
        description="Granted permission level. Defaults to read.",
    )


class ResourceAccessPolicyBase(ABC):
    """Base class for cross-owner resource access policies.

    Applications subclass this policy to read access rules from configuration,
    external IAM systems, LDAP, request-scoped context, or any other source.

    The policy intentionally does not manage users, groups, memberships, or
    resource-share records in AgentScope storage. It only maps a ``viewer_id``
    to resource references.
    """

    @abstractmethod
    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        """List cross-owner resources of ``kind`` accessible by ``viewer_id``.

        The returned refs should not include resources owned by ``viewer_id``.
        Callers merge the viewer's own resources from storage with these
        cross-owner refs.

        Args:
            viewer_id (`str`): The current viewer's user id.
            kind (`ResourceKind`): The resource kind to list.
            storage (`StorageBase`): The owner-scoped storage backend. Policy
                subclasses may use it for read-only lookups.

        Returns:
            `list[ResourceRef]`: The accessible cross-owner resource
            references.
        """

    async def can_edit(
        self,
        viewer_id: str,
        kind: ResourceKind,
        owner_id: str,
        resource_id: str,
        storage: StorageBase,
    ) -> bool:
        """Return whether ``viewer_id`` may mutate the given resource.

        Owners can always edit their own resources. For cross-owner access,
        the default implementation falls back to :meth:`list_accessible` and
        grants mutation only when a matching ref has
        ``ResourcePermission.EDIT``. Subclasses may override this method to
        perform a more efficient direct authorization check.

        Args:
            viewer_id (`str`): The current viewer's user id.
            kind (`ResourceKind`): The resource kind.
            owner_id (`str`): The resource owner's user id.
            resource_id (`str`): The resource id under ``owner_id``.
            storage (`StorageBase`): The owner-scoped storage backend.

        Returns:
            `bool`: ``True`` when mutation is allowed, otherwise ``False``.
        """
        if viewer_id == owner_id:
            return True

        refs = await self.list_accessible(viewer_id, kind, storage)
        return any(
            ref.kind == kind
            and ref.owner_id == owner_id
            and ref.resource_id == resource_id
            and ref.permission == ResourcePermission.EDIT
            for ref in refs
        )


class DenyAllResourceAccessPolicy(ResourceAccessPolicyBase):
    """Default policy that denies all cross-owner resource access."""

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        """Return no cross-owner resources."""
        return []
