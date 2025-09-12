# -*- coding: utf-8 -*-
"""The JSON session class."""
import json
import os
import zlib

from ._session_base import SessionBase
from ..module import StateModule


class JSONSession(SessionBase):
    """The JSON session class."""

    def __init__(
        self, session_id: str, save_dir: str, compress: bool = False
    ) -> None:
        """Initialize the JSON session class with optional compression.

        Args:
            session_id (`str`):
                The session id.
            save_dir (`str`):
                The directory to save the session state.
            compress (`bool`):
                Whether to enable compression for session data.
        """
        super().__init__(session_id=session_id)
        self.save_dir = save_dir
        self.compress = compress

    @property
    def save_path(self) -> str:
        """The path to save the session state."""
        os.makedirs(self.save_dir, exist_ok=True)
        return os.path.join(self.save_dir, f"{self.session_id}.json")

    async def save_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Save the state dictionary to a JSON file with optional compression.

        Args:
            **state_modules_mapping (`dict[str, StateModule]`):
                A dictionary mapping of state module names to their instances.
        """
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        data = json.dumps(state_dicts, ensure_ascii=False).encode("utf-8")
        if self.compress:
            data = zlib.compress(data)
        with open(
            self.save_path,
            "wb" if self.compress else "w",
            encoding=None if self.compress else "utf-8",
        ) as file:
            if self.compress:
                file.write(data)  # Write bytes directly for compressed data
            else:
                file.write(data.decode("utf-8"))  # Decode bytes to string for uncompressed data

    async def load_session_state(
        self,
        **state_modules_mapping: StateModule,
    ) -> None:
        """Load the state dictionary from a JSON file with optional decompression.

        Args:
            state_modules_mapping (`list[StateModule]`):
                The list of state modules to be loaded.
        """
        if os.path.exists(self.save_path):
            with open(
                self.save_path,
                "rb" if self.compress else "r",
                encoding=None if self.compress else "utf-8",
            ) as file:
                data = file.read()
                if self.compress:
                    data = zlib.decompress(data).decode("utf-8")
                states = json.loads(data)

            for name, state_module in state_modules_mapping.items():
                if name in states:
                    state_module.load_state_dict(states[name])
        else:
            raise ValueError(
                f"Failed to load session state for file {self.save_path} "
                "does not exist.",
            )
