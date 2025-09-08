# -*- coding: utf-8 -*-
"""The SQLite session class."""
import json
import sqlite3

from agentscope.module import StateModule
from agentscope.session import SessionBase


class SqliteSession(SessionBase):
    """A session that uses SQLite for storage."""

    def __init__(
        self,
        sqlite_path: str,
        session_id: str,
    ) -> None:
        """Initialize the session.

        Args:
            sqlite_path (`str`):
                The path to the SQLite database file.
            session_id (`str`):
                The unique identifier for the session, e.g. the user ID.
        """
        super().__init__(session_id)

        self.sqlite_path = sqlite_path

    async def save_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the session state to the SQLite database."""
        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()
            # Prepare the session data as a dictionary
            session_data = {
                name: module.state_dict()
                for name, module in state_modules_mapping.items()
            }

            json_data = json.dumps(session_data)

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS as_session (
                    session_id TEXT,
                    session_data JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id)
                )
                """,
            )

            # Insert or replace the session data
            cursor.execute(
                """
                INSERT INTO as_session (session_id, session_data, updated_at)
                VALUES (?, json(?), CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    session_data = excluded.session_data,
                    updated_at = excluded.updated_at
                """,
                (self.session_id, json_data),
            )
            conn.commit()
            cursor.close()

    async def load_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Get the state dictionary from the SQLite database.

        Args:
            **state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()
            # Query the session data
            cursor.execute(
                "SELECT session_data FROM as_session WHERE session_id = ?",
                (self.session_id,),
            )
            row = cursor.fetchone()

            if row is None:
                raise ValueError(
                    f"Failed to load session state for session_id "
                    f"{self.session_id} does not exist.",
                )

            session_data = json.loads(row[0])

            for name, module in state_modules_mapping.items():
                if name in session_data:
                    module.load_state_dict(session_data[name])
                else:
                    raise ValueError(
                        f"State module '{name}' not found in session data.",
                    )
            cursor.close()
