# -*- coding: utf-8 -*-
"""The in-memory plan storage class."""
from ._plan_model import Plan
from .._logging import logger
from ._storage_base import PlanStorageBase


class InMemoryPlanStorage(PlanStorageBase):
    """In-memory plan storage."""

    def __init__(self) -> None:
        """Initialize the in-memory plan storage."""
        super().__init__()
        self.plans = []

    async def add_plan(self, plan: Plan) -> None:
        """Add a plan to the storage.

        Args:
            plan (`Plan`):
                The plan to be added.
        """
        self.plans.append(plan)

    async def delete_plan(self, plan_name: str) -> None:
        """Delete a plan from the storage.

        Args:
            plan_name (`str`):
                The name of the plan to be deleted.
        """
        index = None
        for i, plan in enumerate(self.plans):
            if plan.name == plan_name:
                index = i
                break

        if index is not None:
            self.plans.pop(index)
        else:
            logger.warning("Plan with name '%s' not found", plan_name)

    async def get_plans(self) -> list[Plan]:
        """Get all plans from the storage.

        Returns:
            `list[Plan]`:
                A list of all plans in the storage.
        """
        return self.plans
