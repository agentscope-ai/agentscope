# -*- coding: utf-8 -*-
"""Tests for scripts/model_examples/run_tests.py subprocess decoding."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "scripts" / "model_examples" / "run_tests.py"

_spec = importlib.util.spec_from_file_location(
    "run_tests_module", _RUNNER_PATH
)
# Register before exec so dataclass annotation resolution can find the module.
sys.modules["run_tests_module"] = None
run_tests_module = importlib.util.module_from_spec(_spec)
sys.modules["run_tests_module"] = run_tests_module
_spec.loader.exec_module(run_tests_module)


class RunTestsDecodeTest(unittest.TestCase):
    """run_script must survive non-UTF-8 host locales."""

    def test_quiet_capture_survives_non_utf8_locale(self) -> None:
        """Captured child output decodes as UTF-8 regardless of the host
        locale, instead of crashing the runner with UnicodeDecodeError."""

        with tempfile.TemporaryDirectory() as tmp:
            child = Path(tmp) / "child.py"
            # U+4E01 encodes to UTF-8 bytes E4 B8 81; byte 0x81 is undefined
            # in cp1252, so a locale-decoding parent would raise.
            child.write_text("print(chr(0x4E01))", encoding="utf-8")

            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            with mock.patch.dict(os.environ, env, clear=True):
                # Force the parent's default decode encoding to cp1252,
                # mimicking a non-UTF-8 host locale on any machine.
                with mock.patch(
                    "subprocess._text_encoding",
                    return_value="cp1252",
                ):
                    status, _, output = run_tests_module.run_script(
                        child,
                        timeout=60,
                        verbose=False,
                    )

        self.assertEqual(status, run_tests_module.PASS)
        self.assertIn("丁", output)


if __name__ == "__main__":
    unittest.main()
