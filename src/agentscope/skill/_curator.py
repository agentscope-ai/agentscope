# -*- coding: utf-8 -*-
"""Simplified skill curator with lifecycle management and security scanning.

Provides automatic state transitions and security gating for agent-created
skills.  A full-blown usage-store + ledger system is left for future
expansion; this version focuses on the guard-rails that map directly from
the Java harness.
"""
from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ._security_scanner import (
    Finding,
    ScanResult,
    TrustLevel,
    Verdict,
    scan_skill,
    should_allow,
)
from .._logging import logger


class SkillState(enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


@dataclass
class SkillMeta:
    """Lightweight metadata for a curated skill."""

    name: str
    created_by: str = "agent"
    state: SkillState = SkillState.ACTIVE
    pinned: bool = False
    created_at: float = field(default_factory=time.time)
    latest_activity_at: float = field(default_factory=time.time)
    scan_verdict: Verdict = Verdict.SAFE
    scan_findings: list[Finding] = field(default_factory=list)


class SkillCurator:
    """Manages skill lifecycle and security screening.

    Usage::

        curator = SkillCurator(state_dir=".agentscope/skills")
        meta = curator.register("my_skill", skill_markdown)
        if meta.state == SkillState.ACTIVE:
            toolkit.register_skill(...)
    """

    def __init__(self, state_dir: str = ".agentscope/skills") -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.state_dir / "_curator_state.json"
        self._skills: dict[str, SkillMeta] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                for name, data in raw.items():
                    data["state"] = SkillState(data["state"])
                    data["scan_verdict"] = Verdict(data["scan_verdict"])
                    data["scan_findings"] = [
                        Finding(**f) for f in data.get("scan_findings", [])
                    ]
                    self._skills[name] = SkillMeta(**data)
            except Exception as e:
                logger.warning("SkillCurator load_state failed: %s", e)

    def _save_state(self) -> None:
        try:
            payload: dict[str, dict] = {}
            for name, meta in self._skills.items():
                payload[name] = {
                    "name": meta.name,
                    "created_by": meta.created_by,
                    "state": meta.state.value,
                    "pinned": meta.pinned,
                    "created_at": meta.created_at,
                    "latest_activity_at": meta.latest_activity_at,
                    "scan_verdict": meta.scan_verdict.value,
                    "scan_findings": [
                        {
                            "pattern_id": f.pattern_id,
                            "severity": f.severity.value,
                            "category": f.category.value,
                            "file": f.file,
                            "line": f.line,
                            "match_text": f.match_text,
                            "description": f.description,
                        }
                        for f in meta.scan_findings
                    ],
                }
            self._state_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("SkillCurator save_state failed: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        skill_md: str,
        resources: dict[str, str] | None = None,
        trust: TrustLevel = TrustLevel.AGENT_CREATED,
        created_by: str = "agent",
    ) -> SkillMeta:
        """Register (or re-scan) a skill and apply security gating.

        Returns the updated metadata.  The caller should check
        ``meta.state`` to decide whether to install the skill.
        """
        result = scan_skill(name, skill_md, resources)
        allowed = should_allow(trust, result.verdict)

        meta = self._skills.get(name)
        if meta is None:
            meta = SkillMeta(name=name, created_by=created_by)

        meta.scan_verdict = result.verdict
        meta.scan_findings = list(result.findings)
        meta.latest_activity_at = time.time()

        if not allowed:
            meta.state = SkillState.ARCHIVED
            logger.warning(
                "[SkillCurator] Skill '%s' blocked by security scan (%s). "
                "Findings: %d",
                name,
                result.verdict.value,
                len(result.findings),
            )
        elif meta.state == SkillState.ARCHIVED and allowed:
            # Re-activation after fix
            meta.state = SkillState.ACTIVE
            logger.info(
                "[SkillCurator] Skill '%s' re-activated after scan.",
                name,
            )

        self._skills[name] = meta
        self._save_state()
        return meta

    def promote(self, name: str) -> SkillMeta | None:
        """Promote a DRAFT skill to ACTIVE (e.g. after human review)."""
        meta = self._skills.get(name)
        if meta is None:
            return None
        if meta.state == SkillState.DRAFT:
            meta.state = SkillState.ACTIVE
            meta.latest_activity_at = time.time()
            self._save_state()
            logger.info("[SkillCurator] Promoted '%s' to ACTIVE.", name)
        return meta

    def archive(self, name: str) -> SkillMeta | None:
        """Manually archive a skill."""
        meta = self._skills.get(name)
        if meta is None:
            return None
        meta.state = SkillState.ARCHIVED
        self._save_state()
        logger.info("[SkillCurator] Archived '%s'.", name)
        return meta

    def touch(self, name: str) -> None:
        """Record activity for a skill (prevents STALE transition)."""
        meta = self._skills.get(name)
        if meta is not None:
            meta.latest_activity_at = time.time()
            self._save_state()

    def apply_transitions(
        self,
        stale_after_days: float = 30.0,
        archive_after_days: float = 90.0,
    ) -> dict[str, int]:
        """Auto-transition ACTIVE→STALE→ARCHIVED based on inactivity.

        Returns counts: ``{'checked': N, 'stale': N, 'archived': N, 'reactivated': N}``
        """
        now = time.time()
        stale_cutoff = now - stale_after_days * 86400
        archive_cutoff = now - archive_after_days * 86400

        counts = {"checked": 0, "stale": 0, "archived": 0, "reactivated": 0}

        for meta in self._skills.values():
            if meta.pinned or meta.created_by != "agent":
                continue
            counts["checked"] += 1

            anchor = meta.latest_activity_at or meta.created_at

            if meta.state == SkillState.ACTIVE and anchor < stale_cutoff:
                meta.state = SkillState.STALE
                counts["stale"] += 1
                logger.info("[SkillCurator] '%s' marked STALE.", meta.name)

            elif meta.state == SkillState.STALE and anchor < archive_cutoff:
                meta.state = SkillState.ARCHIVED
                counts["archived"] += 1
                logger.info("[SkillCurator] '%s' archived.", meta.name)

            elif meta.state == SkillState.STALE and anchor >= stale_cutoff:
                meta.state = SkillState.ACTIVE
                counts["reactivated"] += 1
                logger.info("[SkillCurator] '%s' reactivated.", meta.name)

        self._save_state()
        return counts

    def list_skills(self, state: SkillState | None = None) -> list[SkillMeta]:
        """Return metadata for all (or filtered) skills."""
        if state is None:
            return list(self._skills.values())
        return [m for m in self._skills.values() if m.state == state]

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)
