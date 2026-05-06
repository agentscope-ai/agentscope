# -*- coding: utf-8 -*-
""""""

from fastapi import FastAPI, APIRouter
from fastapi.middleware import Middleware


def create_app(
    routers: list[APIRouter],
    middlewares: list[Middleware],
) -> FastAPI:
    """A factory function that creates a FastAPI application with the given
    components.
    """
