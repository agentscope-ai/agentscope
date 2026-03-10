# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""
A2UI Schema Viewer - Tool for viewing A2UI schema.

This script provides a way to retrieve the complete A2UI schema for
generating UI responses.

Usage:
    python view_a2ui_schema.py
"""

from schema import A2UI_SCHEMA


def view_a2ui_schema(schema_category: str = "BASE_SCHEMA") -> str:
    """
    View the complete A2UI schema for generating UI responses.

    This tool returns the complete A2UI JSON schema that defines all
    available UI components and message types.

    Args:
        schema_category: The category of the schema to view. Can be "BASE_SCHEMA".

    Returns:
        The complete A2UI schema as a string.
    """
    if schema_category == "BASE_SCHEMA":
        return f"""
## A2UI JSON Schema

The following is the complete A2UI schema for generating UI responses:

{A2UI_SCHEMA}

---
Use this schema to construct valid A2UI JSON responses.
"""
    else:
        raise ValueError(f"Invalid schema category: {schema_category}")


# Tool metadata for AgentScope registration
TOOL_METADATA = {
    "name": "view_a2ui_schema",
    "description": "View the complete A2UI schema for generating UI responses.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="View the complete A2UI schema for generating UI responses.",
    )
    parser.add_argument(
        "--schema_category",
        type=str,
        required=True,
        help="The category of the schema to view. Can be 'BASE_SCHEMA'.",
        choices=["BASE_SCHEMA"],
        default="BASE_SCHEMA",
    )
    args = parser.parse_args()

    res = view_a2ui_schema(schema_category=args.schema_category)
    print(res)
