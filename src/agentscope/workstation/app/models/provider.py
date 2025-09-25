# -*- coding: utf-8 -*-
"""The provider related model"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Boolean, Integer
from sqlmodel import Field, SQLModel

from .field import formatted_datetime_field


class ProviderBase(SQLModel, table=True):
    """The provider base model"""

    __tablename__ = "provider"  # type: ignore

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    workspace_id: Optional[str] = Field(
        sa_column=Column(String(64), nullable=True),
        description="工作空间ID",
    )
    icon: Optional[str] = Field(
        sa_column=Column(String(255), nullable=True),
        description="图标",
    )
    name: Optional[str] = Field(
        sa_column=Column(String(255), nullable=True),
        description="名称",
    )
    description: Optional[str] = Field(
        sa_column=Column(String(1024), nullable=True),
        description="描述信息",
    )
    provider: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="提供商标识",
    )
    enable: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=True),
        description="是否启用",
    )
    source: str = Field(
        default="preset",
        sa_column=Column(String(64), nullable=False),
        description="来源-preset:预置,custom:自定义",
    )
    credential: Optional[str] = Field(
        sa_column=Column(String(1024), nullable=True),
        description="验权凭证，json",
    )
    supported_model_types: Optional[str] = Field(
        sa_column=Column(String(255), nullable=True),
        description="支持的模型类型，逗号分隔",
    )
    protocol: Optional[str] = Field(
        sa_column=Column(String(64), nullable=True),
        description="协议，默认openai",
    )
    gmt_create: datetime = formatted_datetime_field()
    gmt_modified: datetime = formatted_datetime_field()
    creator: Optional[str] = Field(
        sa_column=Column(String(64), nullable=True),
        description="创建者",
    )
    modifier: Optional[str] = Field(
        sa_column=Column(String(64), nullable=True),
        description="修改者",
    )
