# -*- coding: utf-8 -*-
"""Utility functions for A2UI agent integration."""
import json
from typing import Any

from a2a.types import (
    DataPart,
    TextPart,
    Message,
    Part,
)
from a2ui.a2ui_extension import A2UI_MIME_TYPE, MIME_TYPE_KEY

from agentscope._logging import logger


def check_a2ui_extension(request: Any = None) -> bool:
    """Check if a2ui extension is requested in the request.

    Args:
        request: The agent request object.

    Returns:
        True if a2ui extension is requested, False otherwise.
    """
    logger.info(request)
    return True
    # if not A2UI_AVAILABLE:
    #     return False

    # # Check if request has extensions attribute
    # if hasattr(request, "extensions") and request.extensions:
    #     return A2UI_EXTENSION_URI in request.extensions

    # # Check kwargs for extensions
    # # This is a fallback approach
    # return False


def transfer_ui_event_to_query(ui_event_part: dict) -> str:
    """Transfer UI event to a query string.

    Args:
        ui_event_part: A dictionary containing UI event information with
            actionName and context.

    Returns:
        A formatted query string based on the UI event action.
    """
    action = ui_event_part.get("actionName")
    ctx = ui_event_part.get("context", {})

    if action == "book_restaurant":
        restaurant_name = ctx.get("restaurantName", "Unknown Restaurant")
        address = ctx.get("address", "Address not provided")
        image_url = ctx.get("imageUrl", "")
        query = (
            f"USER_WANTS_TO_BOOK: {restaurant_name}, "
            f"Address: {address}, ImageURL: {image_url}"
        )
    elif action == "submit_booking":
        restaurant_name = ctx.get("restaurantName", "Unknown Restaurant")
        party_size = ctx.get("partySize", "Unknown Size")
        reservation_time = ctx.get("reservationTime", "Unknown Time")
        dietary_reqs = ctx.get("dietary", "None")
        image_url = ctx.get("imageUrl", "")
        query = (
            f"User submitted a booking for {restaurant_name} "
            f"for {party_size} people at {reservation_time} "
            f"with dietary requirements: {dietary_reqs}. "
            f"The image URL is {image_url}"
        )
    else:
        query = f"User submitted an event: {action} with data: {ctx}"

    return query


def pre_process_request_with_ui_event(message: Message) -> Any:
    """Pre-process the request.

    Args:
        message: The agent request object.

    Returns:
        The pre-processed request.
    """

    if message and message.parts:
        logger.info(
            "--- AGENT_EXECUTOR: Processing %s message parts ---",
            len(message.parts),
        )
        for i, part in enumerate(message.parts):
            if isinstance(part.root, DataPart):
                if "userAction" in part.root.data:
                    logger.info(
                        "  Part %s: Found a2ui UI ClientEvent payload: %s",
                        i,
                        json.dumps(part.root.data["userAction"], indent=4),
                    )
                    ui_event_part = part.root.data["userAction"]
                    message.parts[i] = Part(
                        root=TextPart(
                            text=transfer_ui_event_to_query(ui_event_part),
                        ),
                    )
    return message


def _find_json_end(json_string: str) -> int:
    """Find the end position of a JSON array or object.

    Finds the end by matching brackets/braces.

    Args:
        json_string: The JSON string to search.

    Returns:
        The end position (index + 1) of the JSON structure.
    """
    if json_string.startswith("["):
        # Find matching closing bracket
        bracket_count = 0
        for i, char in enumerate(json_string):
            if char == "[":
                bracket_count += 1
            elif char == "]":
                bracket_count -= 1
                if bracket_count == 0:
                    return i + 1
    elif json_string.startswith("{"):
        # Find matching closing brace
        brace_count = 0
        for i, char in enumerate(json_string):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    return i + 1
    return len(json_string)


def extract_ui_json_from_text(content_str: str) -> tuple[str, None]:
    """Extract the UI JSON from the text.

    Args:
        text: The text to extract the UI JSON from.

    Returns:
        The UI JSON.
    """
    text_content, json_string = content_str.split("---a2ui_JSON---", 1)
    json_data = None
    if json_string.strip():
        try:
            # Clean JSON string (remove markdown code blocks if present)
            json_string_cleaned = (
                json_string.strip().lstrip("```json").rstrip("```").strip()
            )

            # Find the end of JSON array/object by matching brackets/braces
            json_end = _find_json_end(json_string_cleaned)
            json_string_final = json_string_cleaned[:json_end].strip()
            # logger.info(f"final json string: {json_string_final}")
            json_data = json.loads(json_string_final)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse UI JSON: {e}")
            # On error, keep the JSON as text content
            return content_str, None
    return text_content, json_data


def post_process_a2a_message_for_ui(message: Message) -> Message:
    """Post-process the transferred A2A message.

    Args:
        message: The transferred A2A message.

    Returns:
        The post-processed A2A message.
    """
    new_parts = []
    for part in message.parts:
        if (
            isinstance(part.root, TextPart)
            and "---a2ui_JSON---" in part.root.text
        ):
            text_content, json_data = extract_ui_json_from_text(
                part.root.text,
            )
            if json_data:
                # Replace the part with a TextPart and multiple DataParts
                # with the same metadata for a2ui
                logger.info(
                    "found a2ui JSON in the message, with length: %s",
                    len(json_data),
                )
                new_parts.append(
                    Part(
                        root=TextPart(
                            text=text_content,
                        ),
                    ),
                )
                for item in json_data:
                    new_parts.append(
                        Part(
                            root=DataPart(
                                data=item,
                                metadata={MIME_TYPE_KEY: A2UI_MIME_TYPE},
                            ),
                        ),
                    )
            else:
                # Keep the original part if json_data is None
                new_parts.append(part)
        else:
            # Keep the original part if it doesn't contain the marker
            new_parts.append(part)

    message.parts = new_parts
    return message
