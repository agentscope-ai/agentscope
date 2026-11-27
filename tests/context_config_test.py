# -*- coding: utf-8 -*-
"""ContextConfig summary_template / summary_schema consistency test.

The template is rendered with summary_template.format(**res.content) where
res.content holds exactly the fields summary_schema declares. A mismatch used
to surface as a bare KeyError mid-reply; it is now caught at construction.
"""
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from agentscope.agent import ContextConfig


class ContextConfigSummaryTemplateTest(unittest.TestCase):
    """summary_template/summary_schema consistency checks."""

    def test_default_config_is_consistent(self) -> None:
        """Default template and schema agree."""
        config = ContextConfig()
        self.assertIn("{next_steps}", config.summary_template)

    def test_schema_missing_template_field_is_rejected(self) -> None:
        """Schema that drops a template field is caught immediately."""
        schema = ContextConfig().summary_schema
        schema["properties"].pop("next_steps")

        with self.assertRaises(ValidationError) as ctx:
            ContextConfig(summary_schema=schema)

        self.assertIn("next_steps", str(ctx.exception))
        self.assertIn("summary_template", str(ctx.exception))

    def test_two_missing_fields_are_all_reported(self) -> None:
        """Every undeclared placeholder is named in the error."""
        schema = ContextConfig().summary_schema
        schema["properties"].pop("next_steps")
        schema["properties"].pop("important_discoveries")

        with self.assertRaises(ValidationError) as ctx:
            ContextConfig(summary_schema=schema)

        msg = str(ctx.exception)
        self.assertIn("important_discoveries", msg)
        self.assertIn("next_steps", msg)

    def test_matching_custom_template_and_schema_is_accepted(self) -> None:
        """Trimmed schema is fine when template is trimmed to match."""
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
        """Schema may declare fields the template does not use."""
        schema = ContextConfig().summary_schema
        schema["properties"]["extra_note"] = {"type": "string"}
        config = ContextConfig(summary_schema=schema)
        self.assertIn("extra_note", config.summary_schema["properties"])

    def test_template_without_placeholders_is_accepted(self) -> None:
        """Constant template needs no schema fields."""
        template = "<system-info>none</system-info>"
        config = ContextConfig(summary_template=template)
        self.assertEqual(config.summary_template, template)

    def test_uninspectable_schema_is_left_to_runtime(self) -> None:
        """Schemas without top-level properties are not rejected."""
        config = ContextConfig(summary_schema={"$ref": "#/$defs/Summary"})
        self.assertEqual(config.summary_schema, {"$ref": "#/$defs/Summary"})

    def test_subscript_into_declared_field_is_accepted(self) -> None:
        """``{stats[count]}`` only needs base field ``stats`` declared."""
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
            attributed.summary_template.format(
                stats=SimpleNamespace(total=9),
            ),
            "<system-info>9</system-info>",
        )

    def test_undeclared_base_field_is_still_rejected(self) -> None:
        """Stripping the accessor must not let a missing field through."""
        with self.assertRaises(ValidationError) as ctx:
            ContextConfig(
                summary_template=(
                    "<system-info>{missing[count]}</system-info>"
                ),
                summary_schema={
                    "type": "object",
                    "properties": {"stats": {"type": "object"}},
                    "required": ["stats"],
                },
            )
        self.assertIn("missing", str(ctx.exception))

    def test_nested_format_spec_field_is_checked(self) -> None:
        """``{overview:{width}}`` also needs ``width`` declared."""
        template = "<system-info>{overview:{current_state}}</system-info>"
        config = ContextConfig(
            summary_template=template,
            summary_schema={
                "type": "object",
                "properties": {
                    "overview": {"type": "string"},
                    "current_state": {"type": "string"},
                },
                "required": ["overview", "current_state"],
            },
        )
        self.assertEqual(
            config.summary_template.format(
                overview="ok", current_state="<8"
            ),
            "<system-info>ok      </system-info>",
        )

        with self.assertRaises(ValidationError) as ctx:
            ContextConfig(
                summary_template=(
                    "<system-info>{o:{undeclared}}</system-info>"
                ),
                summary_schema={
                    "type": "object",
                    "properties": {"o": {"type": "string"}},
                    "required": ["o"],
                },
            )
        self.assertIn("undeclared", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
