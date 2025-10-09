# -*- coding: utf-8 -*-
"""The workflow related model"""
from datetime import datetime
from typing import Optional, List

from sqlalchemy import Column, Integer, String
from sqlmodel import SQLModel, Field, JSON
from .field import formatted_datetime_field


def description_field() -> Field:
    """The MCP description field"""
    return Field(min_length=0, max_length=200)


class WorkflowRuntime(SQLModel, table=True):
    """The workflow model used to create workflow table"""

    __tablename__ = "workflow_runtime"  # type: ignore
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    app_id: str = Field(
        sa_column=Column("app_id", String(255), nullable=False),
    )
    session_id: Optional[str] = Field(
        sa_column=Column("session_id", String(255), nullable=True),
    )
    version: Optional[str] = Field(default="latest")
    task_id: Optional[str] = Field()
    name: Optional[str] = Field()
    description: Optional[str] = Field()
    result: dict = Field(default={}, sa_type=JSON)
    gmt_create: datetime = formatted_datetime_field()
    account_id: str = Field()
    gmt_modified: datetime = formatted_datetime_field()
    inputs: Optional[list[dict]] = Field(
        default=[],
        sa_type=JSON,
        nullable=False,
    )


class CommonParam(SQLModel):
    """The common param model"""

    key: str
    value: str


class TaskPartGraphRequest(SQLModel):
    """The task part graph request model"""

    app_id: str = Field(..., alias="app_id")
    nodes: List[dict] = Field(default_factory=list)
    edges: List[dict] = Field(default_factory=list)
    input_params: List = Field(default_factory=list, alias="input_params")

    class Config:
        """Config"""

        allow_population_by_field_name = True
