# -*- coding: utf-8 -*-
"""Slash-command API schemas."""

from pydantic import BaseModel


class CommandInfo(BaseModel):
    """Public metadata for one slash command."""

    name: str
    command: str
    aliases: list[str]
    description: str
    accepts_args: bool


class CommandListResponse(BaseModel):
    """Response containing all built-in commands."""

    commands: list[CommandInfo]
