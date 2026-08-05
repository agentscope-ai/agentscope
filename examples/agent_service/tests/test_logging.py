"""Tests for the logging trio: trace_context, TraceContextFilter, formatters.

Pin the invariants that matter for request correlation: trace_id
normalization, filter injection, and the text/json formatter output shape.
"""
from __future__ import annotations

import io
import json
import logging

from bocomadp.logging.logging_config import (
    JsonTraceFormatter,
    TraceContextFilter,
    TraceTextFormatter,
    _has_trace_filter,
    _trace_formatter,
    apply_logging_level,
    configure_logging,
)
from bocomadp.logging.trace_context import (
    _MAX_TRACE_ID_LENGTH,
    ensure_trace_context,
    generate_trace_id,
    get_current_trace_id,
    normalize_trace_id,
    request_trace_context,
)


# ---------------------------------------------------------------------------
# trace_context invariants
# ---------------------------------------------------------------------------

class TestNormalizeTraceId:
    def test_accepts_plain_ascii(self) -> None:
        assert normalize_trace_id("abc-123_XYZ") == "abc-123_XYZ"

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_trace_id("  trace-1  ") == "trace-1"

    def test_rejects_non_string(self) -> None:
        assert normalize_trace_id(None) is None
        assert normalize_trace_id(123) is None

    def test_rejects_empty(self) -> None:
        assert normalize_trace_id("") is None
        assert normalize_trace_id("   ") is None

    def test_rejects_too_long(self) -> None:
        assert normalize_trace_id("a" * (_MAX_TRACE_ID_LENGTH + 1)) is None
        assert normalize_trace_id("a" * _MAX_TRACE_ID_LENGTH) == "a" * _MAX_TRACE_ID_LENGTH

    def test_rejects_control_chars(self) -> None:
        assert normalize_trace_id("abc\x01def") is None  # C0
        assert normalize_trace_id("abc\x7f") is None    # DEL
        assert normalize_trace_id("abc\x80") is None    # C1
        assert normalize_trace_id("中文") is None       # non-ascii

    def test_accepts_full_printable_range(self) -> None:
        chars = "".join(chr(c) for c in range(0x20, 0x7F))
        assert normalize_trace_id(chars) == chars


class TestGenerateTraceId:
    def test_is_32_hex_chars(self) -> None:
        tid = generate_trace_id()
        assert len(tid) == 32
        int(tid, 16)

    def test_unique(self) -> None:
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestRequestTraceContext:
    def test_binds_and_resets(self) -> None:
        assert get_current_trace_id() is None
        with request_trace_context("incoming-1") as tid:
            assert tid == "incoming-1"
            assert get_current_trace_id() == "incoming-1"
        assert get_current_trace_id() is None

    def test_generates_when_missing(self) -> None:
        with request_trace_context(None) as tid:
            assert tid is not None
            assert len(tid) == 32
        assert get_current_trace_id() is None

    def test_normalizes_unsafe_input(self) -> None:
        with request_trace_context("  bad\x01input  ") as tid:
            assert len(tid) == 32  # rejected → generated

    def test_ensure_inherits_or_creates(self) -> None:
        with request_trace_context("outer") as outer:
            assert outer == "outer"
            with ensure_trace_context() as inner:
                assert inner == "outer"


# ---------------------------------------------------------------------------
# TraceContextFilter + formatters
# ---------------------------------------------------------------------------

class TestTraceContextFilter:
    def _record(self, msg: str = "hello") -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname="x.py",
            lineno=1, msg=msg, args=None, exc_info=None,
        )

    def test_injects_trace_id(self) -> None:
        rec = self._record()
        with request_trace_context("tid-123"):
            assert TraceContextFilter().filter(rec) is True
            assert rec.trace_id == "tid-123"

    def test_defaults_to_dash(self) -> None:
        rec = self._record()
        assert TraceContextFilter().filter(rec) is True
        assert rec.trace_id == "-"


