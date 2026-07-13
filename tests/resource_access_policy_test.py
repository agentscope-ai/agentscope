# -*- coding: utf-8 -*-
"""Tests for resource access policy primitives."""

from typing import cast
from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import HTTPException
from utils import AnyValue

from agentscope.app.access import (
    DenyAllResourceAccessPolicy,
    ResourceAccessPolicyBase,
    ResourceKind,
    ResourcePermission,
    ResourceRef,
)
from agentscope.app._service import ResourceAccessService
from agentscope.app.storage import AgentData, AgentRecord, CredentialRecord
from agentscope.app.storage import StorageBase
from agentscope.agent import ContextConfig, ReActConfig


class _EditPolicy(ResourceAccessPolicyBase):
    """Test policy that grants edit access to one cross-owner agent."""

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        if viewer_id == "viewer" and kind == ResourceKind.AGENT:
            return [
                ResourceRef(
                    kind=ResourceKind.AGENT,
                    owner_id="owner",
                    resource_id="agent-1",
                    permission=ResourcePermission.EDIT,
                ),
            ]
        return []


class _SharingPolicy(ResourceAccessPolicyBase):
    """Test policy that grants read access to shared resources."""

    async def list_accessible(
        self,
        viewer_id: str,
        kind: ResourceKind,
        storage: StorageBase,
    ) -> list[ResourceRef]:
        if viewer_id != "viewer":
            return []
        if kind == ResourceKind.AGENT:
            return [
                ResourceRef(
                    kind=ResourceKind.AGENT,
                    owner_id="owner",
                    resource_id="agent-1",
                ),
                ResourceRef(
                    kind=ResourceKind.AGENT,
                    owner_id="owner",
                    resource_id="team-agent",
                ),
            ]
        if kind == ResourceKind.CREDENTIAL:
            return [
                ResourceRef(
                    kind=ResourceKind.CREDENTIAL,
                    owner_id="owner",
                    resource_id="credential-1",
                ),
            ]
        return []


class _FakeStorage:
    """Tiny storage double for resource access tests."""

    def __init__(self) -> None:
        """Initialize in-memory records."""
        self.credentials: dict[tuple[str, str], CredentialRecord] = {
            ("owner", "credential-1"): CredentialRecord(
                id="credential-1",
                user_id="owner",
                data={"type": "test"},
            ),
            ("viewer", "credential-own"): CredentialRecord(
                id="credential-own",
                user_id="viewer",
                data={"type": "own"},
            ),
        }
        self.agents: dict[tuple[str, str], AgentRecord] = {
            ("owner", "agent-1"): AgentRecord(
                id="agent-1",
                user_id="owner",
                data=_make_agent_data("Shared"),
            ),
            ("owner", "team-agent"): AgentRecord(
                id="team-agent",
                user_id="owner",
                source="team",
                data=_make_agent_data("Shared team"),
            ),
            ("viewer", "team-agent"): AgentRecord(
                id="team-agent",
                user_id="viewer",
                source="team",
                data=_make_agent_data("Own team"),
            ),
        }

    async def list_credentials(
        self,
        user_id: str,
    ) -> list[CredentialRecord]:
        """List credentials owned by ``user_id``."""
        return [
            record
            for (owner_id, _), record in self.credentials.items()
            if owner_id == user_id
        ]

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Get a credential."""
        return self.credentials.get((user_id, credential_id))

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """List agents owned by ``user_id``."""
        return [
            record
            for (owner_id, _), record in self.agents.items()
            if owner_id == user_id
        ]

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Get an agent."""
        return self.agents.get((user_id, agent_id))


def _make_agent_data(name: str) -> AgentData:
    """Create valid agent data for tests."""
    return AgentData(
        name=name,
        system_prompt="You are a helpful assistant.",
        context_config=ContextConfig(),
        react_config=ReActConfig(),
    )


