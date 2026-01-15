# -*- coding: utf-8 -*-
"""Utility functions for A2UI agent integration."""
import json
from typing import Any
from pydantic import BaseModel
from pydantic import Field

from a2a.types import (
    DataPart,
    TextPart,
    Message,
    Part,
)
from a2ui.a2ui_extension import (
    A2UI_MIME_TYPE,
    MIME_TYPE_KEY,
    A2UI_EXTENSION_URI,
)

from agentscope._logging import logger


class A2UIResponse(BaseModel):
    """Response model for A2UI formatted output."""

    response_with_a2ui: str = Field(
        description="The response with A2UI JSON",
    )


A2UI_SKILL_INSTRUCTION = """<system-info>
You are equipped with the A2UI_response_generator skill for generating rich,
interactive UI responses.

**CRITICAL: Before generating any A2UI response, you MUST:**
1. First, read the SKILL.md file from the skill directory using
   `view_text_file` to understand the skill
2. Then, execute `view_a2ui_schema.py` using `execute_python_code` to load
   the A2UI schema
3. Then, execute `view_a2ui_examples.py` using `execute_python_code` to load
   relevant UI templates based on your task type (list, form, confirmation,
   detail view)
4. Only after loading the schema and examples, generate your response
   following the format rules

**You MUST NOT skip these steps. All A2UI knowledge MUST come from the
skill files.**
For detailed instructions, refer to the SKILL.md file in the skill
directory.
</system-info>
"""
A2UI_SKILL_TEMPLATE_MINIMAL = "- {name}({dir}): {description}"


def check_a2ui_extension(*args: Any) -> bool:
    """Check if a2ui extension is requested in the request context.

    Args:
        *args: Variable arguments that may contain ServerCallContext as the
            first element.

    Returns:
        True if a2ui extension is requested and activated, False otherwise.
    """
    # Extract context from args (ServerCallContext is typically the first
    # element)
    if not args or len(args) == 0:
        logger.warning("check_a2ui_extension: No context provided in args")
        return False

    context = args[0]

    # Check if context has requested_extensions attribute
    if not hasattr(context, "requested_extensions"):
        logger.warning(
            "check_a2ui_extension: Context does not have "
            "requested_extensions attribute",
        )
        return False

    # Check if A2UI extension is requested
    if A2UI_EXTENSION_URI in context.requested_extensions:
        # Activate the extension if add_activated_extension method exists
        if hasattr(context, "add_activated_extension"):
            context.add_activated_extension(A2UI_EXTENSION_URI)
            logger.info("A2UI extension activated: %s", A2UI_EXTENSION_URI)
        else:
            logger.warning(
                "check_a2ui_extension: Context does not have "
                "add_activated_extension method",
            )
        return True

    return False


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

    if action in ["book_restaurant", "select_item"]:
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
            json_data = json.loads(json_string_final)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse UI JSON: %s", e)
            # On error, keep the JSON as text content
            return content_str, None
    return text_content, json_data


def check_a2ui_json_in_message(
    part: Part,
    is_final: bool,
) -> tuple[bool, str | None]:
    """Check if the message contains A2UI JSON.

    Args:
        message: The message to check.

    Returns:
        A tuple containing a boolean indicating if A2UI JSON is found and
        the A2UI JSON string if found.
    """
    # for the case when ReActAgent max iters is reached, the message will be
    # the last complete message, with text message.
    if (
        isinstance(part.root, TextPart)
        and "---a2ui_JSON---" in part.root.text
        and is_final
    ):
        logger.info(
            "--- Found A2UI JSON in the message: %s ---",
            part.root.text,
        )
        return True, part.root.text

    # for the case when ReActAgent max iters is not reached, if it contains
    # tool use block with name "generate_response" and type "tool_use", and
    # the response_with_a2ui contains "---a2ui_JSON---", then return True,
    # response_with_a2ui.
    if (
        isinstance(part.root, DataPart)
        and part.root.data.get("name") == "generate_response"
        and part.root.data.get("type") == "tool_use"
        and not is_final
    ):
        input_data = part.root.data.get("input")
        if input_data and isinstance(input_data, dict):
            response_with_a2ui = input_data.get("response_with_a2ui")
            if response_with_a2ui and "---a2ui_JSON---" in response_with_a2ui:
                return True, response_with_a2ui
    return False, None


def post_process_a2a_message_for_ui(
    message: Message,
    is_final: bool,
) -> Message:
    """Post-process the transferred A2A message.

    Args:
        message: The transferred A2A message.

    Returns:
        The post-processed A2A message.
    """
    new_parts = []
    for part in message.parts:
        has_a2ui_json, a2ui_json_string = check_a2ui_json_in_message(
            part,
            is_final=is_final,
        )
        logger.info(
            "message: %s --- has_a2ui_json: %s ---",
            message,
            has_a2ui_json,
        )
        if has_a2ui_json and a2ui_json_string is not None:
            text_content, json_data = extract_ui_json_from_text(
                a2ui_json_string,
            )
            if json_data:
                # Replace the part with a TextPart and multiple DataParts
                # with the same metadata for a2ui
                try:
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

                except Exception as e:
                    logger.error(
                        "Error processing a2ui JSON parts: %s",
                        e,
                        exc_info=True,
                    )
                    raise
        else:
            # Keep the original part if it doesn't contain the marker
            if isinstance(part.root, DataPart):
                if (
                    part.root.data.get("name") == "generate_response"
                    and part.root.data.get("type") == "tool_result"
                ):
                    continue
            new_parts.append(part)

    message.parts = new_parts
    return message