class TestFormatters:
    def _record(self, msg: str = "hi", args=None) -> logging.LogRecord:
        return logging.LogRecord(
            name="my.logger", level=logging.WARNING, pathname="x.py",
            lineno=42, msg=msg, args=args, exc_info=None,
        )

    def test_json_formatter_outputs_valid_json(self) -> None:
        rec = self._record("something %s", ("happened",))
        with request_trace_context("json-tid"):
            line = JsonTraceFormatter().format(rec)
        payload = json.loads(line)
        assert payload["logger"] == "my.logger"
        assert payload["level"] == "WARNING"
        assert payload["trace_id"] == "json-tid"
        assert payload["message"] == "something happened"

    def test_text_formatter_has_trace_placeholder(self) -> None:
        rec = self._record()
        fmt = _trace_formatter("text")
        with request_trace_context("text-tid"):
            line = fmt.format(rec)
        assert "[trace_id=text-tid]" in line
        assert isinstance(fmt, TraceTextFormatter)


# ---------------------------------------------------------------------------
# configure_logging integration
# ---------------------------------------------------------------------------

class TestConfigureLogging:
    def setup_method(self) -> None:
        self._root_level = logging.root.level
        self._root_handlers = list(logging.root.handlers)
        self._my_level = logging.getLogger("bocomadp").level
        self._app_level = logging.getLogger("app").level

    def teardown_method(self) -> None:
        for h in list(logging.root.handlers):
            logging.root.removeHandler(h)
            h.close()
        for h in self._root_handlers:
            logging.root.addHandler(h)
        logging.root.setLevel(self._root_level)
        logging.getLogger("bocomadp").setLevel(self._my_level)
        logging.getLogger("app").setLevel(self._app_level)

    def _cfg(self, *, enabled: bool, fmt: str = "text", level: str = "info"):
        from types import SimpleNamespace
        return SimpleNamespace(
            log_level=level,
            logging=SimpleNamespace(
                enhance=SimpleNamespace(enabled=enabled, format=fmt),
            ),
        )

    def test_enhanced_installs_text_filter_and_formatter(self) -> None:
        h = logging.StreamHandler(io.StringIO())
        logging.root.addHandler(h)
        configure_logging(self._cfg(enabled=True, fmt="text"))
        assert _has_trace_filter(h)
        assert isinstance(h.formatter, TraceTextFormatter)

    def test_enhanced_installs_json_formatter(self) -> None:
        h = logging.StreamHandler(io.StringIO())
        logging.root.addHandler(h)
        configure_logging(self._cfg(enabled=True, fmt="json"))
        assert _has_trace_filter(h)
        assert isinstance(h.formatter, JsonTraceFormatter)

    def test_disabled_removes_filter(self) -> None:
        h = logging.StreamHandler(io.StringIO())
        h.addFilter(TraceContextFilter())
        h.setFormatter(_trace_formatter("text"))
        logging.root.addHandler(h)
        configure_logging(self._cfg(enabled=False))
        assert not _has_trace_filter(h)

    def test_apply_level_sets_my_and_app(self) -> None:
        apply_logging_level("debug")
        assert logging.getLogger("bocomadp").level == logging.DEBUG
        assert logging.getLogger("app").level == logging.DEBUG

    def test_end_to_end_carries_trace_id(self) -> None:
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        logging.root.addHandler(h)
        configure_logging(self._cfg(enabled=True, fmt="json"))
        lg = logging.getLogger("bocomadp.test")
        lg.setLevel(logging.DEBUG)
        with request_trace_context("e2e-tid"):
            lg.info("end to end")
        line = stream.getvalue().strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["trace_id"] == "e2e-tid"
        assert payload["message"] == "end to end"
