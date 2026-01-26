# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""
A2UI Example Viewer - Tool for viewing A2UI UI template examples.

This script provides a way to retrieve A2UI UI template examples.

Usage:
    # Load specific template name
    python view_a2ui_examples.py --template_name SINGLE_COLUMN_LIST_WITH_IMAGE
"""
from UI_templete_examples import (
    SINGLE_COLUMN_LIST_WITH_IMAGE_EXAMPLE,
    TWO_COLUMN_LIST_WITH_IMAGE_EXAMPLE,
    SIMPLE_LIST_EXAMPLE,
    BOOKING_FORM_WITH_IMAGE,
    SEARCH_FILTER_FORM_EXAMPLE,
    CONTACT_FORM_EXAMPLE,
    EMAIL_COMPOSE_FORM_EXAMPLE,
    SUCCESS_CONFIRMATION_WITH_IMAGE_EXAMPLE,
    ERROR_MESSAGE_EXAMPLE,
    INFO_MESSAGE_EXAMPLE,
    ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE,
    PROFILE_VIEW_WITH_IMAGE_EXAMPLE,
    SELECTION_CARD_EXAMPLE,
    MULTIPLE_SELECTION_CARDS_EXAMPLE,
)

# Template name to example mapping
TEMPLATE_MAP = {
    "SINGLE_COLUMN_LIST_WITH_IMAGE": SINGLE_COLUMN_LIST_WITH_IMAGE_EXAMPLE,
    "TWO_COLUMN_LIST_WITH_IMAGE": TWO_COLUMN_LIST_WITH_IMAGE_EXAMPLE,
    "SIMPLE_LIST": SIMPLE_LIST_EXAMPLE,
    "BOOKING_FORM_WITH_IMAGE": BOOKING_FORM_WITH_IMAGE,
    "SEARCH_FILTER_FORM_WITH_IMAGE": SEARCH_FILTER_FORM_EXAMPLE,
    "CONTACT_FORM_WITH_IMAGE": CONTACT_FORM_EXAMPLE,
    "EMAIL_COMPOSE_FORM_WITH_IMAGE": EMAIL_COMPOSE_FORM_EXAMPLE,
    "SUCCESS_CONFIRMATION_WITH_IMAGE": SUCCESS_CONFIRMATION_WITH_IMAGE_EXAMPLE,
    "ERROR_MESSAGE": ERROR_MESSAGE_EXAMPLE,
    "INFO_MESSAGE": INFO_MESSAGE_EXAMPLE,
    "ITEM_DETAIL_CARD": ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE,
    "ITEM_DETAIL_CARD_WITH_IMAGE": ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE,
    "PROFILE_VIEW": PROFILE_VIEW_WITH_IMAGE_EXAMPLE,
    "SELECTION_CARD": SELECTION_CARD_EXAMPLE,
    "MULTIPLE_SELECTION_CARDS": MULTIPLE_SELECTION_CARDS_EXAMPLE,
}


def view_a2ui_examples(template_name: str) -> str:
    """
    View A2UI UI template examples for generating UI responses.

    Args:
        template_name: Specific template name to load. Options:
                       - SINGLE_COLUMN_LIST_WITH_IMAGE, TWO_COLUMN_LIST_WITH_IMAGE,
                       SIMPLE_LIST,SELECTION_CARD,
                       MULTIPLE_SELECTION_CARDS,
                       BOOKING_FORM_WITH_IMAGE, SEARCH_FILTER_FORM_WITH_IMAGE,
                       CONTACT_FORM_WITH_IMAGE,
                       EMAIL_COMPOSE_FORM_WITH_IMAGE,SUCCESS_CONFIRMATION_WITH_IMAGE,
                       ERROR_MESSAGE, INFO_MESSAGE,
                       ITEM_DETAIL_CARD,
                       ITEM_DETAIL_CARD_WITH_IMAGE,
                       PROFILE_VIEW

    Returns:
        The requested template example.

    Examples:
        # Load specific template
        >>> view_a2ui_examples(template_name="BOOKING_FORM_WITH_IMAGE")
        >>> view_a2ui_examples(template_name="SINGLE_COLUMN_LIST_WITH_IMAGE")
    """
    if not template_name:
        raise ValueError("template_name is required and cannot be empty")

    if template_name not in TEMPLATE_MAP:
        raise ValueError(f"Unknown template name: {template_name}")

    example = TEMPLATE_MAP[template_name]

    return f"""
## A2UI Template: {template_name}

{example}

---
Adapt this template to your specific data and styling requirements.
"""


# Tool metadata for AgentScope registration
TOOL_METADATA = {
    "name": "view_a2ui_examples",
    "description": "View A2UI UI template examples for generating UI responses.",
    "parameters": {
        "type": "object",
        "properties": {
            "template_name": {
                "type": "string",
                "description": "Specific template name to load",
            },
        },
        "required": ["template_name"],
    },
}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="View A2UI UI template examples for generating UI responses.",
    )
    parser.add_argument(
        "--template_name",
        type=str,
        required=True,
        help="Specific template name to load",
    )

    args = parser.parse_args()

    res = view_a2ui_examples(template_name=args.template_name)
    print(res)
