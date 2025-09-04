# -*- coding: utf-8 -*-
"""The reminder API in ACEBench simulation tools."""
from datetime import datetime

from ._shared_state import SharedState


class ReminderApi(SharedState):
    """The reminder Api in the ACEBench evaluation."""

    tool_functions: list[str] = [
        "view_reminder_by_title",
        "add_reminder",
        "delete_reminder",
        "view_all_reminders",
        "mark_as_notified",
        "search_reminders",
    ]

    def __init__(self, share_state: dict) -> None:
        """Initialize the Reminder Api in the ACEBench evaluation."""
        super().__init__(share_state)

        self.max_capacity = 6
        self.reminder_list: dict[
            int,
            dict,
        ] = {
            1: {
                "reminder_id": 1001,
                "title": "Doctor's Appointment",
                "description": "Visit Dr. Smith for a checkup.",
                "time": "2024-07-15 09:30",
                "notified": False,
            },
            2: {
                "reminder_id": 1002,
                "title": "Team Meeting",
                "description": "Monthly project review with the team.",
                "time": "2024-07-17 11:00",
                "notified": False,
            },
            3: {
                "reminder_id": 1003,
                "title": "To-do list",
                "description": '首先帮Frank在"盒马生鲜"点外卖，'
                '需要定两个"生鲜大礼包"，再发短信告诉Frank：'
                '"购买商品的价格是()元"。要把括号换成实际金额，'
                "保留一位小数。",
                "time": "2024-07-16 11:00",
                "notified": False,
            },
        }
        self.reminder_id_counter: int = 3

    def get_state_dict(self) -> dict:
        """Get the current state dict of the ReminderApi."""
        return {
            "ReminderApi": {
                "reminder_list": self.reminder_list,
            },
        }

    def _check_capacity(self) -> bool:
        """检查备忘录容量是否已满。"""
        return len(self.reminder_list) >= self.max_capacity

    def view_reminder_by_title(
        self,
        title: str,
    ) -> dict[str, str | bool | dict[str, str | bool | datetime]]:
        """根据提醒的标题查看特定的提醒。

        Args:
            title (str): 提醒的标题。

        Returns:
            dict[str, str | bool | dict[str, str | bool | datetime]]:
                包含查找状态和提醒详情的字典。
        """
        if not self.logged_in:
            return {"status": False, "message": "device未登录，无法查看提醒"}
        for reminder in self.reminder_list.values():
            if reminder["title"] == title:
                return {"status": True, "reminder": reminder}

        return {"status": False, "message": f"没有找到标题为 '{title}' 的提醒"}

    def add_reminder(
        self,
        title: str,
        description: str,
        time: datetime,
    ) -> dict[str, bool | str]:
        """添加一个新的提醒。

        Args:
            title (str): 提醒标题。
            description (str): 提醒描述。
            time (datetime): 提醒时间, 一定遵循格式"YYYY-MM-DD HH:MM"。

        Returns:
            dict[str, bool | str]: 包含添加状态和结果的字典。
        """
        if not self.logged_in:
            return {
                "status": False,
                "message": "device未登录，无法添加一个新的提醒",
            }
        if self._check_capacity():
            return {"status": False, "message": "提醒容量已满，无法添加新的提醒"}

        self.reminder_id_counter += 1
        reminder_id = self.reminder_id_counter
        self.reminder_list[reminder_id] = {
            "reminder_id": reminder_id,
            "title": title,
            "description": description,
            "time": time,
            "notified": False,
        }
        return {"status": True, "message": f"提醒 '{title}' 已成功添加"}

    def delete_reminder(self, reminder_id: int) -> dict[str, bool | str]:
        """删除指定的提醒。

        Args:
            reminder_id (int): 要删除的提醒ID。

        Returns:
            dict[str, bool | str]: 包含删除状态和结果的字典。
        """
        if not self.logged_in:
            return {"status": False, "message": "device未登录，无法删除指定的提醒"}
        if reminder_id not in self.reminder_list:
            return {"status": False, "message": "提醒ID不存在"}

        del self.reminder_list[reminder_id]
        return {"status": True, "message": f"提醒ID {reminder_id} 已成功删除"}

    def view_all_reminders(
        self,
    ) -> dict:
        """查看所有的提醒。

        Returns:
            dict:
                包含所有提醒的字典列表。
        """
        if not self.reminder_list:
            return {"status": False, "message": "没有任何提醒"}

        reminders = []
        for reminder in self.reminder_list.values():
            reminders.append(
                {
                    "title": reminder["title"],
                    "description": reminder["description"],
                    "time": reminder["time"],
                    "notified": reminder["notified"],
                },
            )
        return {"status": True, "reminders": reminders}

    def mark_as_notified(
        self,
        reminder_id: int,
    ) -> dict[str, bool | str]:
        """标记提醒为已通知。

        Args:
            reminder_id (int): 要标记为已通知的提醒ID。

        Returns:
            dict[str, bool | str]:: 包含操作结果的字典。
        """
        if reminder_id not in self.reminder_list:
            return {"status": False, "message": "提醒ID不存在"}

        self.reminder_list[reminder_id]["notified"] = True
        return {"status": True, "message": f"提醒ID {reminder_id} 已标记为已通知"}

    def search_reminders(
        self,
        keyword: str,
    ) -> dict:
        """根据关键词搜索提醒。

        Args:
            keyword (str): 搜索关键词。

        Returns:
            `dict`:
                包含匹配提醒的字典列表。
        """
        matched_reminders = []

        for reminder in self.reminder_list.values():
            if (
                keyword.lower() in reminder["title"].lower()
                or keyword.lower() in reminder["description"].lower()
            ):
                matched_reminders.append(
                    {
                        "title": reminder["title"],
                        "description": reminder["description"],
                        "time": reminder["time"].strftime("%Y-%m-%d %H:%M"),
                    },
                )

        if not matched_reminders:
            return {"status": False, "message": "没有找到包含该关键词的提醒"}

        return {"status": True, "reminders": matched_reminders}
