# -*- coding: utf-8 -*-
"""
AgentLoop Integration Example - Load dataset from SLS and write results to SLS

Before running, set the following environment variables:
    # Dataset SLS configuration
    export SLS_DATASET_ENDPOINT="cn-hangzhou.log.aliyuncs.com"
    export SLS_DATASET_PROJECT="your-dataset-project"
    export SLS_DATASET_LOGSTORE="your-dataset-logstore"

    # Result upload SLS configuration (can be different from dataset)
    export SLS_RESULT_ENDPOINT="cn-shanghai.log.aliyuncs.com"
    export SLS_RESULT_PROJECT="your-result-project"
    export SLS_RESULT_LOGSTORE="experiment_detail"

    # Alibaba Cloud credentials
    export ALIBABA_CLOUD_ACCESS_KEY_ID="your-access-key-id"
    export ALIBABA_CLOUD_ACCESS_KEY_SECRET="your-access-key-secret"

How to run:
cd  /Users/wu.cc/Code/python/agentscope/examples/evaluation/agentloop_demo
PYTHONPATH=/Users/wu.cc/Code/python/agentscope/src python main.py

If you don't have a real SLS environment, use --mock parameter for mock mode:
    python main.py --mock
"""
import asyncio
import uuid
from typing import Callable

from agentscope.evaluate import (
    Task,
    SolutionOutput,
    RayEvaluator,
    AgentLoopConfig,
)


# ============ HTTP Agent Solution ============
async def http_agent_solution(
    task: Task,
    pre_hook: Callable,
) -> SolutionOutput:
    pre_hook(task)

    output = "" # call your agent and get the output

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
    """Run evaluation using AgentLoop

    Args:
        workspace: CMS workspace name where the dataset is located
        dataset: CMS dataset name
        region_id: Region ID for dataset (used to construct CMS endpoint)
        result_workspace: CMS workspace name for result upload
        result_region_id: Region ID for result upload (used to construct CMS/SLS endpoint)
        result_dir: Local result storage directory
    """
    print("=" * 60)
    print("Running AgentLoop Evaluation")
    print("=" * 60)

    try:
        from agentscope.evaluate import (
            AgentLoopBenchmark,
            AgentLoopEvaluatorStorage,
        )
    except ImportError as e:
        print(f"Import error: {e}")
        print("Please install aliyun-log-python-sdk: pip install aliyun-log-python-sdk")
        return
    exp_id = str(uuid.uuid4())

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
    print(f"\nLoading dataset from CMS:")
    print(f"  Workspace: {agentloop_config.workspace}")
    print(f"  Dataset:   {agentloop_config.dataset}")
    print(f"  Region ID: {agentloop_config.region_id}")
    benchmark = AgentLoopBenchmark(
        config=agentloop_config,
        name="AgentLoop Benchmark",
        description=f"Benchmark from CMS: {workspace}/{dataset}",
    )
    print(f"Loaded {len(benchmark)} tasks")

    # Configure storage
    print(f"\nUsing SLS to store results:")
    print(f"  Result Workspace: {agentloop_config.workspace}")
    print(f"  Result Region ID: {agentloop_config.region_id}")
    print(f"Also saving locally to: {result_dir}")
    storage = AgentLoopEvaluatorStorage(
        save_dir=result_dir,
        config=agentloop_config,
        experiment_name="AgentLoop Demo",
        experiment_type="agent",
        experiment_metadata={"run_env": "local_run"},
        experiment_config=experiment_config,
    )
    print(f"  Project:  {storage.config.project}")
    print(f"  SLS Endpoint: {storage.config.sls_endpoint}")
    print(f"  Logstore: {storage.logstore}")
    print(f"Experiment ID: {storage.experiment_id}")

    # Run experiment
    evaluator = RayEvaluator(
        name="AgentLoop Evaluation",
        benchmark=benchmark,
        n_repeat=1,
        storage=storage,
        n_workers=4,
    )

    print("\nStarting experiment...")
    await evaluator.run(http_agent_solution)
    print("\nExperiment completed!")


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
