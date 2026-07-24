"""Reversible filesystem receipts for agent-managed configuration changes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "agentsmith-change-receipt"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path, root: Path) -> dict[str, object]:
    relative = "." if path == root else str(path.relative_to(root))
    stat = path.lstat()
    base: dict[str, object] = {
        "path": relative,
        "mode": stat.st_mode & 0o7777,
    }
    if path.is_symlink():
        base.update({"kind": "symlink", "target": os.readlink(path)})
    elif path.is_file():
        base.update({"kind": "file", "bytes": stat.st_size, "sha256": _sha256(path)})
    elif path.is_dir():
        base["kind"] = "directory"
    else:
        raise ValueError(f"unsupported filesystem object: {path}")
    return base


def fingerprint(path: Path) -> dict[str, object]:
    """Return a stable recursive description of a path without following symlinks."""
    if not _lexists(path):
        return {"exists": False}
    entries = [_entry(path, path)]
    if path.is_dir() and not path.is_symlink():
        entries.extend(_entry(child, path) for child in sorted(path.rglob("*")))
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "exists": True,
        "kind": entries[0]["kind"],
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "entries": len(entries),
    }


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise ValueError(f"unsupported filesystem object: {source}")


def _remove(path: Path) -> None:
    if not _lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _absolute(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    return absolute.parent.resolve() / absolute.name


def _validate_target(path: Path) -> None:
    home = Path.home().resolve()
    normalized = _absolute(path)
    if normalized == Path(normalized.anchor):
        raise ValueError(f"refusing broad snapshot target: {path}")
    if normalized == home or home.is_relative_to(normalized):
        raise ValueError(
            f"refusing broad snapshot target: {path}; select exact files/directories"
        )


def _load(receipt: Path) -> tuple[Path, dict[str, Any]]:
    root = _absolute(receipt)
    try:
        manifest = json.loads((root / "receipt.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read receipt: {exc}") from exc
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("schema_version") != SCHEMA_VERSION
    ):
        raise ValueError("unsupported or missing receipt schema")
    if not isinstance(manifest.get("targets"), list):
        raise TypeError("receipt has no target inventory")
    return root, manifest


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    temporary = root / ".receipt.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(root / "receipt.json")


def create(targets: list[Path], destination: Path) -> Path:
    """Create an immutable pre-change snapshot in a new receipt directory."""
    root = _absolute(destination)
    if root.exists():
        raise FileExistsError(f"receipt already exists: {root}")
    normalized = [_absolute(target) for target in targets]
    if not normalized:
        raise ValueError("at least one snapshot target is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate snapshot target")
    for target in normalized:
        _validate_target(target)
        if root == target or root.is_relative_to(target):
            raise ValueError(f"receipt must be outside snapshot target: {target}")
    for index, left in enumerate(normalized):
        for right in normalized[index + 1 :]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError(f"snapshot targets overlap: {left} and {right}")

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    records: list[dict[str, object]] = []
    try:
        for index, target in enumerate(normalized, 1):
            before = fingerprint(target)
            backup = Path("before") / f"{index:03d}"
            if bool(before["exists"]):
                _copy(target, staging / backup)
            records.append(
                {
                    "path": str(target),
                    "backup": str(backup) if bool(before["exists"]) else None,
                    "before": before,
                }
            )
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "sealed_at": None,
            "rolled_back_at": None,
            "targets": records,
        }
        _write_manifest(staging, manifest)
        staging.replace(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return root


def _action(before: dict[str, object], after: dict[str, object]) -> str:
    if before == after:
        return "unchanged"
    if not bool(before.get("exists")):
        return "created"
    if not bool(after.get("exists")):
        return "deleted"
    return "modified"


def audit(receipt: Path, seal: bool = False) -> list[dict[str, object]]:
    """Compare live targets with the baseline or seal their post-change state."""
    root, manifest = _load(receipt)
    if seal and manifest.get("sealed_at"):
        raise ValueError("receipt is already sealed")
    rows: list[dict[str, object]] = []
    for record in manifest["targets"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise TypeError("invalid receipt target")
        current = fingerprint(Path(record["path"]))
        before = record.get("before")
        if not isinstance(before, dict):
            raise TypeError("invalid receipt baseline")
        expected = record.get("after") if manifest.get("sealed_at") else before
        action = _action(before, current)
        rows.append(
            {
                "path": record["path"],
                "action": action,
                "matches": current == expected,
            }
        )
        if seal:
            record["after"] = current
            record["action"] = action
    if seal:
        manifest["sealed_at"] = _now()
        _write_manifest(root, manifest)
    return rows


def rollback(receipt: Path, apply: bool) -> list[dict[str, object]]:
    """Restore every target to its snapshotted baseline."""
    root, manifest = _load(receipt)
    rows: list[dict[str, object]] = []
    records = manifest["targets"]
    if apply:
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise TypeError("invalid receipt target")
            before = record.get("before")
            if not isinstance(before, dict):
                raise TypeError("invalid receipt baseline")
            if bool(before.get("exists")):
                backup_value = record.get("backup")
                if not isinstance(backup_value, str):
                    raise ValueError(f"missing baseline backup for {record['path']}")
                if fingerprint(root / backup_value) != before:
                    raise ValueError(
                        f"baseline backup failed verification: {record['path']}"
                    )
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise TypeError("invalid receipt target")
        target = Path(record["path"])
        _validate_target(target)
        before = record.get("before")
        if not isinstance(before, dict):
            raise TypeError("invalid receipt baseline")
        current = fingerprint(target)
        action = _action(before, current)
        rows.append({"path": str(target), "action": action})
        if not apply or action == "unchanged":
            continue
        _remove(target)
        if bool(before.get("exists")):
            backup_value = record.get("backup")
            assert isinstance(backup_value, str)
            backup = root / backup_value
            _copy(backup, target)
    if apply:
        manifest["rolled_back_at"] = _now()
        _write_manifest(root, manifest)
    return rows
