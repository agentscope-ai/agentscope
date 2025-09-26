# -*- coding: utf-8 -*-
"""Utils for model"""


def _verify_text_content_in_openai_delta_response(response: dict) -> bool:
    """Verify if the text content exists in the openai streaming response

    Args:
        response (`dict`):
            The JSON-format OpenAI response (After calling `model_dump`
             function)

    Returns:
        `bool`: If the text content exists
    """

    if len(response.get("choices", [])) == 0:
        return False

    if response["choices"][0].get("delta", None) is None:
        return False

    if response["choices"][0]["delta"].get("content", None) is None:
        return False

    return True


def _verify_text_content_in_openai_message_response(
    response: dict,
    allow_content_none: bool = False,
) -> bool:
    """Verify if the text content exists in the openai streaming response

    Args:
        response (`dict`):
            The JSON-format OpenAI response (After calling `model_dump`
             function)
        allow_content_none (`bool`, defaults to `False`):
            If the content can be `None`

    Returns:
        `bool`: If the text content exists
    """

    if len(response.get("choices", [])) == 0:
        return False

    if response["choices"][0].get("message", None) is None:
        return False

    if not allow_content_none:
        if response["choices"][0]["message"].get("content", None) is None:
            return False

    return True
