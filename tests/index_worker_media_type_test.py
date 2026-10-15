# -*- coding: utf-8 -*-
"""Tests for the index worker's media-type normalisation.

A client-supplied ``Content-Type`` carries parameters far more often than not,
and the parser registry is keyed by bare media types, so the reduction has to
happen before the lookup.
"""
from unittest import TestCase

from agentscope.app._service._index_worker import _normalize_media_type


class NormalizeMediaTypeTest(TestCase):
    """Parameters and casing must not defeat the parser lookup."""

    def test_bare_media_type_is_unchanged(self) -> None:
        """A plain type passes through lowercased."""
        self.assertEqual(
            _normalize_media_type("text/markdown"),
            "text/markdown",
        )
        self.assertEqual(
            _normalize_media_type("application/pdf"),
            "application/pdf",
        )

    def test_parameters_are_stripped(self) -> None:
        """The ``;``-separated tail is not part of the media type."""
        self.assertEqual(
            _normalize_media_type("text/markdown; charset=utf-8"),
            "text/markdown",
        )
        self.assertEqual(
            _normalize_media_type("application/pdf;charset=binary"),
            "application/pdf",
        )
        self.assertEqual(
            _normalize_media_type("text/html; charset=utf-8; boundary=x"),
            "text/html",
        )

    def test_surrounding_whitespace_is_stripped(self) -> None:
        """A leading space and one before the ``;`` are both common."""
        self.assertEqual(
            _normalize_media_type("  text/markdown ; charset=utf-8"),
            "text/markdown",
        )

    def test_type_is_lowercased(self) -> None:
        """``type``/``subtype`` are case-insensitive per RFC 9110."""
        self.assertEqual(
            _normalize_media_type("TEXT/Markdown"),
            "text/markdown",
        )
        self.assertEqual(
            _normalize_media_type("Application/PDF; charset=binary"),
            "application/pdf",
        )

    def test_empty_values_become_none(self) -> None:
        """Nothing usable must surface as `None`, not as ``""``."""
        self.assertIsNone(_normalize_media_type(None))
        self.assertIsNone(_normalize_media_type(""))
        self.assertIsNone(_normalize_media_type("   "))
        self.assertIsNone(_normalize_media_type("; charset=utf-8"))

    def test_normalised_value_hits_a_parser_registry(self) -> None:
        """The whole point: a real registry now resolves the header."""
        registry = {"text/markdown": object(), "application/pdf": object()}

        for raw, expected in (
            ("text/markdown; charset=utf-8", "text/markdown"),
            ("TEXT/Markdown", "text/markdown"),
            ("application/pdf;charset=binary", "application/pdf"),
        ):
            normalised = _normalize_media_type(raw)
            self.assertIsNotNone(normalised)
            self.assertIn(normalised, registry, raw)
            self.assertIs(registry[normalised], registry[expected])
