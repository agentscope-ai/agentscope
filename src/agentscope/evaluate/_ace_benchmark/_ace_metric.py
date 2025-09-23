# -*- coding: utf-8 -*-
"""The ACE benchmark metric implementations in AgentScope."""

from .._solution import SolutionOutput
from .._metric_base import MetricBase, MetricResult, MetricType


class ACEProcessAccuracy(MetricBase):
    """The ace benchmark process accuracy metric."""

    def __init__(
        self,
        mile_stone: list[str],
    ) -> None:
        """Initialize the AceBench process accuracy metric."""
        super().__init__(
            name="process_accuracy",
            metric_type=MetricType.NUMERICAL,
            description="The AceBench Agent eval process accuracy metric.",
        )
        self.mile_stone = mile_stone

    async def __call__(
        self,
        solution: SolutionOutput,
    ) -> MetricResult:
        """Calculate the metric result."""

        # Turn the tool use block sequence into ACEBench format
        # e.g. func(arg1='dfd', arg2=44)
        gathered_trajectory = []
        for tool_call in solution.trajectory:
            if tool_call.get("type") == "tool_use":
                function_name = tool_call.get("name")
                kwargs = tool_call.get("input")

                gathered_kwargs = []
                for key, value in kwargs.items():
                    if isinstance(value, str):
                        gathered_kwargs.append(
                            f"{key}='{value}'",
                        )

                    else:
                        gathered_kwargs.append(
                            f"{key}={value}",
                        )

                kwargs_str = ", ".join(gathered_kwargs)
                gathered_trajectory.append(
                    f"[{function_name}({kwargs_str})]",
                )

        for stone in self.mile_stone:
            if stone not in gathered_trajectory:
                return MetricResult(
                    name=self.name,
                    result=0,
                    message=f"Error: Missing milestone '{stone}' in "
                    "the given trajectory.",
                )

        return MetricResult(
            name=self.name,
            result=1,
            message="Success",
        )


class ACEAccuracy(MetricBase):
    """The ace benchmark metric"""

    def __init__(
        self,
        state: list[dict],
    ) -> None:
        """Initialize the _metric object."""
        super().__init__(
            "accuracy",
            MetricType.NUMERICAL,
            "The AceBench Agent eval accuracy metric.",
        )
        self.state = state

    async def __call__(
        self,
        solution: SolutionOutput,
    ) -> MetricResult:
        """Calculate the metric result."""
        # Check if the solution matches the ground truth
        if not isinstance(solution.output, list):
            raise ValueError("Ground truth state must be a list.")

        # Handle the typos in ACEBench dataset
        gathered_state = {}
        for item in self.state:
            for key, value in item.items():
                if key.endswith("API"):
                    key = key.replace("API", "Api")
                elif key.endswith("rpi"):
                    key = key.replace("pi", "Api")
                gathered_state[key] = value

        gathered_output = {}
        for item in solution.output:
            for key, value in item.items():
                gathered_output[key] = value

        if not set(gathered_state.keys()).issubset(gathered_output.keys()):
            raise ValueError(
                "Missing keys in solution output compared to state, "
                f"ground truth keys: {gathered_state.keys()}, "
                f"solution keys: {gathered_output.keys()}",
            )

        for key, value in gathered_state.items():
            if value != gathered_output.get(key):
                return MetricResult(
                    name=self.name,
                    result=0,
                    message=(
                        f"Error: Mismatch in key '{key}':"
                        f"\n{value}\n{gathered_output.get(key)}"
                    ),
                )

        return MetricResult(
            name=self.name,
            result=1,
            message="Success: All keys match",
        )
