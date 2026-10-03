# -*- coding: utf-8 -*-
"""Shared base and reply helper for WhatsApp agent tools."""
from pathlib import Path
from typing import Any, TYPE_CHECKING

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .....tool import BackendBase, ParamsBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._channel import WhatsAppChannel


def _ack(data: dict | None, what: str) -> ToolChunk:
    """Turn a WhatsApp send response into a success/error chunk."""
    if data and data.get("messages"):
        return ToolChunk(content=[TextBlock(text=f"Sent {what}.")])
    error = (
        (data or {})
        .get("error", {})
        .get("message", "the platform rejected the request")
    )
    return ToolChunk(
        content=[TextBlock(text=f"Failed to send {what}: {error}")],
        state=ToolResultState.ERROR,
    )


class _WhatsAppRecipientParams(ParamsBase):
    """Address fields shared by outbound WhatsApp tools."""

    to: str = Field(
        description="Target id copied from ListChats or ListChatMembers.",
    )
    recipient_type: str = Field(
        description="The target type returned with the target id.",
        json_schema_extra={"enum": ["individual", "group"]},
    )


class _WhatsAppMediaParams(_WhatsAppRecipientParams):
    """Shared schema for outbound workspace media."""

    path: str = Field(description="Absolute workspace path to the media.")


class _WhatsAppToolBase(ToolBase):
    """Base for WhatsApp tools bound to a channel and workspace backend."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        channel: "WhatsAppChannel",
        backend: BackendBase,
    ) -> None:
        """Bind the live channel and the session workspace backend."""
        super().__init__()
        self._channel = channel
        self._backend = backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow lookups and ask for confirmation before sending."""
        return PermissionDecision(
            behavior=(
                PermissionBehavior.ALLOW
                if self.is_read_only
                else PermissionBehavior.ASK
            ),
            message=(
                "Read-only WhatsApp lookup."
                if self.is_read_only
                else "Sending to another WhatsApp recipient needs "
                "confirmation."
            ),
        )


class _WhatsAppMediaTool(_WhatsAppToolBase):
    """Shared workspace-file reader for the media send tools."""

    is_read_only = False

    async def _send_media(
        self,
        path: str,
        to: str,
        recipient_type: str,
    ) -> ToolChunk:
        """Read and send workspace media with consistent error handling."""
        try:
            data = await self._backend.read_file(path)
        except Exception as exc:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"{self.name}: cannot read {path!r}: {exc}",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        name = Path(path).name
        result = await self._send_bytes(to, recipient_type, data, name)
        return _ack(result, self._success_description(name, to))

    async def _send_bytes(
        self,
        to: str,
        recipient_type: str,
        data: bytes,
        name: str,
    ) -> dict | None:
        """Send media bytes; implemented by each media kind."""
        raise NotImplementedError

    def _success_description(self, name: str, to: str) -> str:
        """Return the acknowledgement description for the media kind."""
        raise NotImplementedError
