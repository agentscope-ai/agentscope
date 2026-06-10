# -*- coding: utf-8 -*-
"""Static security scanner for skill markdown and support files.

Ported from the Java ``SkillSecurityScanner`` regex library.
This is a lint-style check, not a sandbox replacement — skills still run
inside the workspace sandbox configured by the host.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Callable


class Severity(enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Category(enum.Enum):
    EXFILTRATION = "EXFILTRATION"
    INJECTION = "INJECTION"
    DESTRUCTIVE = "DESTRUCTIVE"
    PERSISTENCE = "PERSISTENCE"
    NETWORK = "NETWORK"
    OBFUSCATION = "OBFUSCATION"


class Verdict(enum.Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DANGEROUS = "DANGEROUS"


class TrustLevel(enum.Enum):
    BUILTIN = "BUILTIN"
    TRUSTED = "TRUSTED"
    COMMUNITY = "COMMUNITY"
    AGENT_CREATED = "AGENT_CREATED"


@dataclass(frozen=True)
class Finding:
    pattern_id: str
    severity: Severity
    category: Category
    file: str
    line: int
    match_text: str
    description: str


@dataclass(frozen=True)
class ScanResult:
    verdict: Verdict
    findings: list[Finding]
    report_text: str


# ------------------------------------------------------------------
# Rule library (mirrors Java SkillSecurityScanner)
# ------------------------------------------------------------------

_RULES: list[tuple[str, Severity, Category, re.Pattern, str]] = [
    # EXFILTRATION
    (
        "exfil-curl-post",
        Severity.HIGH,
        Category.EXFILTRATION,
        re.compile(r"curl\s+[^\n]*\s(-d|--data|-F|--data-binary)\s"),
        "curl POST upload — possible data exfiltration",
    ),
    (
        "exfil-wget-post",
        Severity.HIGH,
        Category.EXFILTRATION,
        re.compile(r"wget\s+(?:[^\n]*\s)?--post-data\b"),
        "wget --post-data — possible data exfiltration",
    ),
    (
        "exfil-nc-pipe",
        Severity.HIGH,
        Category.EXFILTRATION,
        re.compile(r"\b(cat|tar|gzip)\s+[^\n]*\|\s*nc\s"),
        "piping local data into netcat — exfiltration",
    ),
    # INJECTION
    (
        "inj-ignore-prev",
        Severity.MEDIUM,
        Category.INJECTION,
        re.compile(r"ignore\s+(all\s+)?(your\s+)?previous\s+instructions", re.IGNORECASE),
        "prompt-injection marker: 'ignore previous instructions'",
    ),
    (
        "inj-system-tag",
        Severity.MEDIUM,
        Category.INJECTION,
        re.compile(r"<\s*(system|admin)\s*>", re.IGNORECASE),
        "prompt-injection marker: <system> / <admin> tag inside body",
    ),
    (
        "inj-jailbreak",
        Severity.MEDIUM,
        Category.INJECTION,
        re.compile(r"\b(DAN|jailbreak|developer\s+mode)\b", re.IGNORECASE),
        "prompt-injection marker: jailbreak vocabulary",
    ),
    # DESTRUCTIVE
    (
        "dest-rm-rf",
        Severity.CRITICAL,
        Category.DESTRUCTIVE,
        re.compile(r"rm\s+(?:-[a-zA-Z]*\s+)?-?rf\s+/(?:\s|$)"),
        "rm -rf / — destructive",
    ),
    (
        "dest-mkfs",
        Severity.CRITICAL,
        Category.DESTRUCTIVE,
        re.compile(r"\bmkfs\."),
        "mkfs — filesystem destruction",
    ),
    (
        "dest-dd-dev",
        Severity.CRITICAL,
        Category.DESTRUCTIVE,
        re.compile(r"\bdd\s+.*\bof=/dev/"),
        "dd to /dev — destructive",
    ),
    # PERSISTENCE
    (
        "pers-crontab",
        Severity.HIGH,
        Category.PERSISTENCE,
        re.compile(r"\b(crontab\s+-e|crontab\s+[^\n]*\b)"),
        "crontab modification — persistence",
    ),
    (
        "pers-systemd-install",
        Severity.HIGH,
        Category.PERSISTENCE,
        re.compile(
            r"(systemctl\s+enable\s+|cp\s+[^\n]*\.service\s+/etc/systemd"
            r"/system|/etc/systemd/system/[^\s]+\.service)"
        ),
        "installs systemd unit — persistence",
    ),
    (
        "pers-rc-tamper",
        Severity.MEDIUM,
        Category.PERSISTENCE,
        re.compile(r"echo\s+[^\n]*>>\s+~?/?(\.bashrc|\.zshrc|\.profile)"),
        "writes shell-rc — persistence",
    ),
    # NETWORK
    (
        "net-reverse-shell-bash",
        Severity.CRITICAL,
        Category.NETWORK,
        re.compile(r"bash\s+-i\s+>&\s*/dev/tcp/"),
        "bash reverse shell",
    ),
    (
        "net-nc-listen",
        Severity.HIGH,
        Category.NETWORK,
        re.compile(r"\bnc\s+(?:[^\n]*\s)?-(l|lvp|nlvp)\b"),
        "netcat listener",
    ),
    (
        "net-nc-exec",
        Severity.CRITICAL,
        Category.NETWORK,
        re.compile(r"\bnc\s+(?:[^\n]*\s)?-e\b"),
        "netcat -e (command execution)",
    ),
    # OBFUSCATION
    (
        "obf-base64-pipe-shell",
        Severity.HIGH,
        Category.OBFUSCATION,
        re.compile(r"base64\s+(-d|--decode)\b[^\n]*\|\s*(bash|sh|zsh)"),
        "base64 -d | shell — obfuscated execution",
    ),
    (
        "obf-eval-curl",
        Severity.CRITICAL,
        Category.OBFUSCATION,
        re.compile(r"\beval\s+[^\n]*\$\(\s*(curl|wget)\b"),
        "eval $(curl ...) — remote-code execution",
    ),
    (
        "obf-curl-pipe-shell",
        Severity.HIGH,
        Category.OBFUSCATION,
        re.compile(r"(curl|wget)\s+[^\n]*\|\s*(bash|sh|zsh)"),
        "curl/wget piped to shell — remote-code execution",
    ),
]


def _line_number_at(content: str, char_index: int) -> int:
    return content[:char_index].count("\n") + 1


def _scan_text(file_label: str, content: str) -> list[Finding]:
    findings: list[Finding] = []
    if not content:
        return findings
    for rid, severity, category, pattern, description in _RULES:
        for m in pattern.finditer(content):
            line = _line_number_at(content, m.start())
            text = m.group()
            if len(text) > 200:
                text = text[:200] + "…"
            findings.append(
                Finding(
                    pattern_id=rid,
                    severity=severity,
                    category=category,
                    file=file_label,
                    line=line,
                    match_text=text,
                    description=description,
                )
            )
    return findings


def _verdict_for(findings: list[Finding]) -> Verdict:
    if not findings:
        return Verdict.SAFE
    for f in findings:
        if f.severity in (Severity.CRITICAL, Severity.HIGH):
            return Verdict.DANGEROUS
    return Verdict.CAUTION


def _format_report(title: str, verdict: Verdict, findings: list[Finding]) -> str:
    lines = [f"Security scan: {title} — {verdict.value}"]
    if not findings:
        lines.append("  (no findings)")
        return "\n".join(lines)
    for f in findings:
        lines.append(
            f"  [{f.severity.value}/{f.category.value}] {f.pattern_id} @ "
            f"{f.file}:{f.line} — {f.description}\n"
            f"      match: {f.match_text}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def scan_skill(skill_name: str, skill_md: str, resources: dict[str, str] | None = None) -> ScanResult:
    """Scan a complete skill: SKILL.md + every support file."""
    all_findings: list[Finding] = []
    if skill_md:
        all_findings.extend(_scan_text("SKILL.md", skill_md))
    for rel_path, text in (resources or {}).items():
        if rel_path and text:
            all_findings.extend(_scan_text(rel_path, text))
    verdict = _verdict_for(all_findings)
    return ScanResult(
        verdict=verdict,
        findings=all_findings,
        report_text=_format_report(skill_name, verdict, all_findings),
    )


def scan_single_file(rel_path: str, content: str) -> ScanResult:
    """Scan a single file (e.g. after write_file / patch)."""
    findings = _scan_text(rel_path, content)
    verdict = _verdict_for(findings)
    return ScanResult(
        verdict=verdict,
        findings=findings,
        report_text=_format_report(rel_path, verdict, findings),
    )


def should_allow(trust: TrustLevel, verdict: Verdict) -> bool:
    """Map (trust × verdict) to an install decision.

    Returns ``True`` if the skill should be allowed to install.
    """
    if trust == TrustLevel.BUILTIN:
        return True
    if trust == TrustLevel.TRUSTED:
        return verdict != Verdict.DANGEROUS
    if trust == TrustLevel.COMMUNITY:
        return verdict == Verdict.SAFE
    if trust == TrustLevel.AGENT_CREATED:
        return verdict != Verdict.DANGEROUS
    return False


def install_policy_decision(
    skill_name: str,
    skill_md: str,
    trust: TrustLevel,
    resources: dict[str, str] | None = None,
) -> tuple[bool, ScanResult]:
    """One-shot helper: scan + policy decision.

    Returns:
        ``(allowed, scan_result)``
    """
    result = scan_skill(skill_name, skill_md, resources)
    return should_allow(trust, result.verdict), result
