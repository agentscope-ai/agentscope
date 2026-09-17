# -*- coding: utf-8 -*-
"""``ContextConfig`` summary template/schema consistency test case.

Once a compression summary is generated, the template is rendered with
``summary_template.format(**res.content)``, where ``res.content`` holds
exactly the fields ``summary_schema`` declares. The two are configured
independently, so a schema that drops a field the template still references
used to surface as a bare ``KeyError`` while rendering the summary, in the
middle of a reply. The pair is checked when the configuration is built.
"""
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from agentscope.agent import ContextConfig


class ContextConfigSummaryTemplateTest(unittest.TestCase):
    """Test the summary template/schema consistency check."""

    def test_default_config_is_consistent(self) -> None:
        """The default template and the default schema agree."""
        config = ContextConfig()

        self.assertIn("{next_steps}", config.summary_template)

    def test_schema_missing_template_field_is_rejected(self) -> None:
        """A schema that drops a template field is reported immediately."""
        schema = ContextConfig().summary_schema
        schema["properties"].pop("next_steps")

        with self.assertRaises(ValidationError) as context:
            ContextConfig(summary_schema=schema)

        self.assertIn("next_steps", str(context.exception))
        self.assertIn("summary_template", str(context.exception))

    def test_two_fields_missing_are_all_reported(self) -> None:
        """Every undeclared placeholder is named in the error."""
        schema = ContextConfig().summary_schema
        schema["properties"].pop("next_steps")
        schema["properties"].pop("important_discoveries")

        with self.assertRaises(ValidationError) as context:
            ContextConfig(summary_schema=schema)

        message = str(context.exception)
        self.assertIn("important_discoveries", message)
        self.assertIn("next_steps", message)

    def test_matching_custom_template_and_schema_is_accepted(self) -> None:
        """A trimmed schema is fine once the template is trimmed too."""
        config = ContextConfig(
            summary_template="<system-info>{task_overview}</system-info>",
            summary_schema={
                "type": "object",
                "properties": {"task_overview": {"type": "string"}},
                "required": ["task_overview"],
            },
        )

        self.assertEqual(
            config.summary_template,
            "<system-info>{task_overview}</system-info>",
        )

    def test_extra_schema_fields_are_accepted(self) -> None:
        """A schema may declare more fields than the template uses."""
        schema = ContextConfig().summary_schema
        schema["properties"]["extra_note"] = {"type": "string"}

        config = ContextConfig(summary_schema=schema)

        self.assertIn("extra_note", config.summary_schema["properties"])

    def test_template_without_placeholders_is_accepted(self) -> None:
        """A constant template needs no schema fields at all."""
        template = "<system-info>none</system-info>"

        config = ContextConfig(summary_template=template)

        self.assertEqual(config.summary_template, template)

    def test_uninspectable_schema_is_left_to_runtime(self) -> None:
        """Schemas without top-level ``properties`` are not rejected."""
        config = ContextConfig(
            summary_schema={"$ref": "#/$defs/Summary"},
        )

        self.assertEqual(config.summary_schema, {"$ref": "#/$defs/Summary"})

    def test_reaching_into_a_declared_field_is_accepted(self) -> None:
        """An accessor into a declared field needs only its base declared.

        ``Formatter().parse()`` reports the whole accessor as the field name,
        but ``format`` resolves it at render time against whatever the field
        holds, so validating the accessor itself would reject templates that
        render fine.
        """
        schema = {
            "type": "object",
            "properties": {"stats": {"type": "object"}},
            "required": ["stats"],
        }

        indexed = ContextConfig(
            summary_template="<system-info>{stats[count]}</system-info>",
            summary_schema=schema,
        )
        attributed = ContextConfig(
            summary_template="<system-info>{stats.total}</system-info>",
            summary_schema=schema,
        )

        self.assertEqual(
            indexed.summary_template.format(stats={"count": 5}),
            "<system-info>5</system-info>",
        )
        self.assertEqual(
            attributed.summary_template.format(stats=SimpleNamespace(total=9)),
            "<system-info>9</system-info>",
        )

    def test_undeclared_base_field_is_still_rejected(self) -> None:
        """Dropping the accessor must not let a missing field through."""
        with self.assertRaises(ValidationError) as context:
            ContextConfig(
                summary_template="<system-info>{missing[count]}</system-info>",
                summary_schema={
                    "type": "object",
                    "properties": {"stats": {"type": "object"}},
                    "required": ["stats"],
                },
            )

        self.assertIn("missing", str(context.exception))


if __name__ == "__main__":
    unittest.main()
