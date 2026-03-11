# -*- coding: utf-8 -*-
"""The JSON session class."""
import json
import os

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
            save_dir (`str`, defaults to `"./"`):
                The directory to save the session state.
        """
        self.save_dir = os.path.abspath(save_dir)

    def _validate_identifier(self, value: str) -> None:
        """Validate session_id and user_id against path traversal."""
        if not value:
            return

        # Reject absolute paths
        if os.path.isabs(value):
            raise ValueError("Invalid session_id/user_id")

        # Reject traversal patterns
        if ".." in value:
            raise ValueError("Invalid session_id/user_id")

        # Reject any path separators
        if os.sep in value or (os.altsep and os.altsep in value):
            raise ValueError("Invalid session_id/user_id")

    def _get_save_path(self, session_id: str, user_id: str) -> str:
        """The path to save the session state."""
        os.makedirs(self.save_dir, exist_ok=True)

        # ---- SECURITY FIX: Strict Path Traversal Prevention (CWE-22) ----

        self._validate_identifier(session_id)
        self._validate_identifier(user_id)

        if user_id:
            file_name = f"{user_id}_{session_id}.json"
        else:
            file_name = f"{session_id}.json"

        full_path = os.path.join(self.save_dir, file_name)

        # Final defense-in-depth check
        full_path_real = os.path.realpath(full_path)

        if not full_path_real.startswith(self.save_dir + os.sep):
            raise ValueError("Invalid session_id/user_id")

        return full_path_real

    async def save_session_state(
        self,
        session_id: str,
        user_id: str = "",
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the state dictionary to a JSON file."""
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }

        save_path = self._get_save_path(session_id, user_id=user_id)

        with open(
            save_path,
            "w",
            encoding="utf-8",
            errors="surrogatepass",
        ) as file:
            json.dump(state_dicts, file, ensure_ascii=False)

    async def load_session_state(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from a JSON file."""
        session_save_path = self._get_save_path(session_id, user_id=user_id)

        if os.path.exists(session_save_path):
            with open(
                session_save_path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as file:
                states = json.load(file)

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
