# -*- coding: utf-8 -*-
"""Slash-command discovery endpoint."""

from fastapi import APIRouter

from .._command import list_commands
from ._schema import CommandInfo, CommandListResponse

command_router = APIRouter(prefix="/commands", tags=["commands"])


@command_router.get("/", response_model=CommandListResponse)
async def commands() -> CommandListResponse:
    """List built-in slash commands exposed by the service."""
    return CommandListResponse(
        commands=[
            CommandInfo(
                name=spec.name,
                command=f"/{spec.name}",
                aliases=list(spec.aliases),
                description=spec.description,
                accepts_args=spec.accepts_args,
            )
            for spec in list_commands()
        ],
    )
