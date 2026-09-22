# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the built-in registry of :class:`CredentialFactory`.

Every concrete credential the ``agentscope.credential`` package exports must
be resolvable through the factory: the app's credential router deserializes
stored payloads with :meth:`CredentialFactory.from_dict` and the WebUI builds
its forms from :meth:`CredentialFactory.list_schemas`.  A type that is
exported but not registered cannot be stored at all.
"""
import inspect
import unittest

from fastapi import HTTPException

from agentscope import credential as _credential_package
from agentscope.app._router._model import ListModelsRequest, list_models
from agentscope.app._service._model import get_model
from agentscope.app.storage import ChatModelConfig, CredentialRecord
from agentscope.credential import (
    CredentialBase,
    CredentialFactory,
    TypeSafeCredential,
)


def _exported_credential_classes() -> list[str]:
    """Return the sorted class names of the exported credential subclasses.

    Returns:
        `list[str]`:
            The names of every concrete :class:`CredentialBase` subclass the
            ``agentscope.credential`` package lists in ``__all__``.
    """
    exported = []
    for name in _credential_package.__all__:
        obj = getattr(_credential_package, name)
        if (
            inspect.isclass(obj)
            and issubclass(obj, CredentialBase)
            and obj is not CredentialBase
        ):
            exported.append(obj.__name__)
    return sorted(exported)


class _StubAccess:  # pylint: disable=too-few-public-methods
    """A resource access service that returns one fixed credential record."""

    def __init__(self, record: CredentialRecord) -> None:
        """Store the record that every resolution should return."""
        self._record = record

    async def resolve_credential(
        self,
        _viewer_id: str,
        _credential_id: str,
    ) -> CredentialRecord:
        """Return the fixed record.

        Args:
            _viewer_id (`str`):
                The viewer's user id, ignored by the stub.
            _credential_id (`str`):
                The credential id being resolved, ignored by the stub.

        Returns:
            `CredentialRecord`:
                The stored record.
        """
        return self._record


class CredentialRegistryTest(unittest.TestCase):
    """The factory must know about every exported credential type."""

    def test_registry_covers_every_exported_credential(self) -> None:
        """An exported credential that is not registered is a regression."""
        self.assertEqual(
            _exported_credential_classes(),
            sorted(c.__name__ for c in CredentialFactory._classes),
        )

    def test_typesafe_credential_round_trips_through_the_factory(
        self,
    ) -> None:
        """A classifier-only credential still stores and reloads."""
        credential = TypeSafeCredential(api_key="secret")
        data = credential.model_dump(mode="json")

        self.assertEqual(
            CredentialFactory.from_dict(data).model_dump(mode="json"),
            data,
        )

    def test_list_schemas_offers_typesafe_credential(self) -> None:
        """The WebUI form list includes the TypeSafe discriminator."""
        self.assertIn(
            "typesafe_credential",
            sorted(
                schema["properties"]["type"]["const"]
                for schema in CredentialFactory.list_schemas()
                if "type" in schema.get("properties", {})
            ),
        )


class ChatModelRouteTest(unittest.IsolatedAsyncioTestCase):
    """Providers without a chat model answer with 4xx, not 500."""

    async def test_list_models_rejects_a_credential_without_a_chat_model(
        self,
    ) -> None:
        """Asking for TypeSafe chat models is a bad request, not a crash."""
        request = ListModelsRequest(provider="typesafe_credential")
        with self.assertRaises(HTTPException) as ctx:
            await list_models(request)

        self.assertEqual(
            (ctx.exception.status_code, ctx.exception.detail),
            (
                400,
                "Provider 'typesafe_credential' serves no chat model.",
            ),
        )

    async def test_get_model_rejects_a_credential_without_a_chat_model(
        self,
    ) -> None:
        """A session may not build a chat model from a TypeSafe credential."""
        credential = TypeSafeCredential(api_key="secret")
        record = CredentialRecord(data=credential.model_dump(mode="json"))
        config = ChatModelConfig(
            type="typesafe_credential",
            credential_id=record.id,
            model="system-one",
            parameters={},
        )

        with self.assertRaises(HTTPException) as ctx:
            await get_model("alice", config, _StubAccess(record))

        self.assertEqual(
            (ctx.exception.status_code, ctx.exception.detail),
            (
                400,
                f"Credential {record.id!r} is a TypeSafeCredential "
                "and serves no chat model.",
            ),
        )
