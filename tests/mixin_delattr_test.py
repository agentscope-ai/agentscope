# -*- coding: utf-8 -*-
"""Regression tests for attribute-style deletion on DictMixin."""
from unittest import TestCase

from agentscope._utils._mixin import DictMixin


class DictMixinDeletionTest(TestCase):
    """Attribute-style deletion must mirror attribute get/set."""

    def test_deleting_an_attribute_removes_the_mapping_entry(self) -> None:
        """`del value.field` removes the underlying dict key."""
        value = DictMixin(a=1, b=2)

        del value.a

        self.assertNotIn("a", value)
        self.assertEqual(dict(value), {"b": 2})

    def test_deleting_a_missing_attribute_raises_attribute_error(
        self,
    ) -> None:
        """A missing attribute keeps the attribute protocol on delete."""
        value = DictMixin(a=1)

        with self.assertRaises(AttributeError):
            del value.missing
