# -*- coding: utf-8 -*-
"""Tests for custom AgentScope ID generation."""
import uuid

from agentscope import set_id_factory
from agentscope._utils._common import _id_factory
from agentscope.credential import CredentialBase
from agentscope.event import ModelCallStartEvent
from agentscope.message import AssistantMsg, TextBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse, ModelCard
from agentscope.state import AgentState, Task
from agentscope.tool import ToolResponse


class _DummyCredential(CredentialBase):
    """Concrete credential used only for ID generation tests."""

    api_key: str = ""

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """Return a dummy model class for abstract method completeness."""
        return ChatModelBase

    @classmethod
    def list_models(cls) -> list[ModelCard]:
        """Return no model cards for this test credential."""
        return []


def _reset_id_factory() -> None:
    """Restore the default random UUID hex factory."""
    set_id_factory(lambda: uuid.uuid4().hex)


def test_set_id_factory_updates_imported_factory_reference() -> None:
    """The imported _id_factory wrapper should observe factory updates."""
    try:
        set_id_factory(lambda: "custom-id")

        assert _id_factory() == "custom-id"
        assert TextBlock(text="hello").id == "custom-id"
        assert UserMsg("user", "hello").id == "custom-id"
        assert AssistantMsg("assistant", "hello").id == "custom-id"
        assert (
            ModelCallStartEvent(
                reply_id="reply",
                model_name="model",
            ).id
            == "custom-id"
        )
        assert AgentState().session_id == "custom-id"
        assert (
            Task(
                subject="subject",
                description="description",
                metadata={},
            ).id
            == "custom-id"
        )
        assert ToolResponse().id == "custom-id"
        assert (
            ChatResponse(
                content=[],
                is_last=True,
            ).id
            == "custom-id"
        )
        assert _DummyCredential().id == "custom-id"
    finally:
        _reset_id_factory()


def test_set_id_factory_supports_incremental_ids() -> None:
    """Factories can provide ordered identifiers such as UUID7 strings."""
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"id-{counter}"

    try:
        set_id_factory(next_id)

        assert TextBlock(text="first").id == "id-1"
        assert TextBlock(text="second").id == "id-2"
    finally:
        _reset_id_factory()


def test_set_id_factory_rejects_non_callable() -> None:
    """The ID factory must be callable."""
    try:
        try:
            set_id_factory("not-callable")  # type: ignore[arg-type]
        except TypeError as exc:
            assert "callable" in str(exc)
        else:
            raise AssertionError("set_id_factory accepted a non-callable")
    finally:
        _reset_id_factory()
