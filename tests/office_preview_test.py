from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from agentscope.app._service._office_preview import (
    MAX_OFFICE_PREVIEW_BYTES,
    convert_office_document,
)


class OfficePreviewTest(TestCase):
    def test_rejects_unsupported_extensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only DOCX and PPTX"):
            convert_office_document(b"content", "report.pdf")

    def test_rejects_oversized_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "50 MiB or smaller"):
            convert_office_document(
                b"x" * (MAX_OFFICE_PREVIEW_BYTES + 1),
                "report.docx",
            )

    @patch("agentscope.app._service._office_preview.shutil.which", return_value=None)
    def test_requires_libreoffice(self, _which: object) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "LibreOffice"):
            convert_office_document(b"content", "report.docx")

    @patch("agentscope.app._service._office_preview.subprocess.run")
    @patch(
        "agentscope.app._service._office_preview.shutil.which",
        return_value="/usr/bin/libreoffice",
    )
    def test_returns_generated_pdf(self, _which: object, run: object) -> None:
        def convert(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            output = Path(command[command.index("--outdir") + 1]) / "document.pdf"
            output.write_bytes(b"%PDF-1.7 test")
            return subprocess.CompletedProcess(command, 0, "", "")

        run.side_effect = convert  # type: ignore[attr-defined]
        self.assertEqual(
            convert_office_document(b"office bytes", "report.docx"),
            b"%PDF-1.7 test",
        )
        command = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertIn("--headless", command)
        self.assertIn("--convert-to", command)
        self.assertIn("pdf", command)

    @patch(
        "agentscope.app._service._office_preview.shutil.which",
        return_value="/usr/bin/libreoffice",
    )
    @patch(
        "agentscope.app._service._office_preview.subprocess.run",
        side_effect=subprocess.TimeoutExpired("libreoffice", 45),
    )
    def test_conversion_timeout_is_propagated(
        self, _run: object, _which: object
    ) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            convert_office_document(b"office bytes", "report.pptx")

    def test_rejects_oversized_generated_pdf(self) -> None:
        def convert(
            command: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            output = Path(command[command.index("--outdir") + 1]) / "document.pdf"
            output.write_bytes(b"12345")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch(
                "agentscope.app._service._office_preview.shutil.which",
                return_value="/usr/bin/libreoffice",
            ),
            patch(
                "agentscope.app._service._office_preview.subprocess.run",
                side_effect=convert,
            ),
            patch(
                "agentscope.app._service._office_preview.MAX_PDF_PREVIEW_BYTES",
                4,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "too large"):
                convert_office_document(b"office bytes", "report.docx")
