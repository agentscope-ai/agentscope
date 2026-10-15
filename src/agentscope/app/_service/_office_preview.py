"""Convert supported Office documents to PDF for browser previews."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_OFFICE_PREVIEW_BYTES = 50 * 1024 * 1024
MAX_PDF_PREVIEW_BYTES = 100 * 1024 * 1024
OFFICE_PREVIEW_TIMEOUT_SECONDS = 45
OFFICE_PREVIEW_UPLOAD_TIMEOUT_SECONDS = 30


def convert_office_document(content: bytes, filename: str) -> bytes:
    """Convert one DOCX or PPTX document to PDF using LibreOffice."""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".docx", ".pptx"}:
        raise ValueError("Only DOCX and PPTX files can be previewed.")
    if len(content) > MAX_OFFICE_PREVIEW_BYTES:
        raise ValueError("Office preview files must be 50 MiB or smaller.")

    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable is None:
        raise FileNotFoundError(
            "LibreOffice is required for Office previews. Download the original file instead.",
        )

    with tempfile.TemporaryDirectory(prefix="agentscope-office-preview-") as tmp:
        root = Path(tmp)
        source = root / f"document{suffix}"
        output_dir = root / "output"
        output_dir.mkdir()
        source.write_bytes(content)
        profile = (root / "profile").as_uri()
        result = subprocess.run(
            [
                executable,
                f"-env:UserInstallation={profile}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(source),
            ],
            capture_output=True,
            check=False,
            cwd=root,
            env={"HOME": str(root), "PATH": os.environ.get("PATH", "")},
            text=True,
            timeout=OFFICE_PREVIEW_TIMEOUT_SECONDS,
        )
        pdf_path = output_dir / "document.pdf"
        if result.returncode != 0 or not pdf_path.is_file():
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                detail or "LibreOffice could not convert this document.",
            )
        pdf_size = pdf_path.stat().st_size
        if pdf_size == 0:
            raise RuntimeError("LibreOffice returned an empty PDF preview.")
        if pdf_size > MAX_PDF_PREVIEW_BYTES:
            raise RuntimeError("The converted preview is too large to display.")
        return pdf_path.read_bytes()
