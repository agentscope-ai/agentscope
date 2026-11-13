# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for the DashScope realtime websocket URL builder."""
from unittest import TestCase
from urllib.parse import parse_qs, urlsplit

from agentscope.realtime._dashscope._model import _realtime_url


class RealtimeUrlEncodingTest(TestCase):
    """The ``model`` query value must survive URL parsing intact."""

    def test_reserved_characters_are_encoded(self) -> None:
        """Reserved characters must not split the query into extra keys."""
        model = "qwen-plus&injected=1"
        url = _realtime_url("https://dashscope.aliyuncs.com", model)
        self.assertEqual(urlsplit(url).scheme, "wss")
        self.assertEqual(
            parse_qs(urlsplit(url).query).get("model"),
            [model],
        )

    def test_unicode_and_space_are_encoded(self) -> None:
        """Spaces and non-ASCII names must be percent-encoded."""
        model = "\u6a21\u578b v1"
        url = _realtime_url("https://dashscope.aliyuncs.com", model)
        self.assertNotIn(" ", urlsplit(url).query)
        self.assertEqual(
            parse_qs(urlsplit(url).query).get("model"),
            [model],
        )

    def test_plain_model_keeps_historical_url(self) -> None:
        """A plain model name keeps the historical URL byte-for-byte."""
        self.assertEqual(
            _realtime_url("https://dashscope.aliyuncs.com", "qwen-plus"),
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
            "?model=qwen-plus",
        )

    def test_http_maps_to_ws(self) -> None:
        """An http base URL maps to ws and keeps the encoded model."""
        url = _realtime_url("http://localhost:8080", "m x")
        self.assertEqual(urlsplit(url).scheme, "ws")
        self.assertEqual(
            parse_qs(urlsplit(url).query).get("model"),
            ["m x"],
        )
