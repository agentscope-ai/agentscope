# -*- coding: utf-8 -*-
"""The MCP related model"""
from typing import Optional

from sqlalchemy import Text, Column, SmallInteger
from sqlmodel import SQLModel, Field
from .field import formatted_datetime_field


def name_field() -> Field:
    """The MCP name field"""
    return Field(min_length=1, max_length=64)


def description_field() -> Field:
    """The MCP description field"""
    return Field(min_length=0, max_length=1024)


class MCP(SQLModel, table=True):
    """The MCP model used to create MCP table"""

    __tablename__ = "mcp_server"  # type: ignore
    id: int = Field(default=None, primary_key=True)
    gmt_create: str = formatted_datetime_field()
    gmt_modified: str = formatted_datetime_field()
    server_code: str = Field(min_length=0, max_length=64, default="")
    name: str = name_field()
    description: str = description_field()
    source: str = Field(max_length=128, default="")
    deploy_env: str = Field(max_length=16, nullable=True)
    type: str = Field(default="customer", max_length=32)
    deploy_config: str = Field(
        sa_column=Column(Text, nullable=False, comment="deploy config"),
    )
    workspace_id: str = Field(max_length=64, nullable=True)
    account_id: str = Field(max_length=64, nullable=True)
    status: int = Field(
        sa_column=Column(
            SmallInteger,
            nullable=False,
            default=0,
            comment="Status as tinyint",
        ),
    )
    biz_type: str = Field(max_length=512, nullable=True)
    detail_config: str = Field(
        sa_column=Column(Text, nullable=True, comment="server config"),
    )
    host: str = Field(min_length=0, max_length=1024, default="", nullable=True)
    install_type: str = Field(nullable=True, max_length=32, default="")


class CreateMCPForm(SQLModel):
    """The MCP form used to create MCP"""

    name: str = name_field()
    description: str = description_field()
    deploy_config: str = Field(
        sa_column=Column(Text, nullable=False, comment="deploy config"),
    )
    detail_config: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True, comment="server config"),
    )
    install_type: Optional[str] = Field(default="")


class UpdateMCPForm(SQLModel):
    """The MCP form used to update MCP"""

    server_code: str = Field(min_length=0, max_length=64, default="")
    name: str = name_field()
    deploy_config: str = Field(
        sa_column=Column(Text, nullable=False, comment="deploy config"),
    )
    detail_config: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True, comment="server config"),
    )
    status: Optional[int] = Field(default=0)
    type: Optional[str] = Field(default="customer")
    description: Optional[str] = description_field()
    install_type: Optional[str] = Field(default="", nullable=True)
    source: Optional[str] = Field(default="", nullable=True)


class QueryByCodesForm(SQLModel):
    """The MCP form used to query MCP by codes"""

    server_codes: list[str] = Field(
        default_factory=list,
        min_items=0,
        max_items=64,
    )
    need_tools: bool = Field(default=False)


class DeBugToolsForm(SQLModel):
    """The MCP form used to run a specific mcp tool"""

    server_code: str = Field(min_length=0, max_length=64, default="")
    tool_name: str = Field(min_length=0, max_length=64, default="")
    tool_params: dict = Field(default={})