class ResourceAccessPolicyTest(IsolatedAsyncioTestCase):
    """Test resource access policy defaults and edit fallback."""

    async def test_default_policy_denies_cross_owner_access(self) -> None:
        """The base policy should preserve owner-isolated behavior."""
        policy = DenyAllResourceAccessPolicy()
        storage = cast(StorageBase, object())

        result = (
            await policy.list_accessible(
                "viewer",
                ResourceKind.CREDENTIAL,
                storage,
            ),
            await policy.can_edit(
                "viewer",
                ResourceKind.CREDENTIAL,
                "owner",
                "credential-1",
                storage,
            ),
        )

        self.assertEqual(result, ([], False))

    async def test_owner_can_edit_own_resource(self) -> None:
        """Owners should not need an explicit policy ref to edit resources."""
        policy = DenyAllResourceAccessPolicy()
        storage = cast(StorageBase, object())

        self.assertEqual(
            await policy.can_edit(
                "owner",
                ResourceKind.AGENT,
                "owner",
                "agent-1",
                storage,
            ),
            True,
        )

    async def test_edit_ref_allows_cross_owner_edit(self) -> None:
        """A matching edit ref should grant cross-owner mutation rights."""
        policy = _EditPolicy()
        storage = cast(StorageBase, object())

        result = (
            await policy.can_edit(
                "viewer",
                ResourceKind.AGENT,
                "owner",
                "agent-1",
                storage,
            ),
            await policy.can_edit(
                "viewer",
                ResourceKind.AGENT,
                "owner",
                "agent-2",
                storage,
            ),
        )

        self.assertEqual(result, (True, False))


class ResourceAccessServiceTest(IsolatedAsyncioTestCase):
    """Test resource access service resolution behavior."""

    async def asyncSetUp(self) -> None:
        """Create the service under test."""
        self.storage = cast(StorageBase, _FakeStorage())
        self.service = ResourceAccessService(
            storage=self.storage,
            policy=_SharingPolicy(),
        )

    async def test_get_shared_credential(self) -> None:
        """Shared credentials should resolve through owner storage."""
        view = await self.service.get_resource(
            "viewer",
            ResourceKind.CREDENTIAL,
            "credential-1",
        )

        self.assertEqual(
            view.model_dump(),
            {
                "id": "credential-1",
                "user_id": "owner",
                "created_at": AnyValue(),
                "updated_at": AnyValue(),
                # Shared credentials must be masked in the view.
                "data": {"type": "test"},
                "editable": False,
            },
        )

    async def test_resolve_shared_credential_returns_raw(self) -> None:
        """Runtime resolution should return the unmasked record."""
        record = await self.service.resolve_credential(
            "viewer",
            "credential-1",
        )

        self.assertEqual(
            record.model_dump(),
            {
                "id": "credential-1",
                "user_id": "owner",
                "created_at": AnyValue(),
                "updated_at": AnyValue(),
                "data": {"type": "test"},
            },
        )

    async def test_list_credentials_includes_own_and_shared(self) -> None:
        """Credential lists should merge own records and policy refs."""
        views = await self.service.list_resource(
            "viewer",
            ResourceKind.CREDENTIAL,
        )

        self.assertEqual(
            [v.model_dump() for v in views],
            [
                {
                    "id": "credential-own",
                    "user_id": "viewer",
                    "created_at": AnyValue(),
                    "updated_at": AnyValue(),
                    "data": {"type": "own"},
                    "editable": True,
                },
                {
                    "id": "credential-1",
                    "user_id": "owner",
                    "created_at": AnyValue(),
                    "updated_at": AnyValue(),
                    # Shared entry has masked data.
                    "data": {"type": "test"},
                    "editable": False,
                },
            ],
        )

    async def test_get_own_team_agent_is_allowed(self) -> None:
        """Runtime owner reads should still resolve team agents."""
        agent = await self.service.resolve_agent("viewer", "team-agent")

        self.assertEqual(
            agent.model_dump(),
            {
                "id": "team-agent",
                "user_id": "viewer",
                "source": "team",
                "created_at": AnyValue(),
                "updated_at": AnyValue(),
                "data": AnyValue(),
            },
        )

    async def test_list_agents_skips_shared_team_agents(self) -> None:
        """Cross-owner team agents should not appear in shared lists."""
        views = await self.service.list_resource(
            "viewer",
            ResourceKind.AGENT,
        )

        self.assertEqual(
            [v.model_dump() for v in views],
            [
                {
                    "id": "agent-1",
                    "user_id": "owner",
                    "source": "user",
                    "created_at": AnyValue(),
                    "updated_at": AnyValue(),
                    "data": AnyValue(),
                    "editable": False,
                },
            ],
        )

    async def test_missing_resource_raises_404(self) -> None:
        """Missing visible resources should raise a 404 HTTPException."""
        with self.assertRaises(HTTPException) as ctx:
            await self.service.get_resource(
                "viewer",
                ResourceKind.CREDENTIAL,
                "missing",
            )

        self.assertEqual(
            (ctx.exception.status_code, ctx.exception.detail),
            (404, "Credential 'missing' not found."),
        )
