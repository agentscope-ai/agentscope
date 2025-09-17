# -*- coding: utf-8 -*-
"""The view docx file tool in agentscope."""
import os
from typing import List, Tuple
from docx import Document

from ._response import ToolResponse
from ..message import TextBlock


def _validate_file(file_path: str) -> Tuple[bool, str]:
    """Validate the input file path.

    Args:
        file_path (`str`):
            The file path to validate.

    Returns:
        Tuple[bool, str]: A tuple containing:
            - bool: Whether the file is valid
            - str: Error message if invalid, empty string if valid
    """
    if not os.path.exists(file_path):
        return False, f"Error: The file {file_path} does not exist."

    if not os.path.isfile(file_path):
        return False, f"Error: The path {file_path} is not a file."

    if not file_path.lower().endswith(".docx"):
        return False, f"Error: The file {file_path} is not a .docx file."

    return True, ""


def _validate_range(
    paragraphs_range: List[int] | None,
    total_paragraphs: int,
) -> Tuple[bool, str, List[int] | None]:
    """Validate and process the paragraphs range.

    Args:
        paragraphs_range (`List[int] | None`):
            The range to validate, containing start and end indices.
        total_paragraphs (int):
            Total number of paragraphs in the document.

    Returns:
        Tuple[bool, str, List[int] | None]: A tuple containing:
            - bool: Whether the range is valid
            - str: Error message if invalid, empty string if valid
            - List[int] | None: Processed range if valid, None if invalid
    """
    if paragraphs_range is None:
        return True, "", None

    if len(paragraphs_range) != 2:
        return (
            False,
            "The paragraphs_range must contain exactly two numbers.",
            None,
        )

    start, end = paragraphs_range
    if not isinstance(start, int) or not isinstance(end, int):
        return False, "The paragraphs_range must contain integers.", None

    # Handle negative indices
    if start < 0:
        start = total_paragraphs + start
    if end < 0:
        end = total_paragraphs + end

    # Validate range
    if start < 0 or end < 0 or start > end or start >= total_paragraphs:
        return (
            False,
            f"Invalid paragraphs range: {paragraphs_range}. "
            f"File has {total_paragraphs} paragraphs.",
            None,
        )

    return True, "", [start, end]


def _create_error_response(error_message: str) -> ToolResponse:
    """Create an error response.

    Args:
        error_message (`str`):
            The error message to include in the response.

    Returns:
        ToolResponse: A response object containing the error message.
    """
    return ToolResponse(
        content=[TextBlock(type="text", text=error_message)],
    )


def _create_success_response(
    file_path: str,
    content: str,
    paragraphs_range: List[int] | None = None,
) -> ToolResponse:
    """Create a success response.

    Args:
        file_path (`str`): The path of the processed file.
        content (`str`): The extracted content from the file.
        paragraphs_range (`List[int] | None`, default to `None`):
            The range of paragraphs that were extracted.

    Returns:
        ToolResponse: A response object containing the formatted content.
    """
    if paragraphs_range is None:
        message = f"The content of {file_path}:\n```\n{content}```"
    else:
        message = (
            f"The content of {file_path} in paragraphs {paragraphs_range}:"
            f"\n```\n{content}```"
        )

    return ToolResponse(
        content=[TextBlock(type="text", text=message)],
    )


async def view_docx_file(
    file_path: str,
    paragraphs_range: List[int] | None = None,
) -> ToolResponse:
    """View the content of a docx file in the specified paragraph range.
    If `paragraphs_range` is not provided, the entire file will be returned.

    Args:
        file_path (str): The target docx file path.
        paragraphs_range (`List[int] | None`, defaults to `None`):
            The range of paragraphs to be viewed (e.g. paragraphs 1 to 10: [
            1, 10]), inclusive. If not provided, the entire file will be
            returned. To view the last 10 paragraphs, use [-10, -1].


    Returns:
        ToolResponse: The tool response containing either:
            - The file content with paragraph numbers if successful
            - An error message if any validation or processing fails
    """
    # Validate file
    is_valid, error_message = _validate_file(file_path)
    if not is_valid:
        return _create_error_response(error_message)

    try:
        # Read the docx file
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        # Validate and process range
        is_valid, error_message, processed_range = _validate_range(
            paragraphs_range,
            len(paragraphs),
        )
        if not is_valid:
            return _create_error_response(error_message)

        # Extract specified paragraphs if range is provided
        if processed_range:
            start, end = processed_range
            paragraphs = paragraphs[start : end + 1]

        # Format content with paragraph numbers
        content = "\n\n".join(
            f"[{i + 1}] {p}" for i, p in enumerate(paragraphs)
        )

        return _create_success_response(file_path, content, paragraphs_range)

    except Exception as e:
        return _create_error_response(f"Error reading the docx file: {str(e)}")
