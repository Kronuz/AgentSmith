"""Conservative inventory of portable, non-session agent environment files."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvironmentFile:
    source: Path
    bundle_path: Path
    scope: str
    harness: str
    project_root: str | None = None


_PROJECT_PATHS: dict[str, tuple[str, ...]] = {
    "shared": ("AGENTS.md",),
    "claude": (
        "CLAUDE.md",
        ".mcp.json",
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


def _collect(
    scope: str,
    root: Path,
    mapping: dict[str, tuple[str, ...]],
    prefix: Path,
    project_root: str | None,
) -> list[EnvironmentFile]:
    collected: list[EnvironmentFile] = []
    seen: set[tuple[Path, Path]] = set()
    for harness, relatives in mapping.items():
        for relative_text in relatives:
            base = root / relative_text
            for source in _files(base):
                relative = (
                    Path(relative_text)
                    if base.is_file()
                    else Path(relative_text) / source.relative_to(base)
                )
                bundle_path = prefix / harness / relative
                key = (source.resolve(), bundle_path)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(
                    EnvironmentFile(source, bundle_path, scope, harness, project_root)
                )
    return collected


def collect_project_environment(project_root: Path) -> list[EnvironmentFile]:
    """Collect context whose destination is one specific project."""
    root = project_root.expanduser().resolve()
    key = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    prefix = Path("environment") / "projects" / key
    collected = _collect(
        "project",
        root,
        _PROJECT_PATHS,
        prefix,
        str(root),
    )
    known = {item.source.resolve() for item in collected}
    for directory, names, files in os.walk(root):
        names[:] = [
            name
            for name in names
            if name not in _EXCLUDED_PARTS and not (Path(directory) / name).is_symlink()
        ]
        base = Path(directory)
        for filename, harness in (("AGENTS.md", "shared"), ("CLAUDE.md", "claude")):
            source = base / filename
            if filename not in files or source.resolve() in known:
                continue
            relative = source.relative_to(root)
            collected.append(
                EnvironmentFile(
                    source,
                    prefix / harness / relative,
                    "project",
                    harness,
                    str(root),
                )
            )
            known.add(source.resolve())
    return collected


def collect_global_environment(
    harnesses: set[str] | None = None,
) -> list[EnvironmentFile]:
    """Collect user-wide agent configuration, never session/project state."""
    home = Path(os.environ.get("ASMITH_ENV_HOME", Path.home())).expanduser().resolve()
    mapping = _USER_PATHS
    if harnesses is not None:
        mapping = {
            harness: paths
            for harness, paths in _USER_PATHS.items()
            if harness == "shared" or harness in harnesses
        }
    return _collect(
        "global",
        home,
        mapping,
        Path("environment") / "global",
        None,
    )
