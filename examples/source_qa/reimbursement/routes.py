# -*- coding: utf-8 -*-
"""Static sample of the reimbursement service routes."""
from fastapi import APIRouter

router = APIRouter()


@router.post("/expenses/submit")
def submit(claim: dict) -> None:
    """Submit a draft for approval."""
    if claim["status"] != "draft":
        raise ValueError("仅草稿可提交")
    claim["status"] = "pending"


@router.post("/expenses/approve")
def approve(claim: dict, actor: dict, tenant_config: dict) -> None:
    """Apply the reviewer's tenant-specific approval limit."""
    if actor["role"] != "reviewer":
        raise ValueError("需要审批人角色")
    if claim["amount"] > tenant_config["approval_limit"]:
        raise ValueError("超出当前租户审批额度")
    claim["status"] = "approved"


@router.post("/expenses/withdraw")
def withdraw(claim: dict, actor: dict) -> None:
    """Allow the applicant to withdraw a pending claim."""
    if claim["status"] != "pending":
        raise ValueError("仅待审批报销单可撤回")
    if claim["applicant_id"] != actor["id"]:
        raise ValueError("仅申请人可撤回")
    claim["status"] = "withdrawn"
