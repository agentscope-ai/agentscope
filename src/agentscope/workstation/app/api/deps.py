# -*- coding: utf-8 -*-
"""deps"""
from typing import Annotated, Optional

from fastapi import Depends, Query
from fastapi.security import HTTPBearer
from sqlmodel import Session

from app.db.init_db import get_session
from app.exceptions.base import PermissionDeniedException
from app.models.account import Account
from app.schemas.app import AppQuery
from app.schemas.base import BaseQuery
from app.services.auth_service import AuthService

http_bearer = HTTPBearer()


def get_token(token: Annotated[str, Depends(http_bearer)]) -> str:
    """get token"""
    return token.credentials


SessionDep = Annotated[Session, Depends(get_session)]
TokenDep = Annotated[str, Depends(get_token)]


def get_current_account(session: SessionDep, token: TokenDep) -> Account:
    """get current account"""
    return AuthService(session=session).get_account_by_token(token=token)


CurrentAccount = Annotated[Account, Depends(get_current_account)]


def get_current_active_super_account(
    current_account: CurrentAccount,
) -> Account:
    """get current active super account"""
    if current_account.type == "admin":
        return current_account
    else:
        raise PermissionDeniedException(
            extra_info={"account_id": current_account.id},
        )


CurrentSuperAccount = Annotated[
    Account,
    Depends(get_current_active_super_account),
]


def base_query_params(
    name: Optional[str] = Query(None),
    current: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    type_: Optional[str] = Query(None),
) -> BaseQuery:
    """Preprocess query parameters"""
    return BaseQuery(
        **{
            "name": name,
            "current": current,
            "size": size,
            "status": int(status) if status and status.strip() else None,
            "type": type_,
        },
    )


def app_query_params(
    name: Optional[str] = Query(None),
    current: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),  # Receive raw string
    app_id: Optional[str] = Query(None),
    type_: Optional[str] = Query(None),
) -> AppQuery:
    """Preprocess query parameters"""
    return AppQuery(
        **{
            "name": name,
            "current": current,
            "size": size,
            "status": int(status) if status and status.strip() else None,
            "app_id": app_id,
            "type": type_,
        },
    )


BaseQueryDeps = Annotated[BaseQuery, Depends(base_query_params)]

AppQueryDeps = Annotated[AppQuery, Depends(app_query_params)]


def get_workspace_id() -> str:
    """Get workspace id"""
    return "1"
