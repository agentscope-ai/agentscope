"""
优雅关停管理

提供资源注册和统一关停功能。
"""

import asyncio
from typing import Callable, List, Optional
from dataclasses import dataclass, field

from ..util.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ShutdownTask:
    """关停任务"""
    func: Callable
    name: str = ""
    priority: int = 0  # 优先级，数字越小越先执行


class GracefulShutdownRegistry:
    """优雅关停注册表"""

    _instance: Optional["GracefulShutdownRegistry"] = None

    def __new__(cls) -> "GracefulShutdownRegistry":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """初始化关停注册表"""
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._tasks: List[ShutdownTask] = []
        self._shutdown_lock = asyncio.Lock()

    def register(
        self,
        func: Callable,
        name: str = "",
        priority: int = 0,
    ) -> None:
        """注册关停任务

        Args:
            func: 关停函数（可以是协程或普通函数）
            name: 任务名称
            priority: 优先级（数字越小越先执行）
        """
        task = ShutdownTask(func=func, name=name or func.__name__, priority=priority)
        self._tasks.append(task)

        # 按优先级排序
        self._tasks.sort(key=lambda x: x.priority)

        logger.debug(f"注册关停任务: {task.name} (优先级: {priority})")

    async def run_all(self) -> None:
        """执行所有关停任务"""
        if not self._tasks:
            logger.info("没有注册关停任务")
            return

        logger.info(f"开始执行 {len(self._tasks)} 个关停任务...")

        async with self._shutdown_lock:
            for task in self._tasks:
                try:
                    logger.info(f"执行关停任务: {task.name}")

                    if asyncio.iscoroutinefunction(task.func):
                        await task.func()
                    else:
                        task.func()

                    logger.info(f"关停任务完成: {task.name}")

                except Exception as e:
                    logger.error(f"关停任务失败: {task.name}, 错误: {e}")

        logger.info("所有关停任务执行完成")

    def clear(self) -> None:
        """清空注册的任务"""
        self._tasks.clear()
        logger.debug("已清空所有关停任务")

    def get_task_count(self) -> int:
        """获取注册的任务数量"""
        return len(self._tasks)


# 全局注册表实例
_registry: Optional[GracefulShutdownRegistry] = None


def get_registry() -> GracefulShutdownRegistry:
    """获取全局关停注册表

    Returns:
        GracefulShutdownRegistry: 关停注册表实例
    """
    global _registry
    if _registry is None:
        _registry = GracefulShutdownRegistry()
    return _registry