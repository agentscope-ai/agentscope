"""
基础数据模型

定义通用的基础模型和混入类。
"""

from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, validator


# 使用Pydantic V2的BaseModel
class BaseModel(PydanticBaseModel):
    """基础模型类"""

    class Config:
        """Pydantic配置"""
        from_attributes = True  # 支持从ORM对象创建
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
            UUID: lambda v: str(v) if v else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，排除None值"""
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseModel":
        """从字典创建实例"""
        return cls.model_validate(data)


class IDMixin(BaseModel):
    """ID混入类"""
    id: UUID = Field(default_factory=UUID, description="唯一标识符")


class TimestampMixin(BaseModel):
    """时间戳混入类"""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间"
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="更新时间"
    )

    @validator("updated_at", pre=True, always=True)
    def update_timestamp(cls, v: Optional[datetime], values: dict[str, Any]) -> datetime:
        """自动更新时间戳"""
        return datetime.now(timezone.utc)


T = TypeVar("T")


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码")
    size: int = Field(default=20, ge=1, le=100, description="每页大小")

    @property
    def offset(self) -> int:
        """计算偏移量"""
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        """获取限制数量"""
        return self.size


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应"""
    items: list[T] = Field(description="数据项列表")
    total: int = Field(description="总数量")
    page: int = Field(description="当前页码")
    size: int = Field(description="每页大小")
    pages: int = Field(description="总页数")

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        pagination: PaginationParams,
    ) -> "PaginatedResponse[T]":
        """创建分页响应"""
        pages = (total + pagination.size - 1) // pagination.size

        return cls(
            items=items,
            total=total,
            page=pagination.page,
            size=pagination.size,
            pages=pages,
        )