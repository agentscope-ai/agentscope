# -*- coding: utf-8 -*-
"""Definition namespaces must not change tool parameter constraints."""

import copy
import unittest

from agentscope._utils._common import _flatten_json_schema


class SchemaNamespaceTest(unittest.TestCase):
    """Exercise the real schema flattener without provider dependencies."""

    def test_same_name_resolves_in_its_own_namespace(self) -> None:
        """Modern and legacy definitions are distinct, even with one name."""
        schema = {
            "$defs": {"Item": {"type": "string"}},
            "definitions": {"Item": {"type": "integer"}},
            "properties": {
                "modern": {"$ref": "#/$defs/Item"},
                "legacy": {"$ref": "#/definitions/Item"},
            },
        }
        original = copy.deepcopy(schema)
        self.assertEqual(
            _flatten_json_schema(schema),
            {
                "properties": {
                    "modern": {"type": "string"},
                    "legacy": {"type": "integer"},
                },
            },
        )
        self.assertEqual(schema, original)

    def test_cross_namespace_same_name_is_not_a_cycle(self) -> None:
        """A reference into the other namespace may reuse the type name."""
        for source, target in (
            ("$defs", "definitions"),
            ("definitions", "$defs"),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    _flatten_json_schema(
                        {
                            source: {
                                "Item": {
                                    "type": "array",
                                    "items": {"$ref": f"#/{target}/Item"},
                                },
                            },
                            target: {"Item": {"type": "string"}},
                            "$ref": f"#/{source}/Item",
                        },
                    ),
                    {"type": "array", "items": {"type": "string"}},
                )

    def test_missing_target_does_not_use_other_namespace(self) -> None:
        """The same name elsewhere cannot satisfy a missing reference."""
        for source, target in (
            ("$defs", "definitions"),
            ("definitions", "$defs"),
        ):
            ref = {"$ref": f"#/{target}/Item"}
            with self.subTest(source=source):
                self.assertEqual(
                    _flatten_json_schema({source: {"Item": {}}, **ref}),
                    ref,
                )

    def test_real_cross_namespace_cycle_still_terminates(self) -> None:
        """Both namespaces participate in cycle detection."""
        result = _flatten_json_schema(
            {
                "$defs": {"Item": {"$ref": "#/definitions/Item"}},
                "definitions": {"Item": {"$ref": "#/$defs/Item"}},
                "$ref": "#/$defs/Item",
            },
        )
        self.assertEqual(
            result,
            {"type": "object", "description": "(circular: Item)"},
        )

    def test_single_namespace_escaped_names_and_siblings(self) -> None:
        """Existing escaped references and sibling constraints still work."""
        for keyword in ("$defs", "definitions"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    _flatten_json_schema(
                        {
                            keyword: {"A/B~C": {"type": "string"}},
                            "$ref": f"#/{keyword}/A~1B~0C",
                            "minLength": 2,
                        },
                    ),
                    {"type": "string", "minLength": 2},
                )

    def test_discriminator_lookup_uses_its_namespace(self) -> None:
        """Only mappings to definitions actually removed are dropped."""
        discriminator = {
            "propertyName": "kind",
            "mapping": {"item": "#/definitions/Item"},
        }
        self.assertEqual(
            _flatten_json_schema(
                {
                    "$defs": {"Item": {"type": "string"}},
                    "discriminator": discriminator,
                },
            ),
            {"discriminator": discriminator},
        )
        self.assertEqual(
            _flatten_json_schema(
                {
                    "definitions": {"Item": {"type": "string"}},
                    "discriminator": discriminator,
                },
            ),
            {},
        )

    def test_external_reference_is_unchanged(self) -> None:
        """Remote references are not mistaken for local definitions."""
        ref = {"$ref": "https://example.com/schema/Item"}
        self.assertEqual(
            _flatten_json_schema({"$defs": {"Item": {}}, **ref}),
            ref,
        )

    def test_no_definitions_preserves_identity(self) -> None:
        """Definition-free schemas retain their existing fast path."""
        schema = {"type": "string"}
        self.assertIs(_flatten_json_schema(schema), schema)


if __name__ == "__main__":
    unittest.main()
