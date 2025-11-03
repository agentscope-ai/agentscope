# -*- coding: utf-8 -*-
"""The in-memory plan storage class."""
from collections import OrderedDict

from ._plan_model import Plan
from ._storage_base import PlanStorageBase


class InMemoryPlanStorage(PlanStorageBase):
    """In-memory plan storage."""

    def __init__(self) -> None:
        """Initialize the in-memory plan storage."""
        super().__init__()
        self.plans = OrderedDict()

        # Support historical plan serialization and deserialization
        self.register_state(
            "plans",
            lambda plans: {k: v.model_dump() for k, v in plans.items()},
            lambda json_data: OrderedDict(
                (k, Plan.model_validate(v)) for k, v in json_data.items()
            ),
        )

    async def add_plan(self, plan: Plan, override: bool = True) -> None:
        """Add a plan to the storage.

        Args:
            plan (`Plan`):
                The plan to be added.
            override (`bool`, defaults to `True`):
                Whether to override the existing plan with the same ID.
        """
        if plan.id in self.plans and not override:
            raise ValueError(
                f"Plan with id {plan.id} already exists.",
            )
        self.plans[plan.id] = plan

    async def delete_plan(self, plan_id: str) -> None:
        """Delete a plan from the storage.

        Args:
            plan_id (`str`):
                The ID of the plan to be deleted.
        """
        self.plans.pop(plan_id, None)

    async def get_plans(self) -> list[Plan]:
        """Get all plans from the storage.

        Returns:
            `list[Plan]`:
                A list of all plans in the storage.
        """
        return list(self.plans.values())

    async def get_plan(self, plan_id: str) -> Plan | None:
        """Get a plan by its ID.

        Args:
            plan_id (`str`):
                The ID of the plan to be retrieved.

        Returns:
            `Plan | None`:
                The plan with the specified ID, or None if not found.
        """
        return self.plans.get(plan_id, None)
