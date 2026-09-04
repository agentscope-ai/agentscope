# -*- coding: utf-8 -*-
"""Build the self-contained AgentScope Desktop backend."""
import os
import sysconfig
from pathlib import Path

EXCLUDED_MODULES = (
    "aioboto3",
    "boto3",
    "botocore",
    "daytona",
    "discord",
    "docutils",
    "e2b",
    "elasticsearch",
    "fakeredis",
    "IPython",
    "kubernetes",
    "llvmlite",
    "lxml",
    "matplotlib",
    "mem0",
    "moto",
    "numba",
    "openpyxl",
    "pandas",
    "PIL",
    "pptx",
    "pyarrow",
    "pymilvus",
    "pymongo",
    "pytest",
    "redis",
    "reportlab",
    "scipy",
    "sphinx",
    "torch",
    "xlrd",
)


def resolve_ripgrep_executable(scripts_dir: Path) -> Path:
    """Return the ripgrep executable installed by the desktop extra."""
    executable_name = "rg.exe" if os.name == "nt" else "rg"
    executable = scripts_dir / executable_name
    if not executable.is_file():
        raise FileNotFoundError(
            f"ripgrep executable not found: {executable}",
        )
    return executable


def main() -> None:
    """Build a platform-specific PyInstaller onedir application."""
    desktop_dir = Path(__file__).resolve().parent
    build_dir = desktop_dir / "build"
    os.environ.setdefault(
        "PYINSTALLER_CONFIG_DIR",
        str(build_dir / "pyinstaller-cache"),
    )
    os.environ.setdefault("MPLCONFIGDIR", str(build_dir / "matplotlib"))

    import PyInstaller.__main__

    ripgrep = resolve_ripgrep_executable(
        Path(sysconfig.get_path("scripts")),
    )
    arguments = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "agentscope-backend",
        "--distpath",
        str(desktop_dir / "dist"),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--additional-hooks-dir",
        str(desktop_dir / "hooks"),
        "--add-binary",
        f"{ripgrep}{os.pathsep}.",
    ]
    for module_name in EXCLUDED_MODULES:
        arguments.extend(["--exclude-module", module_name])
    arguments.append(str(desktop_dir / "main.py"))
    PyInstaller.__main__.run(arguments)


if __name__ == "__main__":
    main()
