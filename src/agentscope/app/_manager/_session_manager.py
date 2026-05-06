# -*- coding: utf-8 -*-
""""""
from collections import OrderedDict

from ...event import AgentEvent


class SessionManager:
    """The session manager, used to record which sessions are active and
    cache the yielded events, used for frontend when the user switches to
    certain session (to recover the agent events history).
    """

    def __init__(self) -> None:
        """Initialize the session manager."""

        self._sessions: dict[str, list[AgentEvent]] = OrderedDict()
