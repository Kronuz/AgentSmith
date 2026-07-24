"""Conservative inventory of portable, non-session agent environment files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvironmentFile:
    source: Path
    bundle_path: Path
    scope: str
    harness: str


_PROJECT_PATHS: dict[str, tuple[str, ...]] = {
    "shared": ("AGENTS.md",),
    "claude": (
        "CLAUDE.md",
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claude/commands",
        ".claude/agents",
        ".claude/hooks",
        ".claude/skills",
    ),
    "codex": (
        ".codex/config.toml",
        ".codex/rules",
        ".codex/skills",
    ),
    "copilot": (
        ".github/copilot-instructions.md",
        ".github/instructions",
        ".copilot/config.json",
        ".copilot/instructions",
    ),
}

_USER_PATHS: dict[str, tuple[str, ...]] = {
    "shared": ("AGENTS.md",),
    "claude": (
        ".claude/CLAUDE.md",
        ".claude/settings.json",
        ".claude/commands",
        ".claude/agents",
        ".claude/hooks",
        ".claude/skills",
    ),
    "codex": (
        ".codex/AGENTS.md",
        ".codex/config.toml",
        ".codex/rules",
        ".codex/skills",
    ),
    "copilot": (
        ".copilot/config.json",
        ".copilot/mcp-config.json",
        ".copilot/copilot-instructions.md",
        ".copilot/instructions",
    ),
}

_EXCLUDED_NAMES = {
    "auth.json",
    "credentials.json",
    "credentials",
    ".credentials",
    ".env",
    "history.jsonl",
}
_EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    "cache",
    "logs",
    "projects",
    "sessions",
    "session-state",
    ".system",
}


def _files(path: Path) -> list[Path]:
    if path.is_symlink():
        return []
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and not candidate.is_symlink()
        and candidate.name not in _EXCLUDED_NAMES
        and not _EXCLUDED_PARTS.intersection(candidate.relative_to(path).parts)
    )


def collect_environment(project_root: Path) -> list[EnvironmentFile]:
    """Collect allowlisted project and user environment files.

    Authentication/session stores are intentionally outside the allowlist. Settings
    may still contain inline secrets, so callers must require explicit user opt-in.
    """
    home = Path.home()
    override_home = os.environ.get("ASMITH_ENV_HOME")
    if override_home:
        home = Path(override_home).expanduser()
    collected: list[EnvironmentFile] = []
    seen: set[tuple[Path, Path]] = set()
    for scope, root, mapping in (
        ("project", project_root.expanduser().resolve(), _PROJECT_PATHS),
        ("user", home.resolve(), _USER_PATHS),
    ):
        for harness, relatives in mapping.items():
            for relative_text in relatives:
                base = root / relative_text
                for source in _files(base):
                    relative = (
                        Path(relative_text)
                        if base.is_file()
                        else Path(relative_text) / source.relative_to(base)
                    )
                    bundle_path = Path("environment") / scope / harness / relative
                    key = (source.resolve(), bundle_path)
                    if key in seen:
                        continue
                    seen.add(key)
                    collected.append(
                        EnvironmentFile(source, bundle_path, scope, harness)
                    )
    return collected
