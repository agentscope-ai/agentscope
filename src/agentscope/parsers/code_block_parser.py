# -*- coding: utf-8 -*-
"""Model response parser class for Markdown code block."""
from typing import Optional

from agentscope.model import ModelResponse
from agentscope.parsers import ParserBase


class MarkdownCodeBlockParser(ParserBase):
    """The base class for parsing the response text by fenced block."""

    name: str = "{language_name} block"
    """The name of the parser."""

    tag_begin: str = "```{language_name}"
    """The beginning tag."""

    content_hint: str = "${{your_{language_name}_code}}"
    """The hint of the content."""

    tag_end: str = "```"
    """The ending tag."""

    format_instruction: str = (
        "You should generate {language_name} code in a {language_name} fenced "
        "code block as follows: \n```{language_name}\n"
        "{content_hint}\n```"
    )
    """The instruction for the format of the code block."""

    def __init__(
        self,
        language_name: str,
        content_hint: Optional[str] = None,
    ) -> None:
        """Initialize the parser with the language name and the optional
        content hint.

        Args:
            language_name (`str`):
               The name of the language, which will be used
               in ```{language_name}
           content_hint (`Optional[str]`, defaults to `None`):
               The hint used to remind LLM what should be fill between the
               tags. If not provided, the default content hint
               "${{your_{language_name}_code}}" will be used.
        """
        self.name = self.name.format(language_name=language_name)
        self.tag_begin = self.tag_begin.format(language_name=language_name)

        if content_hint is None:
            self.content_hint = f"${{your_{language_name}_code}}"
        else:
            self.content_hint = content_hint

        self.format_instruction = self.format_instruction.format(
            language_name=language_name,
            content_hint=self.content_hint,
        ).strip()

    def parse(self, response: ModelResponse) -> ModelResponse:
        """Extract the content between the tag_begin and tag_end in the
        response and store it in the parsed field of the response object.
        """

        extract_text = self._extract_first_content_by_tag(
            response,
            self.tag_begin,
            self.tag_end,
        )
        response.parsed = extract_text
        return response
