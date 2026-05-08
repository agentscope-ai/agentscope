# -*- coding: utf-8 -*-
""""""
from pydantic import BaseModel

from ...agent import CompressionConfig, ReActConfig


class AgentConfigModel(BaseModel):
    """The agent ORM model."""

    name: str

    system_prompt: str

    compression_config: CompressionConfig

    react_config: ReActConfig

    default_model_config: ModelConfig

    default_toolkit_config: ToolkitConfig


class SessionConfigModel(BaseModel):
    """The session ORM model."""

    session_id: str

    mcp_disables: list[str]

    builtin_disables: list[str]

    skill_disables: list[str]



