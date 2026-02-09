# -*- coding: utf-8 -*-
"""Packaging/Dependencies contract tests.

These tests ensure we don't regress dependency layering by relying on
transitive dependencies or accidentally changing extras boundaries.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

_SECTION_RE = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")
_OPTIONAL_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)\s*=\s*\[$")
_REQ_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")


def _strip_inline_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _collect_list(
    lines: list[str],
    start_index: int,
) -> tuple[list[str], int]:
    """Collect a TOML multi-line list starting at `start_index`."""
    items: list[str] = []
    i = start_index + 1
    while i < len(lines):
        raw = lines[i].strip()
        if raw.startswith("]"):
            return items, i + 1

        if not raw or raw.startswith("#"):
            i += 1
            continue

        raw = _strip_inline_comment(raw)
        if not raw:
            i += 1
            continue

        if raw.endswith(","):
            raw = raw[:-1].rstrip()

        try:
            value = ast.literal_eval(raw)
        except Exception as exc:  # pragma: no cover
            raise AssertionError(
                f"Failed to parse TOML list item: {raw!r}",
            ) from exc

        if not isinstance(value, str):
            raise AssertionError(
                f"Expected string list item, got: {type(value)!r}",
            )

        items.append(value)
        i += 1

    raise AssertionError("Unterminated list in pyproject.toml")


def _parse_pyproject(
    path: Path,
) -> tuple[list[str], dict[str, list[str]], set[str]]:
    """Return (base dependencies, optional deps, optional keys)."""
    lines = path.read_text(encoding="utf-8").splitlines()

    section: str | None = None
    base_deps: list[str] = []
    optional: dict[str, list[str]] = {}
    optional_keys: set[str] = set()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        sec_match = _SECTION_RE.match(stripped)
        if sec_match:
            section = sec_match.group("section").strip()
            i += 1
            continue

        if section == "project" and stripped.startswith("dependencies") and "[" in stripped:
            base_deps, i = _collect_list(lines, i)
            continue

        if section == "project.optional-dependencies":
            key_match = _OPTIONAL_KEY_RE.match(stripped)
            if key_match:
                key = key_match.group("key")
                optional_keys.add(key)
                optional[key], i = _collect_list(lines, i)
                continue

        i += 1

    if not base_deps:
        raise AssertionError(
            "Failed to parse [project].dependencies from pyproject.toml",
        )

    return base_deps, optional, optional_keys


def _req_name(req: str) -> str:
    """Best-effort extraction of distribution name from PEP 508 requirement."""
    head = req.split(";", 1)[0].strip()
    match = _REQ_NAME_RE.match(head)
    if not match:
        raise AssertionError(f"Unrecognized requirement: {req!r}")
    return match.group(0).lower()


def test_pyproject_optional_dependency_keys_are_full_and_dev() -> None:
    _, _, optional_keys = _parse_pyproject(PYPROJECT)
    assert optional_keys == {"full", "dev"}


def test_pyproject_base_dependencies_include_direct_imports() -> None:
    base_deps, _, _ = _parse_pyproject(PYPROJECT)
    names = {_req_name(_) for _ in base_deps}
    required = {"pydantic", "typing_extensions", "requests", "jsonschema"}
    missing = sorted(required - names)
    assert not missing, f"Missing base dependencies: {missing}"


def test_pyproject_full_includes_tqdm_and_milvus_marker() -> None:
    _, optional, _ = _parse_pyproject(PYPROJECT)
    full = optional.get("full", [])
    full_names = {_req_name(_) for _ in full}
    assert "tqdm" in full_names

    assert any(
        _req_name(_) == "pymilvus" and 'platform_system != "Windows"' in _
        for _ in full
    ), "Missing pymilvus milvus_lite entry with Windows marker"


def test_pyproject_dev_includes_full() -> None:
    _, optional, _ = _parse_pyproject(PYPROJECT)
    full = optional.get("full", [])
    dev = optional.get("dev", [])

    # Accept main-style "agentscope[full]" delegation.
    dev_lower = [_.lower() for _ in dev]
    if any(_.startswith("agentscope[full]") for _ in dev_lower):
        return

    full_names = {_req_name(_) for _ in full}
    dev_names = {_req_name(_) for _ in dev}

    missing = sorted(full_names - dev_names)
    assert not missing, f"dev is missing full requirements: {missing}"

