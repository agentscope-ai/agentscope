# -*- coding: utf-8 -*-
"""Artifacts — ports a workspace declares as viewable, plus the two
tools the agent uses to declare them.

An artifact is a **declaration, not a connection**. ``ArtifactAdd`` only
records that a port is meant to be looked at; the forward that makes it
reachable is opened later, when a viewer actually opens it (see
:meth:`WorkspaceBase.ensure_upstream`). That split is what lets the tool
be synchronous and infallible: it can be called before the service is
listening, it costs nothing for an artifact nobody opens, and it needs
no knowledge of the URL shape the serving layer will hand out.
"""
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from ..message import TextBlock, ToolResultState
from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ParamsBase, ToolBase, ToolChunk

if TYPE_CHECKING:
    from ._base import WorkspaceBase


class Upstream(BaseModel):
    """Where to dial to reach a declared port.

    Produced by :meth:`WorkspaceBase._open_upstream`, which is the one
    place the difference between execution backends lives. Everything
    above this model sees a URL and does not know whether a cloud
    provider, a published container port, or a tunnel is behind it.
    """

    kind: Literal["loopback", "provider", "direct", "tunnel"] = Field(
        description=(
            "How the address was obtained: a port on the serving host, "
            "a sandbox provider's own preview URL, a directly routable "
            "container/pod address, or a tunnel this process owns."
        ),
    )
    url: str = Field(
        description=(
            "Base URL to dial. Server-side only when "
            "``browser_reachable`` is false — it must not be handed to "
            "a browser, which cannot route to it."
        ),
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Headers every request must carry, for providers that gate "
            "their preview URLs behind one."
        ),
    )
    browser_reachable: bool = Field(
        default=False,
        description=(
            "Whether a browser can load ``url`` directly. When true the "
            "serving layer hands it over as-is and no proxying happens."
        ),
    )


class Artifact(BaseModel):
    """A port the agent has declared as worth looking at."""

    id: str = Field(
        description=(
            "Opaque identifier, stable across re-declaration so a URL "
            "already handed to a viewer keeps working."
        ),
    )
    port: int = Field(description="Port inside the workspace.")
    title: str | None = Field(
        default=None,
        description="Label for the viewer; the port is used when absent.",
    )
    entry_path: str = Field(
        default="/",
        description="Path to open first, for a service whose entry is not /.",
    )
    declared_at: float = Field(description="Unix timestamp of declaration.")


class _ArtifactAddParams(ParamsBase):
    """Parameters for :class:`ArtifactAdd`."""

    port: int = Field(
        ge=1024,
        le=65535,
        description=(
            "The port your service is listening on inside this "
            "workspace."
        ),
    )
    title: str | None = Field(
        default=None,
        description=(
            "Short label shown to the user, e.g. 'Landing page'. "
            "Defaults to the port number."
        ),
    )
    entry_path: str = Field(
        default="/",
        description=(
            "Path the viewer should open first. Only set this when the "
            "service's entry point is not the root."
        ),
    )


class _ArtifactRemoveParams(ParamsBase):
    """Parameters for :class:`ArtifactRemove`."""

    port: int = Field(
        ge=1024,
        le=65535,
        description="The port to withdraw.",
    )


class _ArtifactToolBase(ToolBase):
    """Shared plumbing: both tools act on one workspace's declarations."""

    is_mcp: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, workspace: "WorkspaceBase") -> None:
        """Bind the tool to the workspace whose ports it declares.

        Args:
            workspace (`WorkspaceBase`):
                The workspace holding the declarations.
        """
        super().__init__()
        self._workspace = workspace

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allowed: declaring touches nothing outside the
        workspace's own bookkeeping, and the user decides separately
        whether to open what was declared.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} only updates this workspace's own list.",
        )


class ArtifactAdd(_ArtifactToolBase):
    """Declare a port as viewable by the user."""

    name: str = "ArtifactAdd"

    description: str = """Make a service you are running visible to the user.

The user sees declared ports in a panel and can open one to view it \
live in their browser.

## When to Use This Tool
- You started a dev server, preview server, or studio and the user \
should look at it.
- You changed which port a service runs on and want the user pointed at \
the new one.

## When NOT to Use This Tool
- To start a service. This tool only declares an existing one — start it \
yourself first, in the background.
- To show a file or a build artifact on disk. This is for a listening \
port, not for content.

## Important
- **Your service must listen on 0.0.0.0, not 127.0.0.1.** A service \
bound to loopback is unreachable from outside the workspace and the \
user will see nothing. For Vite this means `--host 0.0.0.0`.
- Declaring is instant and does not connect to anything, so it is fine \
to call before the service has finished starting.
- Nothing is forwarded until the user actually opens it. You do not \
need to keep the artifact "warm".
- Re-declaring the same port is harmless and keeps any link the user \
already has."""

    input_schema: dict = _ArtifactAddParams.model_json_schema()
    is_read_only: bool = False

    async def call(
        self,
        port: int,
        title: str | None = None,
        entry_path: str = "/",
    ) -> ToolChunk:
        """Declare ``port`` as viewable.

        Args:
            port (`int`):
                The port the service is listening on.
            title (`str | None`, optional):
                Label shown to the user.
            entry_path (`str`, defaults to `"/"`):
                Path the viewer should open first.

        Returns:
            `ToolChunk`:
                Confirmation, or the reason the port was rejected.
        """
        try:
            artifact = self._workspace.declare_artifact(
                port,
                title=title,
                entry_path=entry_path,
            )
        except ValueError as e:
            return ToolChunk(
                content=[TextBlock(text=str(e))],
                state=ToolResultState.ERROR,
            )

        label = artifact.title or f"port {artifact.port}"
        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Declared {label} as an artifact. The user can "
                        f"now open it from their artifact panel.\n\n"
                        f"Nothing is connected yet — the forward is made "
                        f"when they open it. If they report a blank page, "
                        f"check that the service is listening on 0.0.0.0 "
                        f"and read its log."
                    ),
                ),
            ],
            state=ToolResultState.RUNNING,
        )


class ArtifactRemove(_ArtifactToolBase):
    """Withdraw a previously declared port."""

    name: str = "ArtifactRemove"

    description: str = """Withdraw a port you previously declared with \
ArtifactAdd.

Removes it from the user's panel and tears down any forward that was \
open for it.

## When to Use This Tool
- You stopped the service on that port.
- The work it was showing is finished and the user no longer needs it.

## When NOT to Use This Tool
- Between edits. The artifact survives your changes; there is no need \
to remove and re-add it.

## Important
- You do not have to call this on your way out. Everything declared is \
withdrawn when the workspace closes."""

    input_schema: dict = _ArtifactRemoveParams.model_json_schema()
    is_read_only: bool = False

    async def call(self, port: int) -> ToolChunk:
        """Withdraw ``port``.

        Args:
            port (`int`):
                The port to withdraw.

        Returns:
            `ToolChunk`:
                Confirmation, or a note that the port was not declared.
        """
        removed = await self._workspace.undeclare_artifact(port)
        if not removed:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Port {port} was not declared as an artifact.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        return ToolChunk(
            content=[TextBlock(text=f"Withdrew the artifact on port {port}.")],
            state=ToolResultState.RUNNING,
        )
