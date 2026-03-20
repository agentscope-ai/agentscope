# -*- coding: utf-8 -*-
"""Built-in judge functions for model selection."""

from typing import Dict, Any
from .._judge import JudgeOutput


async def avg_time_judge(
    task: Dict[str, Any],
    response: Any,
) -> JudgeOutput:
    """
    Built-in judge function to calculate average time consumption of a model.
    This function returns a negative reward (since smaller time usage is better,
    making it a bigger-is-better metric), and includes the original metric
    in the metrics field.

    Args:
        task (`Dict[str, Any]`):
            The task information for the corresponding workflow.
        response (`Any`):
            A composite dict containing "response" (the workflow response)
            and "metrics" (workflow metrics including execution_time and usage).

    Returns:
        `JudgeOutput`:
            The negative time taken (making smaller time a bigger reward),
            and metrics containing the original time value.
    """
    # Extract execution time from the composite response dict
    time_taken = 0.0

    if not isinstance(response, dict):
        raise ValueError("Response must be a dict with 'response' and 'metrics' keys")

    metrics = response.get("metrics")
    if metrics is None:
        raise ValueError("Missing 'metrics' field in response")
    if "execution_time" not in metrics:
        raise ValueError("Missing 'execution_time' field in metrics")
    time_taken = metrics["execution_time"]

    # Return negative reward to make it bigger-is-better (smaller time = higher reward)
    reward = -time_taken

    return JudgeOutput(
        reward=reward,
        metrics={"avg_time_seconds": time_taken},
    )


async def avg_token_consumption_judge(
    task: Dict[str, Any],
    response: Any,
) -> JudgeOutput:
    """
    Built-in judge function to calculate average token consumption of a model.
    This function returns a negative reward (since smaller token usage is better,
    making it a bigger-is-better metric), and includes the original metric
    in the metrics field.

    Args:
        task (`Dict[str, Any]`):
            The task information for the corresponding workflow.
        response (`Any`):
            A composite dict containing "response" (the workflow response)
            and "metrics" (workflow metrics including execution_time and usage).
            Must include a 'metrics.usage' field.

    Returns:
        `JudgeOutput`:
            The negative token consumption (making smaller consumption a bigger reward),
            and metrics containing the original token consumption value.
    """
    original_reward = 0.0

    if not isinstance(response, dict):
        raise ValueError("Response must be a dict with 'response' and 'metrics' keys")

    metrics = response.get("metrics")
    if metrics is None or "usage" not in metrics:
        raise ValueError("Missing 'usage' field in response metrics")

    usage = metrics["usage"]
    if isinstance(usage, dict):
        if "total_tokens" in usage and usage["total_tokens"] is not None:
            original_reward = float(usage["total_tokens"])
        elif "output_tokens" in usage and usage["output_tokens"] is not None:
            original_reward = float(usage["output_tokens"])
        else:
            raise ValueError(
                "Neither 'total_tokens' nor 'output_tokens' found in usage field",
            )
    else:
        raise ValueError(
            "Usage field in response.metrics is not a dictionary",
        )

    # Return negative reward to make it bigger-is-better (smaller token usage = higher reward)
    reward = -original_reward

    return JudgeOutput(
        reward=reward,
        metrics={
            "token_consumed": original_reward,
        },
    )
