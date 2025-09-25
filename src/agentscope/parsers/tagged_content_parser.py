# -*- coding: utf-8 -*-
"""The parser for tagged content in the model response."""
import json
from typing import Union, Sequence, Optional, List

from agentscope.exception import JsonParsingError, TagNotFoundError
from agentscope.model import ModelResponse
from agentscope.parsers import ParserBase
from agentscope.parsers.parser_base import DictFilterMixin


class TaggedContent:
    """A tagged content object to store the tag name, tag begin, content hint
    and tag end."""

    name: str
    """The name of the tagged content, which will be used as the key in
    extracted dictionary."""

    tag_begin: str
    """The beginning tag."""

    content_hint: str
    """The hint of the content."""

    tag_end: str
    """The ending tag."""

    parse_json: bool
    """Whether to parse the content as a json object."""

    def __init__(
        self,
        name: str,
        tag_begin: str,
        content_hint: str,
        tag_end: str,
        parse_json: bool = False,
    ) -> None:
        """Initialize the tagged content object.

        Args:
            name (`str`):
                The name of the tagged content.
            tag_begin (`str`):
                The beginning tag.
            content_hint (`str`):
                The hint of the content.
            tag_end (`str`):
                The ending tag.
            parse_json (`bool`, defaults to `False`):
                Whether to parse the content as a json object.
        """

        self.name = name
        self.tag_begin = tag_begin
        self.content_hint = content_hint
        self.tag_end = tag_end
        self.parse_json = parse_json

    def __str__(self) -> str:
        """Return the tagged content as a string."""
        return f"{self.tag_begin}{self.content_hint}{self.tag_end}"


class MultiTaggedContentParser(ParserBase, DictFilterMixin):
    """Parse response text by multiple tags, and return a dict of their
    content. Asking llm to generate JSON dictionary object directly maybe not a
    good idea due to involving escape characters and other issues. So we can
    ask llm to generate text with tags, and then parse the text to get the
    final JSON dictionary object.
    """

    format_instruction = (
        "Respond with specific tags as outlined below{json_required_hint}\n"
        "{tag_lines_format}"
    )
    """The instruction for the format of the tagged content."""

    json_required_hint = ", and the content between {} MUST be a JSON object:"
    """If a tagged content is required to be a JSON object by `parse_json`
    equals to `True`, this instruction will be used to remind the model to
    generate JSON object."""

    def __init__(
        self,
        *tagged_contents: TaggedContent,
        keys_to_memory: Optional[Union[str, bool, Sequence[str]]] = True,
        keys_to_content: Optional[Union[str, bool, Sequence[str]]] = True,
        keys_to_metadata: Optional[Union[str, bool, Sequence[str]]] = False,
        keys_allow_missing: Optional[List[str]] = None,
    ) -> None:
        """Initialize the parser with tags.

        Args:
            *tagged_contents (`dict[str, Tuple[str, str]]`):
                Multiple TaggedContent objects, each object contains the tag
                name, tag begin, content hint and tag end. The name will be
                used as the key in the extracted dictionary.
            required_keys (`Optional[List[str]]`, defaults to `None`):
                A list of required
            keys_to_memory (`Optional[Union[str, bool, Sequence[str]]]`,
            defaults to `True`):
                The key or keys to be filtered in `to_memory` method. If
                it's
                - `False`, `None` will be returned in the `to_memory` method
                - `str`, the corresponding value will be returned
                - `List[str]`, a filtered dictionary will be returned
                - `True`, the whole dictionary will be returned
            keys_to_content (`Optional[Union[str, bool, Sequence[str]]`,
            defaults to `True`):
                The key or keys to be filtered in `to_content` method. If
                it's
                - `False`, `None` will be returned in the `to_content` method
                - `str`, the corresponding value will be returned
                - `List[str]`, a filtered dictionary will be returned
                - `True`, the whole dictionary will be returned
            keys_to_metadata (`Optional[Union[str, bool, Sequence[str]]]`,
            defaults to `False`):
                The key or keys to be filtered in `to_metadata` method. If
                it's
                - `False`, `None` will be returned in the `to_metadata` method
                - `str`, the corresponding value will be returned
                - `List[str]`, a filtered dictionary will be returned
                - `True`, the whole dictionary will be returned
            keys_allow_missing (`Optional[List[str]]`, defaults to `None`):
                A list of keys that are allowed to be missing in the response.
        """
        # Initialize the mixin class
        DictFilterMixin.__init__(
            self,
            keys_to_memory=keys_to_memory,
            keys_to_content=keys_to_content,
            keys_to_metadata=keys_to_metadata,
        )

        self.keys_allow_missing = keys_allow_missing

        self.tagged_contents = list(tagged_contents)

        # Prepare the format instruction according to the tagged contents
        tag_lines = "\n".join([str(_) for _ in tagged_contents])

        # Prepare hint for the tagged contents that requires a JSON object.
        json_required_tags = ", ".join(
            [
                f"{_.tag_begin} and {_.tag_end}"
                for _ in tagged_contents
                if _.parse_json
            ],
        )
        if json_required_tags != "":
            json_required_hint = self.json_required_hint.format(
                json_required_tags,
            )
        else:
            json_required_hint = ": "

        self.format_instruction = self.format_instruction.format(
            json_required_hint=json_required_hint,
            tag_lines_format=tag_lines,
        )

    def parse(self, response: ModelResponse) -> ModelResponse:
        """Parse the response text by tags, and return a dict of their content
        in the parsed field of the model response object. If the tagged content
        requires to parse as a JSON object by `parse_json` equals to `True`, it
        will be parsed as a JSON object by `json.loads`."""

        tag_to_content = {}
        for tagged_content in self.tagged_contents:
            tag_begin = tagged_content.tag_begin
            tag_end = tagged_content.tag_end

            try:
                extract_content = self._extract_first_content_by_tag(
                    response,
                    tag_begin,
                    tag_end,
                )

                if tagged_content.parse_json:
                    try:
                        extract_content = json.loads(extract_content)
                    except json.decoder.JSONDecodeError as e:
                        raw_response = f"{tag_begin}{extract_content}{tag_end}"
                        raise JsonParsingError(
                            f"The content between "
                            f"{tagged_content.tag_begin} and "
                            f"{tagged_content.tag_end} should be a JSON "
                            f'object. An error "{e}" occurred when parsing: '
                            f"{raw_response}",
                            raw_response=raw_response,
                        ) from None

                tag_to_content[tagged_content.name] = extract_content

            except TagNotFoundError as e:
                # if the key is allowed to be missing, skip the error
                if (
                    self.keys_allow_missing is not None
                    and tagged_content.name in self.keys_allow_missing
                ):
                    continue

                raise e from None

        response.parsed = tag_to_content
        return response
