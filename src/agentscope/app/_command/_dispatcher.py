# -*- coding: utf-8 -*-
"""Execution boundary shared by HTTP and channel slash commands."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, TYPE_CHECKING

from ._registry import CommandMatch, list_commands

if TYPE_CHECKING:
    from .._service._session import SessionService


@dataclass(frozen=True)
class CommandContext:
    """Carry the authenticated scope of a slash command."""

    user_id: str
    agent_id: str
    session_id: str
    source: Literal["http", "channel"]
    command_message_id: str


@dataclass(frozen=True)
class CommandResult:
    """Describe a completed command without creating chat history."""

    name: str
    root_session_id: str
    affected_session_ids: tuple[str, ...]
    message: str


CommandHandler = Callable[
    [CommandContext, "SessionService"],
    Awaitable[CommandResult],
]


async def _clear(
    context: CommandContext,
    session_service: "SessionService",
) -> CommandResult:
    """Execute the built-in conversation reset command."""
    affected = await session_service.clear_conversation(
        context.user_id,
        context.agent_id,
        context.session_id,
    )
    return CommandResult(
        name="clear",
        root_session_id=context.session_id,
        affected_session_ids=affected,
        message="Conversation cleared.",
    )


_HANDLERS: dict[str, CommandHandler] = {"clear": _clear}
_COMMAND_NAMES = {spec.name for spec in list_commands()}
if set(_HANDLERS) != _COMMAND_NAMES:
    raise RuntimeError(
        f"Slash command registry and handlers differ: "
        f"commands={sorted(_COMMAND_NAMES)!r}, "
        f"handlers={sorted(_HANDLERS)!r}.",
    )


async def dispatch_command(
    match: CommandMatch,
    context: CommandContext,
    session_service: "SessionService",
) -> CommandResult:
    """Execute a recognized command through its registered handler."""
    handler = _HANDLERS.get(match.spec.name)
    if handler is None:
        raise RuntimeError(
            f"Slash command {match.spec.name!r} has no handler.",
        )
    return await handler(context, session_service)
