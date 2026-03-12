# -*- coding: utf-8 -*-
"""
AgentLoop Integration Example - Load dataset from Agentloop dataset and write results to SLS
"""

import asyncio
import logging
from typing import Callable

from agentscope.evaluate import (
    AgentLoopConfig,
    RayEvaluator,
    SolutionOutput,
    Task,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============ HTTP Agent Solution ============
async def http_agent_solution(
    task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    pre_hook(task)

    output = ""  # call your agent and get the output

    return SolutionOutput(
        success=True,
        output=output,
        trajectory=[],
        meta={
            "task": task,
        },
    )


async def run_agentloop_experiment(
    # Dataset CMS configuration
    ak: str,
    sk: str,
    workspace: str,
    dataset: str,
    region_id: str,
    # Local storage
    result_dir: str,
) -> None:
    """Run evaluation using AgentLoop.

    Args:
        ak: Alibaba Cloud access key ID.
        sk: Alibaba Cloud access key secret.
        workspace: CMS workspace name where the dataset is located.
        dataset: CMS dataset name.
        region_id: Region ID for dataset (constructs CMS endpoint).
        result_dir: Local result storage directory.
    """
    logger.info("=" * 60)
    logger.info("Running AgentLoop Evaluation")
    logger.info("=" * 60)

    try:
        from agentscope.evaluate import (
            AgentLoopBenchmark,
            AgentLoopEvaluatorStorage,
        )
    except ImportError as e:
        logger.error("Import error: %s", e)
        logger.error(
            "Please install the required Agentloop SDKs:\n"
            "  pip install aliyun-log-python-sdk alibabacloud-cms20240330-inner")
        return
    # Create AgentLoopConfig for Benchmark
    agentloop_config = AgentLoopConfig(
        workspace=workspace,
        dataset=dataset,
        region_id=region_id,
        access_key_id=ak,
        access_key_secret=sk,
    )

    experiment_config = {"agent_name": "MockAgent"}

    # Load dataset from CMS Dataset
    logger.info("Loading dataset from CMS:")
    logger.info("  Workspace: %s", agentloop_config.workspace)
    logger.info("  Dataset:   %s", agentloop_config.dataset)
    logger.info("  Region ID: %s", agentloop_config.region_id)
    benchmark = AgentLoopBenchmark(
        config=agentloop_config,
        name="AgentLoop Benchmark",
        description=f"Benchmark from CMS: {workspace}/{dataset}",
    )
    logger.info("Loaded %d tasks", len(benchmark))

    # Configure storage
    logger.info("Using SLS to store results:")
    logger.info("  Result Workspace: %s", agentloop_config.workspace)
    logger.info("  Result Region ID: %s", agentloop_config.region_id)
    logger.info("Also saving locally to: %s", result_dir)
    storage = AgentLoopEvaluatorStorage(
        save_dir=result_dir,
        config=agentloop_config,
        experiment_name="AgentLoop Demo",
        experiment_type="agent",
        experiment_metadata={"run_env": "local_run"},
        experiment_config=experiment_config,
    )
    logger.info("  Project:      %s", storage.config.project)
    logger.info("  SLS Endpoint: %s", storage.config.sls_endpoint)
    logger.info("  Logstore:     %s", storage.logstore)
    logger.info("Experiment ID: %s", storage.experiment_id)

    # Run experiment
    evaluator = RayEvaluator(
        name="AgentLoop Evaluation",
        benchmark=benchmark,
        n_repeat=1,
        storage=storage,
        n_workers=4,
    )

    logger.info("Starting experiment...")
    await evaluator.run(http_agent_solution)
    logger.info("Experiment completed!")


async def main() -> None:
    """Main function"""
    await run_agentloop_experiment(
        ak="replace with your ak",
        sk="replace with your sk",
        # CMS dataset configuration
        workspace="replace with your workspace",
        dataset="replace with your dataset name",
        region_id="replace with your region_id",
        result_dir="./results",
    )


if __name__ == "__main__":
    asyncio.run(main())
