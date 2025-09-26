# -*- coding: utf-8 -*-
"""The App model"""
from datetime import datetime
from enum import Enum, unique
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    TEXT,
)
from sqlmodel import SQLModel, Field

from app.models.base import IntEnum
from app.models.field import formatted_datetime_field


class CommonConstants:
    """General constant configuration class (not instantiable)"""

    __slots__ = ()  # Prohibited instantiation

    APP_INIT_VERSION: str = "1"


@unique
class AppComponentType(Enum):
    """Application component types"""

    AGENT = 1
    WORKFLOW = 2


@unique
class AppType(str, Enum):
    """Application types"""

    BASIC = "basic"
    WORKFLOW = "workflow"


@unique
class AppStatus(int, Enum):
    """Application status"""

    DELETED = 0
    DRAFT = 1
    PUBLISHED = 2
    PUBLISHED_EDITING = 3


class AppEntity(SQLModel, table=True):
    """Application entity"""

    __tablename__ = "application"  # type: ignore

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )

    workspace_id: Optional[str] = None
    app_id: str = None
    type: AppType = Field(
        sa_column=Column(
            String,
            nullable=False,
        ),
        description="application type",
    )  # The type remains the new AppType enumeration.
    status: AppStatus = Field(
        sa_column=Column(
            IntEnum(AppStatus),  # Use the Integer type directly
            nullable=False,
        ),
        description="version status",
    )
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    source: Optional[str] = None
    gmt_create: datetime = formatted_datetime_field()
    gmt_modified: datetime = formatted_datetime_field()
    creator: Optional[str] = None
    modifier: Optional[str] = None


class AppVersionEntity(SQLModel, table=True):
    """Application version entity"""

    __tablename__ = "application_version"  # type: ignore

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    workspace_id: str = Field(
        sa_column=Column("workspace_id", String, nullable=False),
    )
    app_id: str = Field(
        sa_column=Column("app_id", String, nullable=False),
    )
    status: AppStatus = Field(
        sa_column=Column(
            IntEnum(AppStatus),  # Use the Integer type directly
            nullable=False,
        ),
        description="version status",
    )
    config: str = Field(sa_column=Column(TEXT))
    version: Optional[str] = None
    description: Optional[str] = None
    gmt_create: datetime = formatted_datetime_field()
    gmt_modified: datetime = formatted_datetime_field()
    creator: Optional[str] = None
    modifier: Optional[str] = None
