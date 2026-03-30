# -*- coding: utf-8 -*-
"""The JSON session class."""
import json
import os
import re
import aiofiles

from ._session_base import SessionBase
from .._logging import logger
from ..module import StateModule


class JSONSession(SessionBase):
    """The JSON session class."""

    def __init__(
        self,
        save_dir: str = "./",
    ) -> None:
        """Initialize the JSON session class.

        Args:
            save_dir (`str`, defaults to `"./"):
                The directory to save the session state.
        """
        self.save_dir = save_dir

    @staticmethod
    def _sanitize_identifier(identifier: str) -> str:
        """Sanitize session identifiers to safe file-name components."""
        return re.sub(r"[^a-zA-Z0-9._-]", "_", identifier)

    def _get_save_path(self, session_id: str, user_id: str) -> str:
        """The path to save the session state.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`):
                The user ID for the storage.

        Returns:
            `str`:
                The path to save the session state.
        """
        safe_session_id = self._sanitize_identifier(session_id)
        if not safe_session_id:
            raise ValueError("The session_id cannot be empty.")

        safe_user_id = self._sanitize_identifier(user_id)

        base_dir = os.path.realpath(self.save_dir)
        os.makedirs(base_dir, exist_ok=True)

        if safe_user_id:
            file_path = f"{safe_user_id}_{safe_session_id}.json"
        else:
            file_path = f"{safe_session_id}.json"

        save_path = os.path.realpath(os.path.join(base_dir, file_path))
        if os.path.commonpath([base_dir, save_path]) != base_dir:
            raise ValueError(
                "The generated session path is outside of save_dir.",
            )

        return save_path

    async def save_session_state(
        self,
        session_id: str,
        user_id: str = "",
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from a JSON file.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`, default to `""`):
                The user ID for the storage.
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        session_save_path = self._get_save_path(session_id, user_id=user_id)
        async with aiofiles.open(
            session_save_path,
            "w",
            encoding="utf-8",
            errors="surrogatepass",
        ) as f:
            await f.write(json.dumps(state_dicts, ensure_ascii=False))

        logger.info(
            "Saved session state to %s successfully.",
            session_save_path,
        )

    async def load_session_state(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Get the state dictionary to be saved to a JSON file.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`, default to `""`):
                The user ID for the storage.
            allow_not_exist (`bool`, defaults to `True`):
                Whether to allow the session to not exist. If `False`, raises
                an error if the session does not exist.
            state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        session_save_path = self._get_save_path(session_id, user_id=user_id)
        if os.path.exists(session_save_path):
            async with aiofiles.open(
                session_save_path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as f:
                content = await f.read()
                states = json.loads(content)

            for name, state_module in state_modules_mapping.items():
                if name in states:
                    state_module.load_state_dict(states[name])
            logger.info(
                "Load session state from %s successfully.",
                session_save_path,
            )

        elif allow_not_exist:
            logger.info(
                "Session file %s does not exist. Skip loading session state.",
                session_save_path,
            )

        else:
            raise ValueError(
                f"Failed to load session state for file {session_save_path} "
                "does not exist.",
            )
