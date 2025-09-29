# -*- coding: utf-8 -*-
"""The base class for plan storage."""
from abc import abstractmethod

from agentscope.module import StateModule
from agentscope.plan._plan_model import Plan


class PlanStorageBase(StateModule):
    """The base class for plan storage."""

    @abstractmethod
    async def add_plan(self, plan: Plan) -> None:
        """Add a plan to the storage."""

    @abstractmethod
    async def delete_plan(self, plan_id: str) -> None:
        """Delete a plan from the storage."""

    @abstractmethod
    async def get_plans(self) -> list[Plan]:
        """Get all plans from the storage."""

    @abstractmethod
    async def get_plan(self, plan_id: str) -> Plan | None:
        """Get a plan by its ID."""
