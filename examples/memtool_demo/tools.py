# -*- coding: utf-8 -*-
"""Custom tools used in the MemTool demo.

These complement AgentScope's built-in tools. Each tool returns a
ToolResponse and includes a docstring so AgentScope can generate a JSON schema.
"""
from __future__ import annotations

from typing import Iterable
import ast
import operator as op
from pathlib import Path

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock


def _safe_eval_arith(expr: str) -> float | int:
    """Safely evaluate a simple arithmetic expression.

    Supported operators: +, -, *, /, //, %, ** and parentheses.
    Disallows names, calls, attributes, and other unsafe nodes.
    """

    # Allowed operators
    operators: dict[type[ast.AST], callable] = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod,
        ast.Pow: op.pow,
        ast.USub: op.neg,
        ast.UAdd: op.pos,
    }

    def _eval(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Num):  # type: ignore[attr-defined]
            return node.n  # type: ignore[return-value]
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants are allowed")
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            left = _eval(node.left)
            right = _eval(node.right)
            return operators[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
            return operators[type(node.op)](_eval(node.operand))

        # Disallow everything else
        raise ValueError("Unsupported expression for safe arithmetic evaluation")

    tree = ast.parse(expr, mode="eval")
    return _eval(tree)


def calculate_expression(expression: str) -> ToolResponse:
    """Safely evaluate a basic arithmetic expression and return the result.

    Args:
        expression (str):
            An arithmetic expression composed of numbers, +, -, *, /, //, %, **,
            parentheses and whitespace, e.g., "2+2", "3 * (7 + 1)".
    """
    try:
        result = _safe_eval_arith(expression)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Result: {result}",
                ),
            ],
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: {e}",
                ),
            ],
        )


def search_in_file(path: str, query: str, encoding: str = "utf-8") -> ToolResponse:
    """Search for lines containing the query string in a text file.

    Args:
        path (str):
            File path to read.
        query (str):
            Keyword or substring to search for (case-insensitive).
        encoding (str, optional):
            File encoding, defaults to "utf-8".
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ToolResponse(
            content=[TextBlock(type="text", text=f"File not found: {path}")],
        )
    try:
        q = query.lower()
        matched: list[str] = []
        for i, line in enumerate(p.read_text(encoding=encoding).splitlines(), start=1):
            if q in line.lower():
                matched.append(f"{i}: {line}")
        text = (
            "No matches found." if not matched else "\n".join(matched[:200])
        )
        return ToolResponse(content=[TextBlock(type="text", text=text)])
    except Exception as e:  # pylint: disable=broad-exception-caught
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error reading file: {e}")],
        )

