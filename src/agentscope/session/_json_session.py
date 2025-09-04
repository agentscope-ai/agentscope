# -*- coding: utf-8 -*-
"""The JSON session class."""
import json
import os

from ._session_base import SessionBase
from ..module import StateModule


class JSONSession(SessionBase):
    """The JSON session class."""

    def __init__(self, session_id: str, save_dir: str) -> None:
        """Initialize the JSON session class.

        Args:
            session_id (`str`):
                The session id.
            save_dir (`str`):
                The directory to save the session state.
        """
        super().__init__(session_id=session_id)
        self.save_dir = save_dir

    @property
    def save_path(self) -> str:
        """The path to save the session state."""
        os.makedirs(self.save_dir, exist_ok=True)
        return os.path.join(self.save_dir, f"{self.session_id}.json")

    async def save_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from a JSON file.

        Args:
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        with open(self.save_path, "w", encoding="utf-8") as file:
            json.dump(state_dicts, file, ensure_ascii=False)

    async def load_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Get the state dictionary to be saved to a JSON file.

        Args:
            state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        if os.path.exists(self.save_path):
            with open(self.save_path, "r", encoding="utf-8") as file:
                states = json.load(file)

            for name, state_module in state_modules_mapping.items():
                if name in states:
                    state_module.load_state_dict(states[name])
        else:
            raise ValueError(
                f"Failed to load session state for file {self.save_path} "
                "does not exist.",
            )
