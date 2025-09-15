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
        session_id: str | None = None,
        save_dir: str = "./",
    ) -> None:
        """Initialize the JSON session class.

        Args:
            session_id (`str`):
                The session id, deprecated and move to the `save_session_state`
                and `load_session_state` methods to support different session
                ids.
            save_dir (`str`, defaults to `"./"):
                The directory to save the session state.
        """
        self.save_dir = save_dir

        if session_id is not None:
            logger.warning(
                "The `session_id` argument in the JSONSession constructor is "
                "deprecated and will be removed in future versions. Please "
                "pass the `session_id` to the `save_session_state` and "
                "`load_session_state` methods instead.",
            )

    def _get_save_path(self, session_id: str) -> str:
        """The path to save the session state.

        Args:
            session_id (`str`):
                The session id.

        Returns:
            `str`:
                The path to save the session state.
        """
        os.makedirs(self.save_dir, exist_ok=True)
        return os.path.join(self.save_dir, f"{session_id}.json")

    async def save_session_state(
        self,
        session_id: str,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from a JSON file.

        Args:
            session_id (`str`):
                The session id.
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        with open(
            self._get_save_path(session_id),
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(state_dicts, file, ensure_ascii=False)

    async def load_session_state(
        self,
        session_id: str,
        allow_not_exist: bool = True,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Get the state dictionary to be saved to a JSON file.

        Args:
            session_id (`str`):
                The session id.
            allow_not_exist (`bool`, defaults to `True`):
                Whether to allow the session to not exist. If `False`, raises
                an error if the session does not exist.
            state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        session_save_path = self._get_save_path(session_id)
        if os.path.exists(session_save_path):
            with open(session_save_path, "r", encoding="utf-8") as file:
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
