# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for the Excel column-name helper."""
from unittest import TestCase

from agentscope.rag._parser._excel import _get_excel_column_name


class ExcelColumnNameTest(TestCase):
    """Column names are only defined for non-negative indices."""

    def test_negative_one_is_rejected(self) -> None:
        """A negative index must raise instead of yielding an empty name."""
        with self.assertRaises(ValueError):
            _get_excel_column_name(-1)

    def test_large_negative_is_rejected(self) -> None:
        """Any negative index, however small, is rejected."""
        with self.assertRaises(ValueError):
            _get_excel_column_name(-100)

    def test_zero_is_a(self) -> None:
        """Index 0 is the first column."""
        self.assertEqual(_get_excel_column_name(0), "A")

    def test_25_is_z(self) -> None:
        """Index 25 is the last single-letter column."""
        self.assertEqual(_get_excel_column_name(25), "Z")

    def test_26_is_aa(self) -> None:
        """Index 26 rolls over to two letters."""
        self.assertEqual(_get_excel_column_name(26), "AA")

    def test_701_is_zz(self) -> None:
        """Index 701 is the last two-letter column."""
        self.assertEqual(_get_excel_column_name(701), "ZZ")

    def test_702_is_aaa(self) -> None:
        """Index 702 rolls over to three letters."""
        self.assertEqual(_get_excel_column_name(702), "AAA")
