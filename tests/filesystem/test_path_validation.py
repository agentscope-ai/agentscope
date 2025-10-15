# -*- coding: utf-8 -*-
"""Path validation rules for the logical filesystem."""
import pytest

from agentscope.filesystem import validate_path
from agentscope.filesystem import InvalidPathError


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/a.txt",
        "/internal/logs/run-1.json",
        "/userinput/corpus.md",
    ],
)
def test_validate_path_accepts_absolute_noncontrol(path: str) -> None:
    assert validate_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "workspace/a.txt",  # missing leading slash
        "/workspace/../secret",  # traversal
        "/workspace/with*wildcard",  # wildcard
        "/workspace/with?query",  # query-like
        "/workspace/double//slash",  # collapsing
        "/bad\\windows",  # backslash
        "\x01/ctrl",  # control char
    ],
)
def test_validate_path_rejects_invalid_patterns(path: str) -> None:
    with pytest.raises(InvalidPathError):
        validate_path(path)


def test_validate_path_preserves_trailing_space() -> None:
    path = "/workspace/a "
    assert validate_path(path) == path
