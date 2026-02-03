# -*- coding: utf-8 -*-
"""Model selection module for selecting the best performing model from candidates based on evaluation metrics."""

from ._model_selection import select_model
from ._built_in_judges import avg_time_judge, avg_token_consumption_judge

__all__ = ["select_model", "avg_time_judge", "avg_token_consumption_judge"]
