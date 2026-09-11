# -*- coding: utf-8 -*-
"""Get this machine ready to render: Blender 5.1+ with its MCP add-on.

``main.py`` starts and stops Blender itself, but it cannot install one.
That is what this script is for, and it only has to run once::

    python setup_blender.py
    python setup_blender.py --blender /path/to/blender   # already have one

The add-on is the part that actually needs a script: it is not on
extensions.blender.org, so it has to be built from source and installed
through Blender's own CLI. Getting the binary is the easy half, and this
delegates it to whatever the platform already uses.

Stdlib only, so it runs before anything else is set up.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

# Pinned rather than "latest": the add-on requires 5.1+, and a build that
# has been tried beats one that merely should work.
BLENDER_VERSION = "5.2.1"
LINUX_TARBALL = (
    f"https://download.blender.org/release/"
    f"Blender{BLENDER_VERSION.rsplit('.', 1)[0]}/"
    f"blender-{BLENDER_VERSION}-linux-x64.tar.xz"
)
ADDON_REPO = "https://projects.blender.org/lab/blender_mcp.git"
MIN_VERSION = (5, 1)


def blender_version(blender: str) -> tuple[int, ...] | None:
    """Read a Blender binary's version, or ``None`` if it will not run."""
    try:
        out = subprocess.run(
            [blender, "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    word = out.split()[1]
    return tuple(int(part) for part in word.split("."))


def install_blender(into: str) -> str:
    """Put a new enough Blender on this machine and return its path."""
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["brew", "install", "--cask", "blender"], check=True)
        return "/Applications/Blender.app/Contents/MacOS/Blender"
    if system == "Windows":
        raise RuntimeError(
            f"Install Blender {BLENDER_VERSION}, then re-run with "
            f"--blender pointing at blender.exe:\n"
            f"    winget install BlenderFoundation.Blender "
            f"--version {BLENDER_VERSION}",
        )

    os.makedirs(into, exist_ok=True)
    print(f"Downloading {LINUX_TARBALL}")
    # download.blender.org turns away urllib's default User-Agent.
    request = urllib.request.Request(
        LINUX_TARBALL,
        headers={"User-Agent": "agentscope"},
    )
    with tempfile.NamedTemporaryFile(suffix=".tar.xz") as archive:
        with urllib.request.urlopen(request) as response:
            shutil.copyfileobj(response, archive)
        archive.flush()
        with tarfile.open(archive.name) as tar:
            tar.extractall(into, filter="data")
    return os.path.join(
        into,
        f"blender-{BLENDER_VERSION}-linux-x64",
        "blender",
    )


def install_addon(blender: str) -> None:
    """Build the MCP add-on from source and enable it in Blender."""
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(
            ["git", "clone", "--depth", "1", ADDON_REPO, work + "/repo"],
            check=True,
        )
        subprocess.run(
            [
                blender,
                "--command",
                "extension",
                "build",
                "--source-dir",
                work + "/repo/addon/blender_mcp_addon",
                "--output-dir",
                work,
            ],
            check=True,
        )
        package = next(
            os.path.join(work, name)
            for name in os.listdir(work)
            if name.endswith(".zip")
        )
        subprocess.run(
            [
                blender,
                "--command",
                "extension",
                "install-file",
                "--repo",
                "user_default",
                "--enable",
                package,
            ],
            check=True,
        )


def main() -> None:
    """Install what is missing, then prove the add-on answers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=shutil.which("blender"))
    parser.add_argument(
        "--install-dir",
        default=os.path.expanduser("~/.local/share/blender"),
        help="Where to unpack Blender when one has to be downloaded.",
    )
    args = parser.parse_args()

    blender = args.blender
    version = blender_version(blender) if blender else None
    if version is None or version < MIN_VERSION:
        found = f"{blender} is {version}" if version else "none found"
        print(f"Blender {MIN_VERSION[0]}.{MIN_VERSION[1]}+ needed ({found})")
        blender = install_blender(args.install_dir)
        version = blender_version(blender)
    print(f"Blender {version} at {blender}")

    install_addon(blender)

    # The add-on registers this command, so its help is the proof.
    check = subprocess.run(
        [
            blender,
            "--background",
            "--online-mode",
            "--command",
            "blender_mcp",
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if "--port" not in check.stdout:
        sys.exit(f"The add-on did not register:\n{check.stdout}{check.stderr}")

    print(
        f"\nReady. Run the demo with:\n    python main.py --blender {blender}",
    )
    if blender == shutil.which("blender"):
        print("(or just `python main.py` — this Blender is on your PATH)")


if __name__ == "__main__":
    main()
