# -*- coding: utf-8 -*-
"""Reimbursement handlers with different paths and delegation rules."""
from collections.abc import Callable

from fastapi import APIRouter

from .review_rules import require_delegate as verify_delegate

router = APIRouter()


@router.post("/v2/expense/file")
def queue_sheet(sheet: dict) -> None:
    """Queue an expense sheet."""
    sheet["stage"] = "queued"


@router.post("/v2/expense/endorse")
def check_sheet(sheet: dict, check_tenant_policy: Callable) -> None:
    """Delegate approval limits to the tenant-supplied policy."""
    check_tenant_policy(sheet)
    sheet["stage"] = "checked"


@router.patch("/v2/expense/reopen")
def reopen_sheet(sheet: dict, reviewer: dict) -> None:
    """Return the sheet to the editor after checking delegate identity."""
    verify_delegate(sheet, reviewer)
    sheet["stage"] = "editing"
