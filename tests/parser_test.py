# -*- coding: utf-8 -*-
"""Unit test for model response parser."""
import unittest

from pydantic import BaseModel, Field

from agentscope.model import ModelResponse
from agentscope.parsers import (
    MarkdownJsonDictParser,
    MarkdownJsonObjectParser,
    MarkdownCodeBlockParser,
    MultiTaggedContentParser,
    TaggedContent,
)
from agentscope.parsers.parser_base import DictFilterMixin


class ModelResponseParserTest(unittest.TestCase):
    """Unit test for model response parser."""

    def setUp(self) -> None:
        """Init for ExampleTest."""
        self.res_dict_1 = ModelResponse(
            text=(
                "```json\n"
                '{"speak": "Hello, world!", '
                '"thought": "xxx", '
                '"end_discussion": true}\n```'
            ),
        )
        self.instruction_dict_1 = (
            "Respond a JSON dictionary in a markdown's fenced code block "
            "as follows:\n"
            "```json\n"
            '{"speak": "what you speak", '
            '"thought": "what you thought", '
            '"end_discussion": true/false}\n'
            "```"
        )
        self.res_dict_2 = ModelResponse(
            text="[SPEAK]Hello, world![/SPEAK]\n"
            "[THOUGHT]xxx[/THOUGHT]\n"
            "[END_DISCUSSION]true[/END_DISCUSSION]",
        )
        self.instruction_dict_2 = (
            "Respond with specific tags as outlined below, and the content "
            "between [END_DISCUSSION] and [/END_DISCUSSION] MUST be a JSON "
            "object:\n"
            "[SPEAK]what you speak[/SPEAK]\n"
            "[THOUGHT]what you thought[/THOUGHT]\n"
            "[END_DISCUSSION]true/false[/END_DISCUSSION]"
        )
        self.gt_dict = {
            "speak": "Hello, world!",
            "thought": "xxx",
            "end_discussion": True,
        }
        self.hint_dict = (
            '{"speak": "what you speak", '
            '"thought": "what you thought", '
            '"end_discussion": true/false}'
        )

        self.instruction_dict_3 = (
            "Respond a JSON dictionary in a markdown's fenced code block as "
            "follows:\n"
            "```json\n"
            "{a_JSON_dictionary}\n"
            "```\n"
            "The generated JSON dictionary MUST follow this schema: \n"
            "{'properties': {'speak': {'description': 'what you speak', "
            "'title': 'Speak', 'type': 'string'}, 'thought': {'description': "
            "'what you thought', 'title': 'Thought', 'type': 'string'}, "
            "'end_discussion': {'description': 'whether the discussion "
            "reached an agreement or not', 'title': 'End Discussion', "
            "'type': 'boolean'}}, 'required': ['speak', 'thought', "
            "'end_discussion'], 'title': 'Schema', 'type': 'object'}"
        )

        self.gt_to_memory = {"speak": "Hello, world!", "thought": "xxx"}
        self.gt_to_content = "Hello, world!"
        self.gt_to_metadata = {"end_discussion": True}

        self.res_list = ModelResponse(text="""```json\n[1,2,3]\n```""")
        self.instruction_list = (
            "You should respond a json object in a json fenced code block as "
            "follows:\n"
            "```json\n"
            "{Your generated list of numbers}\n"
            "```"
        )
        self.gt_list = [1, 2, 3]
        self.hint_list = "{Your generated list of numbers}"

        self.res_float = ModelResponse(text="""```json\n3.14\n```""")
        self.instruction_float = (
            "You should respond a json object in a json fenced code block as "
            "follows:\n"
            "```json\n"
            "{Your generated float number}\n"
            "```"
        )
        self.gt_float = 3.14
        self.hint_float = "{Your generated float number}"

        self.res_code = ModelResponse(
            text="""```python\nprint("Hello, world!")\n```""",
        )
        self.instruction_code = (
            "You should generate python code in a python fenced code block as "
            "follows: \n"
            "```python\n"
            "${your_python_code}\n"
            "```"
        )
        self.instruction_code_with_hint = (
            "You should generate python code in a python fenced code block as "
            "follows: \n"
            "```python\n"
            "abc\n"
            "```"
        )
        self.gt_code = """\nprint("Hello, world!")\n"""

    def test_markdownjsondictparser_with_schema(self) -> None:
        """Test for MarkdownJsonDictParser with schema"""

        class Schema(BaseModel):  # pylint: disable=missing-class-docstring
            speak: str = Field(description="what you speak")
            thought: str = Field(description="what you thought")
            end_discussion: bool = Field(
                description="whether the discussion reached an agreement or "
                "not",
            )

        parser = MarkdownJsonDictParser(
            content_hint=Schema,
            keys_to_memory=["speak", "thought"],
            keys_to_content="speak",
            keys_to_metadata=["end_discussion"],
        )

        self.assertEqual(parser.format_instruction, self.instruction_dict_3)

        res = parser.parse(self.res_dict_1)

        self.assertDictEqual(res.parsed, self.gt_dict)

        res = parser.parse(
            ModelResponse(
                text="""```json
        {
            "speak" : "Hello, world!",
            "thought" : "xxx",
            "end_discussion" : "true"
        }
        ```""",
            ),
        )

        self.assertDictEqual(res.parsed, self.gt_dict)

    def test_markdownjsondictparser(self) -> None:
        """Test for MarkdownJsonDictParser"""
        parser = MarkdownJsonDictParser(
            content_hint=self.hint_dict,
            keys_to_memory=["speak", "thought"],
            keys_to_content="speak",
            keys_to_metadata=["end_discussion"],
        )

        self.assertEqual(parser.format_instruction, self.instruction_dict_1)

        res = parser.parse(self.res_dict_1)

        self.assertDictEqual(res.parsed, self.gt_dict)

        # test filter functions
        self.assertDictEqual(parser.to_memory(res.parsed), self.gt_to_memory)
        self.assertEqual(parser.to_content(res.parsed), self.gt_to_content)
        self.assertDictEqual(
            parser.to_metadata(res.parsed),
            self.gt_to_metadata,
        )

    def test_markdownjsonobjectparser(self) -> None:
        """Test for MarkdownJsonObjectParser"""
        # list
        parser_list = MarkdownJsonObjectParser(content_hint=self.hint_list)

        self.assertEqual(parser_list.format_instruction, self.instruction_list)

        res_list = parser_list.parse(self.res_list)
        self.assertListEqual(res_list.parsed, self.gt_list)

        # float
        parser_float = MarkdownJsonObjectParser(content_hint=self.hint_float)

        self.assertEqual(
            parser_float.format_instruction,
            self.instruction_float,
        )

        res_float = parser_float.parse(self.res_float)
        self.assertEqual(res_float.parsed, self.gt_float)

    def test_markdowncodeblockparser(self) -> None:
        """Test for MarkdownCodeBlockParser"""
        parser = MarkdownCodeBlockParser(language_name="python")

        self.assertEqual(parser.format_instruction, self.instruction_code)

        res = parser.parse(self.res_code)

        self.assertEqual(res.parsed, self.gt_code)

    def test_markdowncodeblockparser_with_hint(self) -> None:
        """Test for MarkdownCodeBlockParser"""
        parser = MarkdownCodeBlockParser(
            language_name="python",
            content_hint="abc",
        )

        self.assertEqual(
            parser.format_instruction,
            self.instruction_code_with_hint,
        )

        res = parser.parse(self.res_code)

        self.assertEqual(res.parsed, self.gt_code)

    def test_multitaggedcontentparser(self) -> None:
        """Test for MultiTaggedContentParser"""
        parser = MultiTaggedContentParser(
            TaggedContent(
                "speak",
                tag_begin="[SPEAK]",
                content_hint="what you speak",
                tag_end="[/SPEAK]",
            ),
            TaggedContent(
                "thought",
                tag_begin="[THOUGHT]",
                content_hint="what you thought",
                tag_end="[/THOUGHT]",
            ),
            TaggedContent(
                "end_discussion",
                tag_begin="[END_DISCUSSION]",
                content_hint="true/false",
                tag_end="[/END_DISCUSSION]",
                parse_json=True,
            ),
            keys_to_memory=["speak", "thought"],
            keys_to_content="speak",
            keys_to_metadata=["end_discussion"],
        )

        self.assertEqual(parser.format_instruction, self.instruction_dict_2)

        res = parser.parse(self.res_dict_2)

        self.assertDictEqual(res.parsed, self.gt_dict)

        # test filter functions
        self.assertDictEqual(parser.to_memory(res.parsed), self.gt_to_memory)
        self.assertEqual(parser.to_content(res.parsed), self.gt_to_content)
        self.assertDictEqual(
            parser.to_metadata(res.parsed),
            self.gt_to_metadata,
        )

    def test_DictFilterMixin_default_value(self) -> None:
        """Test the default value of the DictFilterMixin class"""
        mixin = DictFilterMixin(
            keys_to_memory=True,
            keys_to_content=True,
            keys_to_metadata=False,
        )

        self.assertDictEqual(mixin.to_memory(self.gt_dict), self.gt_dict)
        self.assertDictEqual(mixin.to_content(self.gt_dict), self.gt_dict)
        self.assertEqual(mixin.to_metadata(self.gt_dict), None)


if __name__ == "__main__":
    unittest.main()