"""Tests for the logging trio: trace_context, TraceContextFilter, formatters.

Adapted from deer-flow-2.0's ``test_logging_config.py`` and
``test_trace_context.py``. These pin the invariants that matter for
request correlation: trace_id normalization, filter injection, and the
text/json formatter output shape.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from bocomadp.logging.logging_config import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_DATE_FORMAT,
    JsonTraceFormatter,
    TraceContextFilter,
    TraceTextFormatter,
    _default_formatter,
    _has_trace_filter,
    _install_trace_filter,
    _remove_trace_filter,
    _trace_formatter,
    apply_logging_level,
    configure_logging,
)
from bocomadp.logging.trace_context import (
    _MAX_TRACE_ID_LENGTH,
    generate_trace_id,
    get_current_trace_id,
    normalize_trace_id,
    request_trace_context,
    set_current_trace_id,
)


# ---------------------------------------------------------------------------
# trace_context invariants
# ---------------------------------------------------------------------------

class TestNormalizeTraceId:
    def test_accepts_plain_ascii(self) -> None:
        assert normalize_trace_id("abc-123_XYZ") == "abc-123_XYZ"

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_trace_id("  trace-1  ") == "trace-1"

    def test_rejects_non_string(self) -> None:
        assert normalize_trace_id(None) is None
        assert normalize_trace_id(123) is None

    def test_rejects_empty(self) -> None:
        assert normalize_trace_id("") is None
        assert normalize_trace_id("   ") is None

    def test_rejects_too_long(self) -> None:
        assert normalize_trace_id("a" * (_MAX_TRACE_ID_LENGTH + 1)) is None
        assert normalize_trace_id("a" * _MAX_TRACE_ID_LENGTH) == "a" * _MAX_TRACE_ID_LENGTH

    def test_rejects_control_chars(self) -> None:
        # C0 control (< 0x20) — log-injection / header-safety rejection
        assert normalize_trace_id("abc\x01def") is None
        # DEL (0x7F)
        assert normalize_trace_id("abc\x7f") is None
        # C1 control (0x80-0x9F)
        assert normalize_trace_id("abc\x80") is None
        # codepoint > 0x7E (non-ascii)
        assert normalize_trace_id("中文") is None

    def test_accepts_full_printable_range(self) -> None:
        # 0x20 (space) through 0x7E (~) all valid
        chars = "".join(chr(c) for c in range(0x20, 0x7F))
        assert normalize_trace_id(chars) == chars


class TestGenerateTraceId:
    def test_is_32_hex_chars(self) -> None:
        tid = generate_trace_id()
        assert len(tid) == 32
        int(tid, 16)  # must parse as hex

    def test_unique(self) -> None:
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestRequestTraceContext:
    def test_binds_and_resets(self) -> None:
        assert get_current_trace_id() is None
        with request_trace_context("incoming-1") as tid:
            assert tid == "incoming-1"
            assert get_current_trace_id() == "incoming-1"
        assert get_current_trace_id() is None

    def test_generates_when_missing(self) -> None:
        with request_trace_context(None) as tid:
            assert tid is not None
            assert len(tid) == 32
        assert get_current_trace_id() is None

    def test_normalizes_unsafe_input(self) -> None:
        with request_trace_context("  bad\x01input  ") as tid:
            # normalize rejects control chars → falls back to generated
            assert len(tid) == 32

    def test_inherit_or_create(self) -> None:
        from bocomadp.logging.trace_context import ensure_trace_context
        with request_trace_context("outer") as outer:
            assert outer == "outer"
            with ensure_trace_context() as inner:
                # inherits the outer trace
                assert inner == "outer"


# ---------------------------------------------------------------------------
# TraceContextFilter + formatters
# ---------------------------------------------------------------------------

class TestTraceContextFilter:
    def test_injects_trace_id_into_record(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="x.py",
            lineno=1,
            msg="hello",
            args=None,
            exc_info=None,
        )
        flt = TraceContextFilter()
        with request_trace_context("tid-123"):
            assert flt.filter(record) is True
            assert record.trace_id == "tid-123"

    def test_defaults_to_dash_when_no_context(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="x.py",
            lineno=1,
            msg="hello",
            args=None,
            exc_info=None,
        )
        flt = TraceContextFilter()
        assert flt.filter(record) is True
        assert record.trace_id == "-"


class TestJsonTraceFormatter:
    def test_outputs_valid_json_with_trace_id(self) -> None:
        record = logging.LogRecord(
            name="my.logger",
            level=logging.WARNING,
            pathname="x.py",
            lineno=42,
            msg="something %s",
            args=("happened",),
            exc_info=None,
        )
        formatter = JsonTraceFormatter()
        with request_trace_context("json-tid"):
            line = formatter.format(record)
        payload = json.loads(line)
        assert payload["logger"] == "my.logger"
        assert payload["level"] == "WARNING"
        assert payload["trace_id"] == "json-tid"
        assert payload["message"] == "something happened"
        assert "timestamp" in payload

    def test_includes_exc_info_when_present(self) -> None:
        try:
            raise ValueError("boom")
        except Exception:
            import sys
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname="x.py",
                lineno=1,
                msg="err",
                args=None,
                exc_info=sys.exc_info(),
            )
        line = JsonTraceFormatter().format(record)
        payload = json.loads(line)
        assert "exc_info" in payload
        assert "ValueError" in payload["exc_info"]


class TestTraceTextFormatter:
    def test_includes_trace_id_placeholder(self) -> None:
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="x.py",
            lineno=1,
            msg="hi",
            args=None,
            exc_info=None,
        )
        fmt = _trace_formatter("text")
        with request_trace_context("text-tid"):
            line = fmt.format(record)
        assert "[trace_id=text-tid]" in line
        assert "hi" in line


# ---------------------------------------------------------------------------
# configure_logging integration
# ---------------------------------------------------------------------------

class TestConfigureLogging:
    def setup_method(self) -> None:
        self._root_level = logging.root.level
        self._root_handlers = list(logging.root.handlers)
        self._my_level = logging.getLogger("bocomadp").level
        self._app_level = logging.getLogger("app").level

    def teardown_method(self) -> None:
        for h in list(logging.root.handlers):
            logging.root.removeHandler(h)
            h.close()
        for h in self._root_handlers:
            logging.root.addHandler(h)
        logging.root.setLevel(self._root_level)
        logging.getLogger("bocomadp").setLevel(self._my_level)
        logging.getLogger("app").setLevel(self._app_level)

    def _make_config(self, *, enabled: bool, fmt: str = "text", level: str = "info"):
        from types import SimpleNamespace
        return SimpleNamespace(
            log_level=level,
            logging=SimpleNamespace(
                enhance=SimpleNamespace(enabled=enabled, format=fmt),
            ),
        )

    def test_enhanced_installs_filter_and_text_formatter(self) -> None:
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        logging.root.addHandler(h)
        configure_logging(self._make_config(enabled=True, fmt="text"))
        assert _has_trace_filter(h)
        assert isinstance(h.formatter, TraceTextFormatter)

    def test_enhanced_installs_json_formatter(self) -> None:
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        logging.root.addHandler(h)
        configure_logging(self._make_config(enabled=True, fmt="json"))
        assert _has_trace_filter(h)
        assert isinstance(h.formatter, JsonTraceFormatter)

    def test_disabled_removes_filter_and_formatter(self) -> None:
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        h.addFilter(TraceContextFilter())
        h.setFormatter(_trace_formatter("text"))
        logging.root.addHandler(h)
        configure_logging(self._make_config(enabled=False))
        assert not _has_trace_filter(h)
        assert not getattr(h.formatter, "_my_trace_formatter", False)

    def test_apply_logging_level_sets_my_and_app(self) -> None:
        apply_logging_level("debug")
        assert logging.getLogger("bocomadp").level == logging.DEBUG
        assert logging.getLogger("app").level == logging.DEBUG

    def test_apply_logging_level_does_not_raise_root(self) -> None:
        logging.root.setLevel(logging.WARNING)
        apply_logging_level("error")
        assert logging.root.level == logging.WARNING

    def test_end_to_end_log_line_carries_trace_id(self) -> None:
        stream = io.StringIO()
        h = logging.StreamHandler(stream)
        logging.root.addHandler(h)
        configure_logging(self._make_config(enabled=True, fmt="json"))

        lg = logging.getLogger("bocomadp.test")
        lg.setLevel(logging.DEBUG)
        with request_trace_context("e2e-tid"):
            lg.info("end to end")

        line = stream.getvalue().strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["trace_id"] == "e2e-tid"
        assert payload["message"] == "end to end"
