# -*- coding: utf-8 -*-
"""Read-only session list projections; never use them to persist state."""
from dataclasses import dataclass

from ._session import SessionRecord
from ....state import ToolContext


@dataclass
class SessionListItem:
    """A trimmed record and the tail tool states needed for live status."""

    session: SessionRecord
    tool_call_states: list[str]

    @classmethod
    def from_record(cls, record: SessionRecord) -> "SessionListItem":
        """Project a full record without mutating the stored state."""
        context = record.state.context
        states = []
        if context and context[-1].role == "assistant":
            states = [
                block.state
                for block in context[-1].get_content_blocks("tool_call")
            ]
        return cls(
            session=record.model_copy(
                update={
                    "state": record.state.model_copy(
                        update={
                            "context": [],
                            "summary": "",
                            "tool_context": ToolContext(),
                        },
                    ),
                },
            ),
            tool_call_states=states,
        )
