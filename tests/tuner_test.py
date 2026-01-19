# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
# pylint: disable=too-many-statements
"""Unit tests for tuner related modules."""
from unittest.async_case import IsolatedAsyncioTestCase
from typing import Dict, Any

from agentscope.tuner import TunerModelConfig, WorkflowOutput, JudgeOutput
from agentscope.tuner._config import (
    check_judge_function,
    check_workflow_function,
)


async def correct_workflow_func(
    task: Dict,
    model: TunerModelConfig,
    auxiliary_models: Dict[str, TunerModelConfig],
) -> WorkflowOutput:
    """Correct interface matching the workflow type."""
    return WorkflowOutput(
        response="Test response",
    )


async def correct_workflow_func_no_aux(
    task: Dict,
    model: TunerModelConfig,
) -> WorkflowOutput:
    """Correct interface matching the workflow type without
    auxiliary models."""
    return WorkflowOutput(
        response="Test response",
    )


async def incorrect_workflow_func_1(task: Dict) -> WorkflowOutput:
    """Incorrect interface not matching the workflow type."""
    return WorkflowOutput(
        response="Test response",
    )


async def incorrect_workflow_func_2(
    task: Dict,
    model: TunerModelConfig,
    aux_model: int,
) -> WorkflowOutput:
    """Incorrect interface not matching the workflow type."""
    return WorkflowOutput(
        response="Test response",
    )


async def correct_judge_func(
    task: Dict,
    response: Any,
    auxiliary_models: Dict[str, TunerModelConfig],
) -> JudgeOutput:
    """Correct interface matching the judge type."""
    return JudgeOutput(
        reward=1.0,
    )


async def incorrect_judge_func_1(
    wrong_name: Dict,
    response: Any,
) -> JudgeOutput:
    """Incorrect interface not matching the judge type."""
    return JudgeOutput(
        reward=1.0,
    )


async def incorrect_judge_func_2(
    response: Any,
) -> JudgeOutput:
    """Incorrect interface not matching the judge type."""
    return JudgeOutput(
        reward=1.0,
    )


class TestTunerFunctionType(IsolatedAsyncioTestCase):
    """Test cases for tuner function type validation."""

    def test_validate_workflow_type(self) -> None:
        """Test workflow type validation."""
        # Correct cases
        check_workflow_function(correct_workflow_func)
        check_workflow_function(correct_workflow_func_no_aux)

        # Incorrect cases
        with self.assertRaises(ValueError):
            check_workflow_function(incorrect_workflow_func_1)
        with self.assertRaises(ValueError):
            check_workflow_function(incorrect_workflow_func_2)

        # Correct cases
        check_judge_function(correct_judge_func)

        # Incorrect cases
        with self.assertRaises(ValueError):
            check_judge_function(incorrect_judge_func_1)
        with self.assertRaises(ValueError):
            check_judge_function(incorrect_judge_func_2)


class TestDataset(IsolatedAsyncioTestCase):
    """Test cases for DatasetConfig."""

    async def test_preview(self) -> None:
        """Test preview method."""
        try:
            import datasets
        except ImportError:
            datasets = None
            self.skipTest("datasets library is not installed.")
        from agentscope.tuner import DatasetConfig
        from pathlib import Path
        import tempfile

        assert datasets is not None

        with tempfile.TemporaryDirectory() as tmpdirname:
            # generate a small dataset directory
            dataset_dir = Path(tmpdirname) / "my_dataset"
            dataset_dir.mkdir(parents=True, exist_ok=True)
            sample_file = dataset_dir / "train.jsonl"
            sample_content = [
                '{"question": "What is 2 + 2?", "answer": "4"}',
                '{"question": "What is 4 + 4?", "answer": "8"}',
                '{"question": "What is 8 + 8?", "answer": "16"}',
            ]
            with open(sample_file, "w", encoding="utf-8") as f:
                for line in sample_content:
                    f.write(line + "\n")

            dataset = DatasetConfig(path=str(dataset_dir), split="train")
            samples = dataset.preview(n=2)
            self.assertEqual(len(samples), 2)
            samples = dataset.preview(n=5)
            self.assertEqual(len(samples), 3)
            with self.assertRaises(OSError):
                invalid_ds = DatasetConfig(path="/invalid/path", split="train")
                invalid_ds.preview()
            with self.assertRaises(ValueError):
                invalid_ds = DatasetConfig(
                    path=str(dataset_dir),
                    split="invalid_split",
                )
                invalid_ds.preview()
