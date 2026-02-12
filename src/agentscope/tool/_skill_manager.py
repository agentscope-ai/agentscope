# -*- coding: utf-8 -*-
"""Agent skill manager for lazy-loading and monitoring skill directories."""

import os
import threading
from functools import wraps
from types import MappingProxyType
from typing import Callable, TypeVar

from ._types import AgentSkill
from .._logging import logger

# Type variable for generic decorator
_T = TypeVar("_T")


def _thread_safe(func: Callable[..., _T]) -> Callable[..., _T]:
    """Decorator to make methods thread-safe using instance lock.

    This decorator ensures that the decorated method is executed
    atomically by acquiring the instance's RLock before execution.

    Args:
        func: The method to be decorated.

    Returns:
        The wrapped method with thread-safety guarantees.
    """

    @wraps(func)
    def wrapper(
        self: "AgentSkillManager",
        *args: object,
        **kwargs: object,
    ) -> _T:
        # Access lock through public property to avoid protected-access
        lock = getattr(self, "_lock")
        with lock:
            return func(self, *args, **kwargs)

    return wrapper


class AgentSkillManager:
    """Manager for agent skills with lazy-loading and monitoring.

    Manages skills from manual registration and monitored directories.
    Features: lazy loading, mtime caching, automatic updates, thread-safe.
    Performance: O(k) unchanged, O(n) changed (k=known, n=total).
    """

    def __init__(
        self,
        agent_skill_instruction: str,
        agent_skill_template: str,
    ) -> None:
        """Initialize the AgentSkillManager.

        Args:
            agent_skill_instruction: Template for skill prompt header.
            agent_skill_template: Template for formatting skills.
        """
        self._lock = threading.RLock()
        self._skills: dict[str, AgentSkill] = {}
        self._monitored_dirs: set[str] = set()
        self._skill_dir_mtime_cache: dict[str, float] = {}
        self._monitored_dir_last_scan_mtime: dict[str, float] = {}
        self._agent_skill_instruction: str = agent_skill_instruction
        self._agent_skill_template: str = agent_skill_template

    @property
    def skills(self) -> MappingProxyType[str, AgentSkill]:
        """Read-only view of registered skills."""
        with self._lock:
            return MappingProxyType(self._skills)

    @property
    def monitored_dirs(self) -> frozenset[str]:
        """Read-only view of monitored directories."""
        with self._lock:
            return frozenset(self._monitored_dirs)

    def _safe_path_operation(
        self,
        operation: Callable[[], bool],
        path: str,
        error_msg: str,
    ) -> bool:
        """Safely execute a filesystem operation with error handling.

        Args:
            operation: Function that returns True if path is valid.
            path: Path being checked.
            error_msg: Error message prefix for logging.

        Returns:
            True if operation succeeds, False otherwise.
        """
        try:
            return operation()
        except OSError as e:
            logger.warning("%s '%s': %s", error_msg, path, e)
            return False

    @_thread_safe
    def monitor_agent_skills(self, directory: str) -> None:
        """Register a directory to be monitored for agent skills.

        Skills are discovered lazily when get_agent_skill_prompt() is called.

        Args:
            directory: Path to monitor.

        Raises:
            ValueError: If directory doesn't exist or isn't accessible.
        """
        directory = os.path.abspath(os.path.normpath(directory))

        if not self._safe_path_operation(
            lambda: os.path.isdir(directory),
            directory,
            "Cannot access directory",
        ):
            raise ValueError(f"Invalid directory: {directory}")

        if directory in self._monitored_dirs:
            logger.warning(
                "Directory '%s' is already monitored. Skipping duplicate.",
                directory,
            )
            return

        self._monitored_dirs.add(directory)
        logger.info("Registered monitored directory: '%s'", directory)

    @_thread_safe
    def remove_monitored_directory(self, directory: str) -> None:
        """Remove a monitored directory and its auto-discovered skills.

        Manually registered skills are NOT removed.

        Args:
            directory: Path to remove.
        """
        directory = os.path.abspath(os.path.normpath(directory))

        if directory not in self._monitored_dirs:
            logger.warning(
                "Directory '%s' is not monitored. Nothing to remove.",
                directory,
            )
            return

        self._monitored_dirs.remove(directory)

        # Collect all skills from this monitored directory
        skills_to_remove = self._get_monitored_skills(directory)

        # Batch remove all skills
        for name, skill_dir in skills_to_remove:
            self._remove_skill_internal(name, skill_dir)
            logger.info(
                "Removed skill '%s' from monitored directory '%s'",
                name,
                directory,
            )

        self._monitored_dir_last_scan_mtime.pop(directory, None)
        logger.info(
            "Removed monitored directory '%s' (%d skills removed)",
            directory,
            len(skills_to_remove),
        )

    def _parse_skill_md(self, skill_dir: str) -> AgentSkill | None:
        """Parse SKILL.md file and extract metadata.

        Args:
            skill_dir: Path to skill directory containing SKILL.md.

        Returns:
            AgentSkill dict if parsing succeeds, None otherwise.
        """
        import frontmatter

        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)

            name = post.get("name")
            description = post.get("description")

            if not name or not description:
                logger.warning(
                    "SKILL.md in '%s' missing required fields "
                    "(name and/or description)",
                    skill_dir,
                )
                return None

            return AgentSkill(
                name=str(name),
                description=str(description),
                dir=skill_dir,
                source="manual",
                monitored_root=None,
            )

        except FileNotFoundError:
            logger.warning("SKILL.md file not found in '%s'", skill_dir)
            return None
        except (OSError, IOError) as e:
            logger.warning("Cannot read SKILL.md in '%s': %s", skill_dir, e)
            return None
        except (ValueError, TypeError) as e:
            logger.warning(
                "Invalid YAML frontmatter in SKILL.md at '%s': %s",
                skill_dir,
                e,
            )
            return None
        except Exception as e:
            logger.warning(
                "Failed to parse SKILL.md in '%s': %s",
                skill_dir,
                e,
            )
            return None

    def register_agent_skill(self, skill_dir: str) -> None:
        """Register an agent skill from a directory.

        Directory must contain SKILL.md with 'name' and 'description' fields.

        Args:
            skill_dir: Path to skill directory.

        Raises:
            ValueError: If directory invalid, SKILL.md missing/invalid,
                or name conflicts.
        """
        # Phase 1: I/O operations outside lock (no shared state access)
        skill_dir = os.path.abspath(os.path.normpath(skill_dir))

        # Validate directory
        if not self._safe_path_operation(
            lambda: os.path.isdir(skill_dir),
            skill_dir,
            "Cannot access skill directory",
        ):
            raise ValueError(
                f"The skill directory '{skill_dir}' does not exist or is "
                "not a directory.",
            )

        # Validate SKILL.md exists
        path_skill_md = os.path.join(skill_dir, "SKILL.md")
        if not self._safe_path_operation(
            lambda: os.path.isfile(path_skill_md),
            path_skill_md,
            "Cannot access SKILL.md",
        ):
            raise ValueError(
                f"The skill directory '{skill_dir}' must include a "
                "SKILL.md file at the top level.",
            )

        # Parse SKILL.md (I/O operation)
        skill = self._parse_skill_md(skill_dir)
        if skill is None:
            raise ValueError(
                f"The SKILL.md file in '{skill_dir}' must have a YAML Front "
                "Matter including 'name' and 'description' fields.",
            )

        name = skill["name"]
        skill["source"] = "manual"
        skill["monitored_root"] = None

        # Phase 2: Critical section - only modify shared state
        with self._lock:
            # Check for conflicts
            if name in self._skills:
                existing_dir = self._skills[name]["dir"]
                raise ValueError(
                    f"An agent skill with name '{name}' is already registered "
                    f"from directory '{existing_dir}'.",
                )

            # Add to skills
            self._skills[name] = skill

        logger.info(
            "Registered agent skill '%s' from directory '%s'.",
            name,
            skill_dir,
        )

    def _get_monitored_skills(
        self,
        monitored_root: str,
    ) -> list[tuple[str, str]]:
        """Get all skills from a monitored root directory.

        Returns list of (skill_name, skill_dir) tuples.
        """
        return [
            (name, skill["dir"])
            for name, skill in self._skills.items()
            if skill.get("source") == "monitored"
            and skill.get("monitored_root") == monitored_root
        ]

    def _remove_skill_internal(
        self,
        name: str,
        skill_dir: str | None = None,
    ) -> None:
        """Core skill removal logic without lock or logging."""
        if skill_dir is None:
            skill_dir = self._skills[name]["dir"]
        del self._skills[name]
        self._skill_dir_mtime_cache.pop(skill_dir, None)

    @_thread_safe
    def remove_agent_skill(self, name: str) -> None:
        """Remove an agent skill by name.

        Args:
            name: The name of the skill to remove.
        """
        if name not in self._skills:
            logger.warning("Skill '%s' not found. Nothing to remove.", name)
            return

        skill_dir = self._skills[name]["dir"]
        self._remove_skill_internal(name, skill_dir)
        logger.info("Removed skill '%s'", name)

    def _remove_skill_by_name(self, name: str) -> None:
        """Remove a skill by name without logging warnings."""
        if name in self._skills:
            self._remove_skill_internal(name)

    def _remove_skill_by_dir(self, skill_dir: str) -> None:
        """Remove a skill by directory path."""
        # Find skill name by directory
        skill_name = next(
            (
                name
                for name, skill in self._skills.items()
                if skill["dir"] == skill_dir
            ),
            None,
        )
        if skill_name:
            self._remove_skill_internal(skill_name, skill_dir)
            logger.info(
                "Removed skill '%s' from directory '%s'",
                skill_name,
                skill_dir,
            )

    def _remove_all_skills_from_dir(self, monitored_root: str) -> None:
        """Remove all skills from a monitored root directory."""
        skills_to_remove = self._get_monitored_skills(monitored_root)

        # Batch remove all skills
        for name, skill_dir in skills_to_remove:
            self._remove_skill_internal(name, skill_dir)
            logger.info(
                "Removed skill '%s' from inaccessible directory '%s'",
                name,
                monitored_root,
            )

    def get_agent_skill_prompt(self) -> str | None:
        """Get the prompt for all registered agent skills.

        Triggers lazy loading of monitored directories if needed.

        Returns:
            Combined prompt string, or None if no skills exist.
        """
        # Get snapshot of monitored directories
        with self._lock:
            monitored_dirs = list(self._monitored_dirs)

        # Update skills (I/O operations outside main lock)
        for monitored_dir in monitored_dirs:
            self._update_monitored_skills(monitored_dir)

        # Generate prompt with lock
        with self._lock:
            return self._generate_prompt()

    def _update_monitored_skills(self, monitored_dir: str) -> None:
        """Update skills from a monitored directory using two-phase approach.

        Fast path O(k): Check only known skills if root mtime unchanged.
        Slow path O(n): Full directory scan if root mtime changed.
        """
        # Phase 1: Check directory accessibility (I/O outside lock)
        try:
            current_root_mtime = os.stat(monitored_dir).st_mtime
        except (OSError, FileNotFoundError) as e:
            logger.warning(
                "Monitored directory '%s' is inaccessible: %s",
                monitored_dir,
                e,
            )
            with self._lock:
                self._remove_all_skills_from_dir(monitored_dir)
            return

        # Phase 2: Check if update needed (quick lock)
        with self._lock:
            cached_root_mtime = self._monitored_dir_last_scan_mtime.get(
                monitored_dir,
            )
            needs_full_scan = cached_root_mtime != current_root_mtime

        # Phase 3: Perform update based on strategy
        if needs_full_scan:
            self._full_rescan(monitored_dir)
            with self._lock:
                self._monitored_dir_last_scan_mtime[
                    monitored_dir
                ] = current_root_mtime
        else:
            self._check_existing_skills(monitored_dir)

    def _update_skill_cache_after_reparse(
        self,
        skill_dir: str,
        current_mtime: float,
    ) -> None:
        """Update cache after reparsing a skill."""
        with self._lock:
            skill_still_exists = any(
                s["dir"] == skill_dir for s in self._skills.values()
            )
            if skill_still_exists:
                self._skill_dir_mtime_cache[skill_dir] = current_mtime
            else:
                self._skill_dir_mtime_cache.pop(skill_dir, None)

    def _check_existing_skills(
        self,
        monitored_dir: str,
    ) -> None:
        """Check only known skills without filesystem scan. Fast path O(k)."""
        # Get snapshot of skills to check
        with self._lock:
            skills_to_check = self._get_monitored_skills(monitored_dir)
            # Get cached mtimes in same lock acquisition
            cached_mtimes = {
                skill_dir: self._skill_dir_mtime_cache.get(skill_dir)
                for _, skill_dir in skills_to_check
            }

        # Collect skills to remove and skills to reparse (I/O outside lock)
        skills_to_remove = []
        skills_to_reparse = []

        for name, skill_dir in skills_to_check:
            # Check if directory still exists
            try:
                current_mtime = os.stat(skill_dir).st_mtime
            except (OSError, FileNotFoundError) as e:
                skills_to_remove.append(
                    (name, skill_dir, f"inaccessible: {e}"),
                )
                continue

            # Check cache (already fetched)
            cached_mtime = cached_mtimes.get(skill_dir)

            if cached_mtime != current_mtime:
                skills_to_reparse.append(
                    (skill_dir, monitored_dir, name, current_mtime),
                )

        # Batch remove skills (single lock acquisition)
        if skills_to_remove:
            with self._lock:
                for name, skill_dir, reason in skills_to_remove:
                    self._remove_skill_by_name(name)
                    logger.info("Removed skill '%s' (%s)", name, reason)

        # Reparse modified skills
        for (
            reparse_skill_dir,
            reparse_monitored_dir,
            old_name,
            current_mtime,
        ) in skills_to_reparse:
            self._reparse_skill(
                reparse_skill_dir,
                reparse_monitored_dir,
                old_name=old_name,
            )
            self._update_skill_cache_after_reparse(
                reparse_skill_dir,
                current_mtime,
            )

    def _full_rescan(
        self,
        monitored_dir: str,
    ) -> None:
        """Full directory scan to detect new/deleted skills. Slow path O(n)."""
        # Phase 1: Scan filesystem (I/O outside lock)
        current_skill_dirs = self._scan_for_skill_dirs(monitored_dir)
        current_skill_dirs_set = set(current_skill_dirs)

        # Phase 2: Find deleted skills and get cached mtimes (single lock)
        with self._lock:
            existing_skill_dirs = {
                skill["dir"]
                for skill in self._skills.values()
                if skill.get("source") == "monitored"
                and skill.get("monitored_root") == monitored_dir
            }
            # Get all cached mtimes in same lock acquisition
            cached_mtimes = {
                skill_dir: self._skill_dir_mtime_cache.get(skill_dir)
                for skill_dir in current_skill_dirs
            }

        # Batch remove deleted skills (single lock acquisition)
        deleted_dirs = existing_skill_dirs - current_skill_dirs_set
        if deleted_dirs:
            with self._lock:
                for deleted_dir in deleted_dirs:
                    self._remove_skill_by_dir(deleted_dir)

        # Phase 3: Collect skills to process (I/O outside lock)
        new_skills = []
        modified_skills = []

        for skill_dir in current_skill_dirs:
            # Get mtime (I/O)
            try:
                current_mtime = os.stat(skill_dir).st_mtime
            except (OSError, FileNotFoundError):
                logger.warning(
                    "Skill directory '%s' is inaccessible, skipping",
                    skill_dir,
                )
                continue

            # Check cache (already fetched)
            cached_mtime = cached_mtimes.get(skill_dir)

            if cached_mtime is None:
                new_skills.append((skill_dir, monitored_dir, current_mtime))
            elif cached_mtime != current_mtime:
                modified_skills.append(
                    (skill_dir, monitored_dir, current_mtime),
                )

        # Process new skills
        for new_skill_dir, new_monitored_dir, current_mtime in new_skills:
            self._parse_and_register_skill(new_skill_dir, new_monitored_dir)
            with self._lock:
                self._skill_dir_mtime_cache[new_skill_dir] = current_mtime

        # Process modified skills
        for (
            mod_skill_dir,
            mod_monitored_dir,
            current_mtime,
        ) in modified_skills:
            self._reparse_skill(mod_skill_dir, mod_monitored_dir)
            self._update_skill_cache_after_reparse(
                mod_skill_dir,
                current_mtime,
            )

    def _scan_for_skill_dirs(self, root_dir: str) -> list[str]:
        """Recursively scan for skill directories (containing SKILL.md)."""
        skill_dirs = []

        try:
            for dirpath, dirnames, filenames in os.walk(
                root_dir,
                followlinks=False,
            ):
                if "SKILL.md" in filenames:
                    # Use abspath immediately to avoid closure issues
                    abs_dirpath = os.path.abspath(dirpath)
                    skill_dirs.append(abs_dirpath)
                    dirnames.clear()  # Don't recurse into skill directories
        except OSError as e:
            logger.error("Error scanning directory '%s': %s", root_dir, e)

        return skill_dirs

    def _parse_and_register_skill(
        self,
        skill_dir: str,
        monitored_root: str,
    ) -> None:
        """Parse and register a new skill from a directory."""
        # Parse skill (I/O outside lock)
        skill = self._parse_skill_md(skill_dir)
        if skill is None:
            return

        skill["source"] = "monitored"
        skill["monitored_root"] = monitored_root
        name = skill["name"]

        # Register skill (critical section)
        with self._lock:
            # Check for conflicts (monitored skills log warning and skip)
            if name in self._skills:
                existing_dir = self._skills[name]["dir"]
                logger.warning(
                    "Skill name conflict: '%s' from '%s' conflicts with "
                    "existing skill from '%s'. Keeping the existing one.",
                    name,
                    skill_dir,
                    existing_dir,
                )
                return

            self._skills[name] = skill

        logger.info("Discovered skill '%s' from '%s'", name, skill_dir)

    def _reparse_skill(
        self,
        skill_dir: str,
        monitored_root: str,
        old_name: str | None = None,
    ) -> None:
        """Re-parse a skill and handle name changes.

        Args:
            skill_dir: Path to the skill directory.
            monitored_root: Root directory being monitored.
            old_name: Previous name of the skill (if known).
        """
        # Check if SKILL.md exists (I/O)
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        if not self._safe_path_operation(
            lambda: os.path.exists(skill_md_path),
            skill_md_path,
            "Error checking SKILL.md",
        ):
            with self._lock:
                self._remove_skill_by_dir(skill_dir)
            logger.info(
                "Removed skill from '%s' (SKILL.md deleted)",
                skill_dir,
            )
            return

        # Parse new skill data (I/O)
        new_skill = self._parse_skill_md(skill_dir)
        if new_skill is None:
            with self._lock:
                self._remove_skill_by_dir(skill_dir)
            return

        new_skill["source"] = "monitored"
        new_skill["monitored_root"] = monitored_root
        new_name = new_skill["name"]

        # Update skill registry (critical section)
        with self._lock:
            # Find old entry by directory path
            old_entry_name = None
            if (
                old_name
                and old_name in self._skills
                and self._skills[old_name]["dir"] == skill_dir
            ):
                old_entry_name = old_name
            else:
                old_entry_name = next(
                    (
                        name
                        for name, skill in self._skills.items()
                        if skill["dir"] == skill_dir
                    ),
                    None,
                )

            # Remove old entry if name changed
            if old_entry_name and old_entry_name != new_name:
                del self._skills[old_entry_name]
                logger.info(
                    "Skill name changed: '%s' -> '%s'",
                    old_entry_name,
                    new_name,
                )

            # Add/update entry (with conflict check)
            if new_name not in self._skills:
                self._skills[new_name] = new_skill
                logger.info(
                    "Updated skill '%s' from '%s'",
                    new_name,
                    skill_dir,
                )
            else:
                existing_dir = self._skills[new_name]["dir"]
                if existing_dir == skill_dir:
                    self._skills[new_name] = new_skill
                    logger.info(
                        "Updated skill '%s' from '%s'",
                        new_name,
                        skill_dir,
                    )
                else:
                    logger.warning(
                        "Skill name conflict: '%s' from '%s' conflicts with "
                        "existing skill from '%s'. Keeping the existing one.",
                        new_name,
                        skill_dir,
                        existing_dir,
                    )

    @_thread_safe
    def refresh_monitored_skills(
        self,
        force: bool = False,
    ) -> dict[str, int]:
        """Manually trigger skill scanning and refresh.

        Useful when mtime-based caching might be unreliable.

        Args:
            force: If True, clear all caches and force full rescan.

        Returns:
            Statistics dict: {"added": int, "modified": int, "removed": int}
        """
        # Track statistics - capture before state
        skills_before = set(self._skills.keys())
        skill_dirs_before = {
            skill["dir"]
            for skill in self._skills.values()
            if skill.get("source") == "monitored"
        }

        if force:
            self._skill_dir_mtime_cache.clear()
            self._monitored_dir_last_scan_mtime.clear()
            logger.info("Forcing full rescan of all monitored directories")

        # Trigger update for all monitored directories
        for monitored_dir in list(self._monitored_dirs):
            self._update_monitored_skills(monitored_dir)

        # Calculate statistics - capture after state
        skills_after = set(self._skills.keys())
        skill_dirs_after = {
            skill["dir"]
            for skill in self._skills.values()
            if skill.get("source") == "monitored"
        }

        added = len(skills_after - skills_before)
        removed = len(skills_before - skills_after)
        # Modified = directories that existed before and after
        modified = len(skill_dirs_before & skill_dirs_after) - (
            len(skills_before & skills_after)
        )
        modified = max(0, modified)

        stats = {"added": added, "modified": modified, "removed": removed}
        logger.info(
            "Refresh complete: %d added, %d modified, %d removed",
            added,
            modified,
            removed,
        )

        return stats

    def _generate_prompt(self) -> str | None:
        """Generate the prompt for all registered agent skills.

        Returns None if no skills exist.
        """
        if not self._skills:
            return None

        skill_prompts = [
            self._agent_skill_template.format(
                name=skill["name"],
                description=skill["description"],
                dir=skill["dir"],
            )
            for skill in self._skills.values()
        ]

        return "\n".join([self._agent_skill_instruction] + skill_prompts)
