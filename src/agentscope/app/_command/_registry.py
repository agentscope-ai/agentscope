# -*- coding: utf-8 -*-
"""Static registry for service-owned slash commands."""

from dataclasses import dataclass

from ...message import Msg, TextBlock


@dataclass(frozen=True)
class CommandSpec:
    """Describe one built-in slash command."""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    accepts_args: bool = False


@dataclass(frozen=True)
class CommandMatch:
    """Represent a recognized command and its raw arguments."""

    spec: CommandSpec
    args: str
    message: Msg


BUILTIN_COMMANDS = (
    CommandSpec(
        name="clear",
        description="Clear the current conversation context",
    ),
)


def _build_index() -> dict[str, CommandSpec]:
    """Build the immutable name and alias lookup."""
    result: dict[str, CommandSpec] = {}
    for spec in BUILTIN_COMMANDS:
        names = (spec.name, *spec.aliases)
        for name in names:
            key = name.casefold().strip()
            if not key:
                raise ValueError("Slash command names cannot be empty.")
            if key in result:
                raise ValueError(f"Duplicate slash command name {name!r}.")
            result[key] = spec
    return result


_COMMAND_INDEX = _build_index()


def parse_command(value: object) -> CommandMatch | None:
    """Return a match for one plain user message, otherwise ``None``."""
    message: Msg | None = None
    if isinstance(value, Msg):
        message = value
    elif (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], Msg)
    ):
        message = value[0]
    if message is None or message.role != "user":
        return None
    if len(message.content) != 1 or not isinstance(
        message.content[0],
        TextBlock,
    ):
        return None

    text = message.content[0].text.strip()
    if not text.startswith("/") or text.startswith("//"):
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return None
    command = parts[0]
    args = parts[1].strip() if len(parts) == 2 else ""
    spec = _COMMAND_INDEX.get(command.casefold())
    if spec is None:
        return None
    return CommandMatch(spec=spec, args=args, message=message)


def list_commands() -> tuple[CommandSpec, ...]:
    """Return command metadata in stable name order."""
    return tuple(sorted(BUILTIN_COMMANDS, key=lambda item: item.name))
