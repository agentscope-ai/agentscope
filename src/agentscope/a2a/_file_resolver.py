# -*- coding: utf-8 -*-
"""The JSON file based A2A agent card resolver."""
import json
from pathlib import Path
from typing import TYPE_CHECKING

from ._base import AgentCardResolverBase

if TYPE_CHECKING:
    from a2a.types import AgentCard
else:
    AgentCard = "a2a.types.AgentCard"


class FileAgentCardResolver(AgentCardResolverBase):
    """Agent card resolver that loads AgentCard from a JSON file.

    The JSON file should contain an AgentCard object with the following
    required fields:

    - name (str): The name of the agent
    - url (str): The URL of the agent
    - version (str): The version of the agent
    - capabilities (dict): The capabilities of the agent
    - default_input_modes (list[str]): Default input modes
    - default_output_modes (list[str]): Default output modes
    - skills (list): List of agent skills

    Example JSON file content:

        .. code-block:: json

            {
                "name": "RemoteAgent",
                "url": "http://localhost:8000",
                "description": "A remote A2A agent",
                "version": "1.0.0",
                "capabilities": {},
                "default_input_modes": ["text/plain"],
                "default_output_modes": ["text/plain"],
                "skills": []
            }

    """

    def __init__(
        self,
        file_path: str,
    ) -> None:
        """Initialize the FileAgentCardResolver with the path to the JSON file.

        Args:
            file_path (`str`):
                The path to the JSON file containing the agent card.
        """
        self._file_path = file_path

    async def get_agent_card(self) -> AgentCard:
        """Get the agent card from the JSON file.

        Returns:
            `AgentCard`:
                The agent card loaded from the file.
        """
        from a2a.types import AgentCard

        path = Path(self._file_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Agent card file not found: {self._file_path}",
            )

        if not path.is_file():
            raise ValueError(f"Path is not a file: {self._file_path}")

        with path.open("r", encoding="utf-8") as f:
            agent_json_data = json.load(f)
            return AgentCard.model_validate(agent_json_data)
