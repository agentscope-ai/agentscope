# -*- coding: utf-8 -*-
"""The parser for dynamic tagged content"""
import json
import re
from typing import Union, Sequence, Optional, List

from loguru import logger

from ..exception import TagNotFoundError
from ..model import ModelResponse
from ..parsers import ParserBase
from ..parsers.parser_base import DictFilterMixin


class RegexTaggedContentParser(ParserBase, DictFilterMixin):
    """A regex tagged content parser, which extracts tagged content according
    to the provided regex pattern. Different from other parsers, this parser
    allows to extract multiple tagged content without knowing the keys in
    advance. The parsed result will be a dictionary within the parsed field of
    the model response.

    Compared with other parsers, this parser is more flexible and can be used
    in dynamic scenarios where
    - the keys are not known in advance
    - the number of the tagged content is not fixed

    Note: Without knowing the keys in advance, it's hard to prepare a format
    instruction template for different scenarios. Therefore, we ask the user
    to provide the format instruction in the constructor. Of course, the user
    can construct and manage the prompt by themselves optionally.

    Example:
        By default, the parser use a regex pattern to extract tagged content
        with the following format:
        ```
        <{name1}>{content1}</{name1}>
        <{name2}>{content2}</{name2}>
        ```
        The parser will extract the content as the following dictionary:
        ```
        {
            "name1": content1,
            "name2": content2,
        }
        ```
    """

    def __init__(
        self,
        tagged_content_pattern: str = r"<(?P<name>[^>]+)>"
        r"(?P<content>.*?)"
        r"</\1?>",
        format_instruction: Optional[str] = None,
        try_parse_json: bool = True,
        required_keys: Optional[List[str]] = None,
        keys_to_memory: Union[str, bool, Sequence[str]] = True,
        keys_to_content: Union[str, bool, Sequence[str]] = True,
        keys_to_metadata: Union[str, bool, Sequence[str]] = False,
    ) -> None:
        """Initialize the regex tagged content parser.

        Args:
            tagged_content_pattern (`Optional[str]`, defaults to
            `"<(?P<name>[^>]+)>(?P<content>.*?)</\1?>"`):
                The regex pattern to extract tagged content. The pattern should
                contain two named groups: `name` and `content`. The `name`
                group is used as the key of the tagged content, and the
                `content` group is used as the value.
            format_instruction (`Optional[str]`, defaults to `None`):
                The instruction for the format of the tagged content, which
                will be attached to the end of the prompt messages to remind
                the LLM to follow the format.
            try_parse_json (`bool`, defaults to `True`):
                Whether to try to parse the tagged content as JSON. Note
                the parsing function won't raise exceptions.
            required_keys (`Optional[List[str]]`, defaults to `None`):
                The keys that are required in the tagged content.
            keys_to_memory (`Union[str, bool, Sequence[str]]`,
            defaults to `True`):
                The keys to save to memory.
            keys_to_content (`Union[str, bool, Sequence[str]]`,
            defaults to `True`):
                The keys to save to content.
            keys_to_metadata (`Union[str, bool, Sequence[str]]`,
            defaults to `False`):
                The key or keys to be filtered in `to_metadata` method. If
                it's
                - `False`, `None` will be returned in the `to_metadata` method
                - `str`, the corresponding value will be returned
                - `List[str]`, a filtered dictionary will be returned
                - `True`, the whole dictionary will be returned
        """

        DictFilterMixin.__init__(
            self,
            keys_to_memory=keys_to_memory,
            keys_to_content=keys_to_content,
            keys_to_metadata=keys_to_metadata,
        )

        assert (
            "<name>" in tagged_content_pattern
        ), "The tagged content pattern should contain a named group 'name'."
        assert (
            "<content>" in tagged_content_pattern
        ), "The tagged content pattern should contain a named group 'content'."

        self.tagged_content_pattern = tagged_content_pattern
        self._format_instruction = format_instruction
        self.try_parse_json = try_parse_json
        self.required_keys = required_keys or []

    @property
    def format_instruction(self) -> str:
        """The format instruction for the tagged content."""
        if self._format_instruction is None:
            raise ValueError(
                "The format instruction is not provided. Please provide it in "
                "the constructor of the parser.",
            )
        return self._format_instruction

    def parse(self, response: ModelResponse) -> ModelResponse:
        """Parse the response text by the regex pattern, and return a dict of
        the content in the parsed field of the response.

        Args:
            response (`ModelResponse`):
                The response to be parsed.

        Returns:
            `ModelResponse`: The response with the parsed field as the parsed
            result.
        """
        assert response.text is not None, "The response text is None."

        matches = re.finditer(
            self.tagged_content_pattern,
            response.text,
            flags=re.DOTALL,
        )

        results = {}
        for match in matches:
            results[match.group("name")] = match.group("content")

        keys_missing = [
            key for key in self.required_keys if key not in results
        ]

        if len(keys_missing) > 0:
            raise TagNotFoundError(
                f"Failed to find tags: {', '.join(keys_missing)}",
                response.text,
            )

        if self.try_parse_json:
            keys_failed = []
            for key in results:
                try:
                    results[key] = json.loads(results[key])
                except json.JSONDecodeError:
                    keys_failed.append(key)

            if keys_failed:
                logger.debug(
                    f'Failed to parse JSON for keys: {", ".join(keys_failed)}',
                )

        response.parsed = results
        return response
