# -*- coding: utf-8 -*-
"""Audience vocabulary shared by the ``message``, ``event``, ``agent`` and
``console`` modules. Lives in ``types`` (a leaf) so all of them can depend
on it downward without an import cycle."""
from enum import StrEnum


class Visibility(StrEnum):
    """Who a message or an event is meant for.

    An orchestration produces far more traffic than a person wants to
    read: a planner briefing a worker, a critic's rework notes, a code
    generator's thousand-line file. This value rides along with every
    :class:`~..message.Msg` and every :class:`~..event.AgentEvent`, so a
    frontend routes on a field instead of guessing from the agent's name.

    It says nothing about the LLM context. An ``INTERNAL`` message is
    still fed to whichever agent observes it — only the human-facing
    surface reads this field.
    """

    USER = "user"
    """Belongs in the main conversation. The default."""

    INTERNAL = "internal"
    """Agent-to-agent traffic. A frontend keeps it out of the
    conversation, showing it (if at all) in a trace or debug view."""

    ARTIFACT = "artifact"
    """A product the conversation refers to rather than contains — the
    generated code, the drafted document, the rendered report. A frontend
    streams it into a side panel or drawer and leaves a reference in the
    main thread. A surface with nowhere else to put it (a terminal, a
    chat channel) falls back to rendering it inline."""
