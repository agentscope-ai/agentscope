# -*- coding: utf-8 -*-
"""Local policy from the alternative reimbursement application."""


def require_delegate(sheet: dict, reviewer: dict) -> None:
    """Allow reopening only for the assigned delegate."""
    if sheet["delegate"] != reviewer["uid"]:
        raise ValueError("需要指定的复核经办人")
