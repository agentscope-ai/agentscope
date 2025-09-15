# -*- coding: utf-8 -*-
"""The SQLite session class."""
import json
import os
import sqlite3

from agentscope import logger
from agentscope.module import StateModule
from agentscope.session import SessionBase


class SqliteSession(SessionBase):
    """A session that uses SQLite for storage."""

    def __init__(
        self,
        sqlite_path: str,
    ) -> None:
        """Initialize the session.

        Args:
            sqlite_path (`str`):
                The path to the SQLite database file.
        """
        self.sqlite_path = sqlite_path

    async def save_session_state(
        self,
        session_id: str,
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
                (session_id, json_data),
            )
            conn.commit()
            cursor.close()

    async def load_session_state(
        self,
        session_id: str,
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Get the state dictionary from the SQLite database.

        Args:
            session_id (`str`):
                The session id.
            allow_not_exist (`bool`, defaults to `True`):
                Whether to allow the session to not exist. If `False`, raises
                an error if the session does not exist.
            **state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        if not os.path.exists(self.sqlite_path):
            if allow_not_exist:
                logger.info(
                    "SQLite database %s does not exist. "
                    "Skipping load for session_id %s.",
                    self.sqlite_path,
                    session_id,
                )
                return
            raise ValueError(
                "Failed to load session state because the SQLite database "
                f"file '{self.sqlite_path}' does not exist.",
            )

        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()

            try:
                # If the table does not exist, return
                cursor.execute(
                    """
                    SELECT name FROM sqlite_master WHERE type='table' AND
                        name='as_session';
                    """,
                )
                if cursor.fetchone() is None:
                    if allow_not_exist:
                        logger.info(
                            "Session table does not exist in database %s. "
                            "Skipping load for session_id %s.",
                            self.sqlite_path,
                            session_id,
                        )
                        return

                    raise ValueError(
                        "Failed to load session state because the session "
                        "table 'as_session' does not exist in database "
                        f"{self.sqlite_path}.",
                    )

                # Query the session data
                cursor.execute(
                    "SELECT session_data FROM as_session WHERE session_id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()

                if row is None:
                    if allow_not_exist:
                        logger.info(
                            "Session_id %s does not exist in database %s. "
                            "Skip loading.",
                            session_id,
                            self.sqlite_path,
                        )
                        return

                    raise ValueError(
                        f"Failed to load session state for session_id "
                        f"{session_id} does not exist.",
                    )

                session_data = json.loads(row[0])

                for name, module in state_modules_mapping.items():
                    if name in session_data:
                        module.load_state_dict(session_data[name])
                    else:
                        raise ValueError(
                            f"State module '{name}' not found in session "
                            "data.",
                        )
                logger.info(
                    "Load session state for session_id %s from "
                    "database %s successfully.",
                    session_id,
                    self.sqlite_path,
                )

            finally:
                cursor.close()
