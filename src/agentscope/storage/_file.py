# -*- coding: utf-8 -*-
"""The file storage module."""
import os
import uuid
from datetime import datetime
from typing import Any, Literal

import aiofiles
from pydantic import Field, BaseModel

from ._base import StorageBase
from ..agent import AgentState
from ..message import Msg


class Session(BaseModel):
    """The session data abstraction."""

    type: Literal["agent_session"] = "agent_session"

    session_id: str
    """Unique identifier for the session"""

    # The agent states, in-case there are multiple agents in the session, e.g.
    # sub-agents or team collaboration.
    state: dict[str, AgentState] = Field(default_factory=dict)
    """The state of each agent in the session, keyed by agent_id"""

    # MetaInfo
    created_at: datetime = Field(default_factory=datetime.now)
    """Timestamp when the session was created"""
    updated_at: datetime = Field(default_factory=datetime.now)
    """Timestamp when the session was last updated"""
    name: str | None = None
    """Optional name"""

    # For frontend rendering
    history: list[Msg] = Field(default_factory=list)
    """The completed message history for this session, used to render the
    conversation history in the frontend."""


class LocalJSONStorage(StorageBase):
    """Store session data in local JSON files.

    The sessions will be saved as JSON files under the user-agent-session
    directory as follows:

    ```
    root_dir/
    ├── {user_id}/
    │   ├── {session_id}.json
    │   ├── {session_id}.json
    │   └── ...
    └── ...
    ```
    """

    def __init__(self, root_dir: str) -> None:
        """Initialize the local JSON storage.

        Args:
            root_dir (`str`):
                The root directory to store the JSON files.
        """
        self.root_dir = root_dir

    def _session_path(self, user_id: str, session_id: str) -> str:
        return os.path.join(self.root_dir, user_id, f"{session_id}.json")

    def _user_dir(self, user_id: str) -> str:
        return os.path.join(self.root_dir, user_id)

    async def _read_session(
        self,
        user_id: str,
        session_id: str,
    ) -> Session | None:
        path = self._session_path(user_id, session_id)
        if not os.path.exists(path):
            return None
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        return Session.model_validate_json(content)

    async def _write_session(self, user_id: str, session: Session) -> None:
        user_dir = self._user_dir(user_id)
        os.makedirs(user_dir, exist_ok=True)
        path = self._session_path(user_id, session.session_id)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(session.model_dump_json(indent=2))

    # ============ History management ============

    async def get_history(
        self,
        session_id: str,
        limit: int,
        user_id: str = "default",
        **kwargs: Any,
    ) -> list[Msg]:
        session = await self._read_session(user_id, session_id)
        if session is None:
            return []
        return session.history[-limit:] if limit > 0 else session.history

    async def upsert_history(
        self,
        session_id: str,
        msgs: list[Msg],
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        existing_ids = {m.id for m in session.history}
        for msg in msgs:
            if msg.id in existing_ids:
                session.history = [
                    msg if m.id == msg.id else m for m in session.history
                ]
            else:
                session.history.append(msg)
        session.updated_at = datetime.now()
        await self._write_session(user_id, session)

    # ============ Agent state management ============

    async def get_state(
        self,
        session_id: str,
        agent_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> AgentState:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        if agent_id not in session.state:
            raise KeyError(
                f"Agent {agent_id!r} not found in session {session_id!r}",
            )
        return session.state[agent_id]

    async def update_state(
        self,
        session_id: str,
        agent_id: str,
        state: AgentState,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        session = await self._read_session(user_id, session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        session.state[agent_id] = state
        session.updated_at = datetime.now()
        await self._write_session(user_id, session)

    # ============ Session management ============

    async def list_sessions(
        self,
        user_id: str = "default",
        *args: Any,
        **kwargs: Any,
    ) -> list[str]:
        user_dir = self._user_dir(user_id)
        if not os.path.isdir(user_dir):
            return []
        return [
            fname[:-5]
            for fname in os.listdir(user_dir)
            if fname.endswith(".json")
        ]

    async def upsert_session(
        self,
        user_id: str = "default",
        **kwargs: Any,
    ) -> str:
        session_id = kwargs.get("session_id") or uuid.uuid4().hex
        name = kwargs.get("name")

        existing = await self._read_session(user_id, session_id)
        if existing is not None:
            if name is not None:
                existing.name = name
            existing.updated_at = datetime.now()
            await self._write_session(user_id, existing)
        else:
            session = Session(session_id=session_id, name=name)
            await self._write_session(user_id, session)
        return session_id

    async def delete_session(
        self,
        session_id: str,
        user_id: str = "default",
        **kwargs: Any,
    ) -> None:
        path = self._session_path(user_id, session_id)
        if os.path.exists(path):
            os.remove(path)
