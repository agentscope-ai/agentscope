"""
异常定义

定义系统中使用的各种异常类型。
"""

from typing import Any, Optional


class BaseError(Exception):
    """基础异常类"""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "error": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class NotFoundError(BaseError):
    """资源未找到异常"""
    pass


class ValidationError(BaseError):
    """验证异常"""
    pass


class DatabaseError(BaseError):
    """数据库异常"""
    pass


class ConfigError(BaseError):
    """配置异常"""
    pass


class PromptError(BaseError):
    """Prompt异常"""
    pass


class KnowledgeError(BaseError):
    """知识异常"""
    pass


class PKMError(BaseError):
    """PKM异常"""
    pass


class AgentError(BaseError):
    """智能体异常"""
    pass


class AuthenticationError(BaseError):
    """认证异常"""
    pass


class AuthorizationError(BaseError):
    """授权异常"""
    pass


class ExternalServiceError(BaseError):
    """外部服务异常"""
    pass


class BusinessLogicError(BaseError):
    """业务逻辑异常"""
    pass