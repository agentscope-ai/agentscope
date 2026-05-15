# -*- coding: utf-8 -*-
"""Unified test runner for AgentScope model scripts.

Automatically reads API keys from environment variables and only runs tests
for providers whose keys are present. Supports fine-grained control over
which providers and test types to run.

Usage examples:
    # Run all available tests (auto-detected from env vars)
    python scripts/model_examples/run_tests.py

    # Run only specific providers
    python scripts/model_examples/run_tests.py --providers openai_chat,
    anthropic,gemini

    # Run only specific test types
    python scripts/model_examples/run_tests.py --tests call,multiagent

    # Combine: only call tests for openai and dashscope
    python scripts/model_examples/run_tests.py --providers openai_chat,
    dashscope --tests call

    # List all providers and their env var / availability status
    python scripts/model_examples/run_tests.py --list

    # Include ollama even if server may not be running
    python scripts/model_examples/run_tests.py --providers ollama

    # Set a per-test timeout (seconds, default 120)
    python scripts/model_examples/run_tests.py --timeout 60

    # Stream each script's output to the terminal (default: suppressed,
    shown only on failure)
    python scripts/model_examples/run_tests.py --verbose
"""
import argparse
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Provider definitions
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent


@dataclass
class Provider:
    """Metadata for a model provider."""

    name: str
    env_var: Optional[str]  # None for local providers (e.g. Ollama)
    file_prefix: str  # e.g. "openai_chat_model"
    supported_tests: list[str] = field(default_factory=list)
    description: str = ""

    def is_available(self) -> bool:
        """Return True if the provider's credentials are present."""
        if self.env_var is None:
            # Ollama: check if server is reachable
            return _ollama_is_running()
        return bool(os.environ.get(self.env_var, "").strip())

    def script_path(self, test_type: str) -> Optional[Path]:
        """Return the script path for the given test type, or None if it
        doesn't exist."""
        path = SCRIPTS_DIR / f"{self.file_prefix}_{test_type}.py"
        return path if path.exists() else None


def _ollama_is_running() -> bool:
    """Check whether an Ollama server is reachable."""
    import urllib.request
    import urllib.error

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3):
            pass
        return True
    except Exception:
        return False


ALL_PROVIDERS: list[Provider] = [
    Provider(
        name="openai_chat",
        env_var="OPENAI_API_KEY",
        file_prefix="openai_chat",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="OpenAI Chat Completions API (gpt-4.1, etc.)",
    ),
    Provider(
        name="openai_response",
        env_var="OPENAI_API_KEY",
        file_prefix="openai_response",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="OpenAI Responses API (o1, o3, etc.)",
    ),
    Provider(
        name="anthropic",
        env_var="ANTHROPIC_API_KEY",
        file_prefix="anthropic",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="Anthropic Claude models",
    ),
    Provider(
        name="dashscope",
        env_var="DASHSCOPE_API_KEY",
        file_prefix="dashscope",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="Alibaba DashScope / Qwen models",
    ),
    Provider(
        name="deepseek",
        env_var="DEEPSEEK_API_KEY",
        file_prefix="deepseek",
        supported_tests=["call", "multiagent"],
        description="DeepSeek models (no multimodal support)",
    ),
    Provider(
        name="gemini",
        env_var="GEMINI_API_KEY",
        file_prefix="gemini",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="Google Gemini models",
    ),
    Provider(
        name="moonshot",
        env_var="MOONSHOT_API_KEY",
        file_prefix="moonshot",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="Moonshot AI (Kimi) models",
    ),
    Provider(
        name="xai",
        env_var="XAI_API_KEY",
        file_prefix="xai",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="xAI Grok models",
    ),
    Provider(
        name="ollama",
        env_var=None,
        file_prefix="ollama",
        supported_tests=[
            "call",
            "multiagent",
            "multimodal",
            "multiagent_multimodal",
        ],
        description="Ollama local models (requires running server)",
    ),
]

PROVIDER_MAP: dict[str, Provider] = {p.name: p for p in ALL_PROVIDERS}
ALL_TEST_TYPES = ["call", "multiagent", "multimodal", "multiagent_multimodal"]

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"


