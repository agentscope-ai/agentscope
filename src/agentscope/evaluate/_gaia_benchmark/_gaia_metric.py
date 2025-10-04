# -*- coding: utf-8 -*-
"""
GAIA benchmark metric implementations in AgentScope.

The code is implemented with reference to the `gaia-benchmark/leaderboard
<https://huggingface.co/spaces/gaia-benchmark/leaderboard/blob/main/scorer.py
>`_
"""

import re
import string
import warnings
from typing import Any

from .._solution import SolutionOutput
from .._metric_base import MetricBase, MetricResult, MetricType


def _normalize_number_str(number_str: str) -> float:
    """Normalize a string representation of a number."""
    # we replace these common units and commas to allow
    # conversion to float
    for char in ["$", "%", ","]:
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        print(f"String {number_str} cannot be normalized to number str.")
        return float("inf")


def _split_string(
    s: str,
    char_list: list[str] | None = None,
) -> list[str]:
    """Split a string by specified characters."""
    if char_list is None:
        char_list = [",", ";"]
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, s)


def _is_float(element: Any) -> bool:
    """Check if an element can be converted to float."""
    try:
        float(element)
        return True
    except ValueError:
        return False


def _normalize_str(input_str: str, remove_punct: bool = True) -> str:
    """
    Normalize a string by:
    - Removing all white spaces
    - Optionally removing punctuation (if remove_punct is True)
    - Converting to lowercase

    Parameters:
    - input_str: str, the string to normalize
    - remove_punct: bool, whether to remove punctuation (default: True)

    Returns:
    - str, the normalized string
    """
    # Remove all white spaces. Required e.g for seagull vs. sea gull
    no_spaces = re.sub(r"\s", "", input_str)

    # Remove punctuation, if specified.
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    else:
        return no_spaces.lower()


def _question_scorer(
    model_answer: str,
    ground_truth: str,
) -> bool:
    """Score a model answer against ground truth."""
    if model_answer is None:
        model_answer = "None"

    # if gt is a number
    if _is_float(ground_truth):
        print(f"Evaluating {model_answer} as a number.")
        normalized_answer = _normalize_number_str(model_answer)
        return normalized_answer == float(ground_truth)

    # if gt is a list
    elif any(char in ground_truth for char in [",", ";"]):
        print(f"Evaluating {model_answer} as a comma separated list.")
        # question with the fish: normalization removes punct

        gt_elems = _split_string(ground_truth)
        ma_elems = _split_string(model_answer)

        # check length is the same
        if len(gt_elems) != len(ma_elems):
            warnings.warn(
                "Answer lists have different lengths, returning False.",
                UserWarning,
            )
            return False

        # compare each element as float or str
        comparisons = []
        for ma_elem, gt_elem in zip(ma_elems, gt_elems):
            if _is_float(gt_elem):
                normalized_ma_elem = _normalize_number_str(ma_elem)
                comparisons.append(normalized_ma_elem == float(gt_elem))
            else:
                # we do not remove punct since comparisons can include punct
                comparisons.append(
                    _normalize_str(ma_elem, remove_punct=False)
                    == _normalize_str(gt_elem, remove_punct=False),
                )
        return all(comparisons)

    # if gt is a str
    else:
        print(f"Evaluating {model_answer} as a string.")
        return _normalize_str(model_answer) == _normalize_str(ground_truth)


class GAIAAccuracy(MetricBase):
    """The GAIA benchmark accuracy metric."""

    def __init__(self, ground_truth: str) -> None:
        """Initialize the GAIA benchmark accuracy metric."""
        super().__init__(
            name="accuracy",
            metric_type=MetricType.NUMERICAL,
            description="The GAIA benchmark accuracy metric that evaluates "
            "final answer correctness.",
        )
        self.ground_truth = ground_truth

    def __call__(
        self,
        solution: SolutionOutput,
    ) -> MetricResult:
        """Calculate the metric result by comparing the final answer with
        ground truth."""
        # Extract the model answer from solution output
        model_answer = (
            str(solution.output) if solution.output is not None else "None"
        )

        # Score the answer
        try:
            is_correct = _question_scorer(model_answer, self.ground_truth)
            return MetricResult(
                name=self.name,
                result=1.0 if is_correct else 0.0,
                message="Correct" if is_correct else "Incorrect",
            )
        except Exception as e:
            return MetricResult(
                name=self.name,
                result=0.0,
                message=f"Error during evaluation: {str(e)}",
            )
