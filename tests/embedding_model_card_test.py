# -*- coding: utf-8 -*-
"""Tests for embedding model card schema merging."""
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from agentscope.embedding import EmbeddingModelCard


class _SchemaParameters(BaseModel):
    """Parameter model with optional and multi-branch union fields."""

    optional_text: str | None = None
    scalar_union: int | str | None = None


def _write_card(tmpdir: str, parameter_overrides: str) -> str:
    """Write a minimal embedding model card."""
    path = Path(tmpdir) / "embedding-card.yaml"
    path.write_text(
        f"""
name: custom-embedding
label: Custom Embedding
dimensions: 3
parameter_overrides:
{parameter_overrides}
""",
        encoding="utf-8",
    )
    return str(path)


def test_optional_schema_flattens_when_default_is_overridden() -> None:
    """Optional single-branch schemas are flattened for UI defaults."""
    with TemporaryDirectory() as tmpdir:
        card = EmbeddingModelCard.from_yaml(
            _write_card(
                tmpdir,
                """
  optional_text:
    default: custom
""",
            ),
            _SchemaParameters,
        )

    schema = card.parameter_schema["properties"]["optional_text"]
    assert schema["type"] == "string"
    assert "anyOf" not in schema
    assert schema["default"] == "custom"


def test_multi_branch_anyof_schema_is_preserved_with_default() -> None:
    """Multi-type unions keep all valid branches after YAML overrides."""
    with TemporaryDirectory() as tmpdir:
        card = EmbeddingModelCard.from_yaml(
            _write_card(
                tmpdir,
                """
  scalar_union:
    default: 7
""",
            ),
            _SchemaParameters,
        )

    schema = card.parameter_schema["properties"]["scalar_union"]
    branch_types = {branch["type"] for branch in schema["anyOf"]}
    assert branch_types == {"integer", "string", "null"}
    assert schema["default"] == 7
