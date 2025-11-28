# -*- coding: utf-8 -*-
"""The formatter module."""

from abc import abstractmethod
from typing import Any, List, Tuple, Sequence

from .._utils._common import _save_base64_data, _generate_unique_identifier
from ..message import Msg, AudioBlock, ImageBlock, TextBlock, VideoBlock


class FormatterBase:
    """The base class for formatters."""

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """Assert that the input is a list of Msg objects.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be validated.
        """
        if not isinstance(msgs, list):
            raise TypeError("Input must be a list of Msg objects.")

        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"Expected Msg object, got {type(msg)} instead.",
                )

    # pylint: disable=too-many-branches
    @staticmethod
    def convert_tool_result_to_string(
        output: str | List[TextBlock | ImageBlock | AudioBlock | VideoBlock],
        promote_tool_result_images: bool = False,
        promote_tool_result_audios: bool = False,
        promote_tool_result_videos: bool = False,
        save_locally: bool = False,
    ) -> tuple[
        str,
        Sequence[
            Tuple[
                str,
                ImageBlock | AudioBlock | TextBlock | VideoBlock,
            ]
        ],
    ]:
        """Turn the tool result list into a textual output to be compatible
        with the LLM API that doesn't support multimodal data in the tool
        result.

        For URL-based images, the URL is included in the list. For
        base64-encoded images, by default the local file path where the image
        is saved is included in the returned list. However, if the
        corresponding promote parameter is True (e.g.,
        `promote_tool_result_images=True`),
        a unique identifier string will be returned instead of saving the file
        locally.

        Args:
            output (`str | List[TextBlock | ImageBlock | AudioBlock | \
            VideoBlock]`):
                The output of the tool response, including text and multimodal
                data like images and audio.
            promote_tool_result_images (`bool`, optional):
                If True, base64-encoded images will not be saved locally but
                instead return a unique identifier. Defaults to False.
            promote_tool_result_audios (`bool`, optional):
                If True, base64-encoded audios will not be saved locally but
                instead return a unique identifier. Defaults to False.
            promote_tool_result_videos (`bool`, optional):
                If True, base64-encoded videos will not be saved locally but
                instead return a unique identifier. Defaults to False.
            save_locally (`bool`, default to False):
                Whether saved base64 data.

        Returns:
            `tuple[str, list[Tuple[str, ImageBlock | AudioBlock | VideoBlock \
            TextBlock]]]`:
                A tuple containing the textual representation of the tool
                result and a list of tuples. The first element of each tuple
                is the local file path, URL, or unique identifier of the
                multimodal data, and the second element is the corresponding
                block.
        """

        if isinstance(output, str):
            return output, []

        textual_output = []
        multimodal_data = []
        for block in output:
            assert isinstance(block, dict) and "type" in block, (
                f"Invalid block: {block}, a TextBlock, ImageBlock, "
                f"AudioBlock, or VideoBlock is expected."
            )
            if block["type"] == "text":
                textual_output.append(block["text"])

            elif block["type"] in ["image", "audio", "video"]:
                assert "source" in block, (
                    f"Invalid {block['type']} block: {block}, 'source' key "
                    "is required."
                )
                source = block["source"]
                # Save the image locally and return the file path
                if source["type"] == "url":
                    textual_output.append(
                        f"The returned {block['type']} can be found "
                        f"at: {source['url']}",
                    )

                    path_multimodal_file = source["url"]
                    multimodal_data.append((path_multimodal_file, block))
                elif source["type"] == "base64":
                    should_promote = False
                    if block["type"] == "image" and promote_tool_result_images:
                        should_promote = True
                    elif (
                        block["type"] == "audio" and promote_tool_result_audios
                    ):
                        should_promote = True
                    elif (
                        block["type"] == "video" and promote_tool_result_videos
                    ):
                        should_promote = True
                    if should_promote:
                        path_multimodal_file = _generate_unique_identifier(
                            source["data"],
                        )
                        textual_output.append(
                            f"The returned {block['type']} is represented "
                            f"by: {path_multimodal_file}",
                        )
                        multimodal_data.append((path_multimodal_file, block))
                    else:
                        if save_locally:
                            path_multimodal_file = _save_base64_data(
                                source["media_type"],
                                source["data"],
                            )
                            textual_output.append(
                                f"The returned {block['type']} can be found "
                                f"at: {path_multimodal_file}",
                            )
                            multimodal_data.append(
                                (path_multimodal_file, block),
                            )
                        else:
                            # Don't save, include base64 data directly in text
                            textual_output.append(
                                f"The returned {block['type']} contains "
                                f"base64 data.",
                            )
                            # Optionally add to multimodal_data with empty path
                            multimodal_data.append(("", block))
                else:
                    raise ValueError(
                        f"Invalid image source: {block['source']}, "
                        "expected 'url' or 'base64'.",
                    )

            else:
                raise ValueError(
                    f"Unsupported block type: {block['type']}, "
                    "expected 'text', 'image', 'audio', or 'video'.",
                )

        if len(textual_output) == 1:
            return textual_output[0], multimodal_data

        else:
            return "\n".join("- " + _ for _ in textual_output), multimodal_data
