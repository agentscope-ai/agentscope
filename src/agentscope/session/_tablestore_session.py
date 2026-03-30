# -*- coding: utf-8 -*-
"""The Tablestore session class for agentscope."""
import asyncio
import json
from typing import Any, Optional

from ._session_base import SessionBase
from .._logging import logger
from ..module import StateModule


class TablestoreSession(SessionBase):
    """A Tablestore-based session implementation using
    ``tablestore_for_agent_memory``'s ``AsyncMemoryStore``.

    This session stores and retrieves agent state via the session table's
    metadata field in Tablestore, enabling persistent session management
    across distributed environments.
    """

    _SESSION_SECONDARY_INDEX_NAME = "agentscope_session_secondary_index"
    _SESSION_SEARCH_INDEX_NAME = "agentscope_session_search_index"
    _MESSAGE_SECONDARY_INDEX_NAME = "agentscope_message_secondary_index"
    _MESSAGE_SEARCH_INDEX_NAME = "agentscope_message_search_index"

    def __init__(
        self,
        end_point: str,
        instance_name: str,
        access_key_id: str,
        access_key_secret: str,
        sts_token: Optional[str] = None,
        session_table_name: str = "agentscope_session",
        message_table_name: str = "agentscope_message",
        **kwargs: Any,
    ) -> None:
        """Initialize the Tablestore session.

        Args:
            end_point (`str`):
                The endpoint of the Tablestore instance.
            instance_name (`str`):
                The name of the Tablestore instance.
            access_key_id (`str`):
                The access key ID for authentication.
            access_key_secret (`str`):
                The access key secret for authentication.
            sts_token (`str | None`, optional):
                The STS token for temporary credentials.
            session_table_name (`str`, defaults to
                ``"agentscope_session"``):
                The table name for storing sessions.
            message_table_name (`str`, defaults to
                ``"agentscope_message"``):
                The table name for storing messages.
            **kwargs (`Any`):
                Additional keyword arguments passed to the
                ``AsyncMemoryStore``.
        """
        try:
            from tablestore import (
                AsyncOTSClient as AsyncTablestoreClient,
                WriteRetryPolicy,
            )
            from tablestore_for_agent_memory.memory.async_memory_store import (
                AsyncMemoryStore,
            )
        except ImportError as exc:
            raise ImportError(
                "The 'tablestore' and 'tablestore-for-agent-memory' packages "
                "are required for TablestoreSession. Please install them via "
                "'pip install tablestore tablestore-for-agent-memory'.",
            ) from exc

        self._tablestore_client = AsyncTablestoreClient(
            end_point=end_point,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            instance_name=instance_name,
            sts_token=None if sts_token == "" else sts_token,
            retry_policy=WriteRetryPolicy(),
        )

        self._session_table_name = session_table_name
        self._message_table_name = message_table_name
        self._memory_store: Optional[AsyncMemoryStore] = None
        self._memory_store_kwargs = kwargs
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Lazily initialize the memory store on first use.

        Uses an ``asyncio.Lock`` to prevent concurrent initialization when
        multiple coroutines call this method simultaneously.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            from tablestore_for_agent_memory.memory.async_memory_store import (
                AsyncMemoryStore,
            )

            self._memory_store = AsyncMemoryStore(
                tablestore_client=self._tablestore_client,
                session_table_name=self._session_table_name,
                message_table_name=self._message_table_name,
                session_secondary_index_name=(
                    self._SESSION_SECONDARY_INDEX_NAME
                ),
                session_search_index_name=self._SESSION_SEARCH_INDEX_NAME,
                message_secondary_index_name=(
                    self._MESSAGE_SECONDARY_INDEX_NAME
                ),
                message_search_index_name=self._MESSAGE_SEARCH_INDEX_NAME,
                **self._memory_store_kwargs,
            )

            await self._memory_store.init_table()
            await self._memory_store.init_search_index()
            self._initialized = True

    async def save_session_state(
        self,
        session_id: str,
        user_id: str = "",
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the session state to Tablestore.

        Each state module's ``state_dict()`` is serialized to JSON and stored
        in the session table's metadata field under the key
        ``"__state__"``.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`, default to ``""``):
                The user ID for the storage.
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        from tablestore_for_agent_memory.base.base_memory_store import (
            Session as TablestoreSessionModel,
        )

        await self._ensure_initialized()

        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        serialized_state = json.dumps(state_dicts, ensure_ascii=False)

        # Create a session model
        tablestore_session = TablestoreSessionModel(
            session_id=session_id,
            user_id=user_id or "default",
            metadata={"__state__": serialized_state},
        )
        await self._memory_store.update_session(tablestore_session)

        logger.info(
            "Saved session state to Tablestore for session '%s'.",
            session_id,
        )

    async def load_session_state(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the session state from Tablestore.

        The state is read from the session table's metadata field
        under the key ``"__state__"``.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`, default to ``""``):
                The user ID for the storage.
            allow_not_exist (`bool`, defaults to ``True``):
                Whether to allow the session to not exist.
            **state_modules_mapping (`dict[str, StateModule]`):
                The mapping of state modules to be loaded.
        """
        await self._ensure_initialized()

        tablestore_session = await self._memory_store.get_session(
            user_id=user_id or "default",
            session_id=session_id,
        )

        if not tablestore_session:
            if allow_not_exist:
                logger.info(
                    "Session '%s' does not exist in Tablestore. "
                    "Skip loading session state.",
                    session_id,
                )
                return
            raise ValueError(
                f"Failed to load session state because session "
                f"'{session_id}' does not exist in Tablestore.",
            )

        state_content = (tablestore_session.metadata or {}).get("__state__")

        if state_content is None:
            if allow_not_exist:
                logger.info(
                    "No state data found for session '%s'. "
                    "Skip loading session state.",
                    session_id,
                )
                return
            raise ValueError(
                f"Failed to load session state because no state data "
                f"found for session '{session_id}'.",
            )

        states = json.loads(state_content)

        for name, state_module in state_modules_mapping.items():
            if name in states:
                state_module.load_state_dict(states[name])

        logger.info(
            "Loaded session state from Tablestore for session '%s'.",
            session_id,
        )

    async def close(self) -> None:
        """Close the Tablestore client connection."""
        if self._memory_store is not None:
            await self._memory_store.close()
            self._memory_store = None
            self._initialized = False

    async def __aenter__(self) -> "TablestoreSession":
        """Enter the async context manager.

        Returns:
            `TablestoreSession`:
                The current ``TablestoreSession`` instance.
        """
        await self._ensure_initialized()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and close the connection.

        Args:
            exc_type (`type[BaseException] | None`):
                The type of the exception.
            exc_value (`BaseException | None`):
                The exception instance.
            traceback (`Any`):
                The traceback.
        """
        await self.close()