def _color(text: str, color: str) -> str:
    """Wrap *text* in ANSI *color* escape codes when stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{color}{text}{COLOR_RESET}"
    return text


@dataclass
class TestResult:
    """Result of a single test run."""

    provider: str
    test_type: str
    status: str  # PASS / FAIL / SKIP
    reason: str = ""
    duration: float = 0.0
    output: str = ""  # captured stdout+stderr (only when not streaming)


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_script(
    script_path: Path,
    timeout: int,
    verbose: bool,
) -> tuple[str, float, str]:
    """Run a script as a subprocess; return (status, elapsed_seconds, output).

    In verbose mode the subprocess output streams directly to the terminal and
    the returned output string is empty.  In quiet mode (default) output is
    captured; it is printed only when the test fails.
    """
    start = time.monotonic()
    try:
        if verbose:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                timeout=timeout,
                text=True,
                check=False,
            )
            captured = ""
        else:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False,
            )
            captured = (result.stdout or "") + (result.stderr or "")
        elapsed = time.monotonic() - start
        status = PASS if result.returncode == 0 else FAIL
        return status, elapsed, captured
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        return FAIL, elapsed, f"[Timed out after {timeout}s]"


def print_header(text: str) -> None:
    """Print a prominent section header separated by a full-width rule."""
    width = 72
    print()
    print(_color("=" * width, COLOR_BLUE))
    print(_color(f"  {text}", COLOR_BOLD))
    print(_color("=" * width, COLOR_BLUE))


def print_section(text: str) -> None:
    """Print a lightweight subsection heading."""
    print()
    print(_color(f"--- {text} ---", COLOR_BLUE))


def run_all(
    providers: list[str],
    test_types: list[str],
    timeout: int,
    verbose: bool,
) -> list[TestResult]:
    """Run every requested test type for each provider and return all results.

    Providers whose credentials are absent are skipped automatically.
    For each (provider, test_type) pair the corresponding script file is
    located and executed as a subprocess.

    Args:
        providers: Ordered list of provider names to evaluate.
        test_types: List of test-type suffixes to run per provider.
        timeout: Per-script timeout in seconds.
        verbose: When True, stream subprocess output directly to the terminal.
            When False, capture it and print only on failure.

    Returns:
        A list of :class:`TestResult` objects, one per (provider, test_type).
    """
    results: list[TestResult] = []

    for pname in providers:
        provider = PROVIDER_MAP[pname]

        available = provider.is_available()
        if not available:
            if provider.env_var:
                reason = f"env var {provider.env_var} not set"
            else:
                reason = "Ollama server not reachable"
            for ttype in test_types:
                results.append(TestResult(pname, ttype, SKIP, reason))
            print_section(
                f"{pname.upper()}  [{_color('SKIP', COLOR_YELLOW)}] —"
                f" {reason}",
            )
            continue

        print_section(
            f"{pname.upper()} — running tests: {', '.join(test_types)}",
        )

        for ttype in test_types:
            if ttype not in provider.supported_tests:
                results.append(
                    TestResult(
                        pname,
                        ttype,
                        SKIP,
                        f"not supported by {pname}",
                    ),
                )
                print(
                    f"  [{_color('SKIP', COLOR_YELLOW)}] {ttype:30s}  (not "
                    f"supported)",
                )
                continue

            script = provider.script_path(ttype)
            if script is None:
                results.append(
                    TestResult(pname, ttype, SKIP, "script not found"),
                )
                print(
                    f"  [{_color('SKIP', COLOR_YELLOW)}] {ttype:30s}  ("
                    f"script not found)",
                )
                continue

            print(f"\n  >>> {script.name}")
            status, elapsed, captured = run_script(script, timeout, verbose)
            elapsed_str = f"{elapsed:.1f}s"

            if status == PASS:
                label = _color(PASS, COLOR_GREEN)
            else:
                label = _color(FAIL, COLOR_RED)
                # Always show captured output on failure (even in quiet mode)
                if captured and not verbose:
                    print(captured, end="")

            print(f"  [{label}] {ttype:30s}  {elapsed_str}")
            results.append(
                TestResult(
                    pname,
                    ttype,
                    status,
                    duration=elapsed,
                    output=captured,
                ),
            )

    return results


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def print_summary(results: list[TestResult]) -> None:
    """Print a formatted table summarising every test result.

    Args:
        results: All :class:`TestResult` objects produced by :func:`run_all`.
    """
    print_header("TEST SUMMARY")

    passes = [r for r in results if r.status == PASS]
    fails = [r for r in results if r.status == FAIL]
    skips = [r for r in results if r.status == SKIP]

    col_p = f"{len(passes):>3}"
    col_f = f"{len(fails):>3}"
    col_s = f"{len(skips):>3}"

    print(f"  {'Provider':<22} {'Test Type':<28} {'Status':<8} {'Time':>7}")
    print(f"  {'-'*22} {'-'*28} {'-'*8} {'-'*7}")
    for r in results:
        if r.status == PASS:
            status_str = _color(r.status, COLOR_GREEN)
        elif r.status == FAIL:
            status_str = _color(r.status, COLOR_RED)
        else:
            status_str = _color(r.status, COLOR_YELLOW)
        time_str = (
            f"{r.duration:.1f}s"
            if r.duration
            else (r.reason[:20] if r.reason else "")
        )
        print(
            f"  {r.provider:<22} {r.test_type:<28} {status_str:<18}"
            f" {time_str:>7}",
        )

    print()
    total = len(results)
    summary_line = (
        f"  Total: {total}  |  "
        f"{_color('PASS', COLOR_GREEN)}: {col_p}  |  "
        f"{_color('FAIL', COLOR_RED)}: {col_f}  |  "
        f"{_color('SKIP', COLOR_YELLOW)}: {col_s}"
    )
    print(summary_line)
    print()

    if fails:
        print(_color("  Failed tests:", COLOR_RED))
        for r in fails:
            print(f"    - {r.provider} / {r.test_type}")
        print()


# ---------------------------------------------------------------------------
# --list mode
# ---------------------------------------------------------------------------


def print_provider_list() -> None:
    """Print a status table of all registered providers and their
    availability."""
    print_header("PROVIDER STATUS")
    print(f"  {'Provider':<22} {'Env Var':<25} {'Available':<12} Description")
    print(f"  {'-'*22} {'-'*25} {'-'*12} {'-'*30}")
    for p in ALL_PROVIDERS:
        avail = p.is_available()
        avail_str = (
            _color("YES", COLOR_GREEN) if avail else _color("NO", COLOR_RED)
        )
        env_str = p.env_var or "(local — ping server)"
        tests_str = ", ".join(p.supported_tests)
        print(f"  {p.name:<22} {env_str:<25} {avail_str:<21} {p.description}")
        print(f"  {'':22} {'Supported tests:':<25} {tests_str}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description=textwrap.dedent(__doc__),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--providers",
        "-p",
        metavar="NAME[,NAME...]",
        default=None,
        help=(
            "Comma-separated list of providers to test "
            f"(default: all). Available: {', '.join(PROVIDER_MAP)}"
        ),
    )
    parser.add_argument(
        "--tests",
        "-t",
        metavar="TYPE[,TYPE...]",
        default=None,
        help=(
            "Comma-separated list of test types to run "
            f"(default: all). Available: {', '.join(ALL_TEST_TYPES)}"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SECONDS",
        help="Per-script timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all providers with their env var and availability "
        "status, then exit",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "Stream each script's output to the terminal in real time. "
            "By default output is suppressed and only shown when a test fails."
        ),
    )
    return parser


def main() -> int:
    """Entry point: parse arguments, run tests, and return an exit code.

    Returns:
        0 if all executed tests passed (or were skipped); 1 if any test failed.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.list:
        print_provider_list()
        return 0

    # Resolve providers
    if args.providers:
        requested = [p.strip() for p in args.providers.split(",") if p.strip()]
        unknown = [p for p in requested if p not in PROVIDER_MAP]
        if unknown:
            print(f"Unknown providers: {', '.join(unknown)}")
            print(f"Available: {', '.join(PROVIDER_MAP)}")
            return 1
        providers = requested
    else:
        providers = list(PROVIDER_MAP)

    # Resolve test types
    if args.tests:
        requested_tests = [
            t.strip() for t in args.tests.split(",") if t.strip()
        ]
        unknown_tests = [t for t in requested_tests if t not in ALL_TEST_TYPES]
        if unknown_tests:
            print(f"Unknown test types: {', '.join(unknown_tests)}")
            print(f"Available: {', '.join(ALL_TEST_TYPES)}")
            return 1
        test_types = requested_tests
    else:
        test_types = ALL_TEST_TYPES

    print_header(
        f"AgentScope Model Tests  |  providers: {len(providers)}  |  test "
        f"types: {len(test_types)}",
    )
    print(f"  Providers : {', '.join(providers)}")
    print(f"  Test types: {', '.join(test_types)}")
    print(f"  Timeout   : {args.timeout}s per script")

    results = run_all(providers, test_types, args.timeout, args.verbose)
    print_summary(results)

    # Exit with non-zero if any test failed
    failed = any(r.status == FAIL for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
