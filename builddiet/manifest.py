"""What BuildDiet learned, stored in ``<project>/.builddiet/manifest.json``.

A proof is only valid for the environment it was made in, so the manifest
records an environment fingerprint and :func:`staleness` explains why an old
proof may no longer hold.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

from . import __version__, fs
from .config import CONFIG_DIR, MANIFEST_FILE, Config, load_config

MANIFEST_VERSION = 1
_INPUT_HASH_LIMIT = 8 << 20


class ManifestError(RuntimeError):
    pass


def git_head(root: Path, guard=None) -> Optional[str]:
    git_dir = Path(root) / ".git"
    if (guard is not None and not guard.allows_touch(git_dir)) or not git_dir.exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode().strip() or None


def inputs_hash(root: Path, guard=None) -> str:
    """Hash of the top-level files (lockfiles, build scripts, manifests...).
    Protected files are represented by their name only, never read."""
    from .protect import Guard

    guard = guard if guard is not None else Guard()
    h = hashlib.sha256()
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if guard.status(entry.path) != "clear":
            h.update(f"{entry.name}\0guarded\0".encode())
            continue
        if not entry.is_file(follow_symlinks=False) or fs.is_link(entry):
            continue
        size = entry.stat(follow_symlinks=False).st_size
        h.update(f"{entry.name}\0{size}\0".encode())
        if size <= _INPUT_HASH_LIMIT:
            try:
                with open(entry.path, "rb") as fh:
                    h.update(hashlib.sha256(fh.read()).digest())
            except OSError:
                pass
    return h.hexdigest()[:16]


def environment(root: Path, cfg: Config, guard=None) -> dict:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "git_head": git_head(root, guard),
        "inputs": inputs_hash(root, guard),
        "config": cfg.digest(),
    }


def build(root: Path, cfg: Config, *, entries, baseline, total_bytes, warnings, guard=None) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "tool": f"builddiet {__version__}",
        "project": str(Path(root).resolve()),
        "name": Path(root).resolve().name,
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": environment(root, cfg, guard),
        "commands": {"regenerate": cfg.regenerate, "verify": cfg.verify},
        "hash_mode": cfg.hash_mode,
        "reuse": dict(cfg.reuse),
        "baseline": baseline,
        "total_bytes": total_bytes,
        "entries": entries,
        "warnings": warnings,
    }


def manifest_path(root: Path) -> Path:
    return Path(root) / CONFIG_DIR / MANIFEST_FILE


def save(root: Path, manifest: dict) -> Path:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(root: Path) -> dict:
    path = manifest_path(root)
    if not path.is_file():
        raise ManifestError(f"no analysis found for {root} (run `builddiet analyze`)")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != MANIFEST_VERSION:
        raise ManifestError(f"{path}: unsupported manifest version {data.get('version')}")
    return data


def find(path: Path, max_depth: int = 4, guard=None) -> list:
    """Project roots at or below ``path`` that contain a manifest. Protected /
    excluded folders are neither listed nor entered, and links are not followed."""
    path = Path(path).resolve()
    found = []

    def walk(current: Path, depth: int) -> None:
        if guard is not None and guard.status(current) != "clear":
            return
        if manifest_path(current).is_file():
            found.append(current)
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(
                e.path for e in os.scandir(current)
                if (guard is None or guard.status(e.path) == "clear")
                and not fs.is_link(e) and e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                and e.name not in ("node_modules", "target", "__pycache__")
            )
        except OSError:
            return
        for child in children:
            walk(Path(child), depth + 1)

    walk(path, 0)
    return found


def staleness(manifest: dict, guard=None) -> list:
    """Reasons why the proofs in ``manifest`` may no longer hold."""
    root = Path(manifest["project"])
    if not root.is_dir():
        return [f"project directory {root} no longer exists"]
    recorded = manifest.get("environment", {})
    reasons = []
    if recorded.get("platform") != platform.platform():
        reasons.append("platform/toolchain host changed")
    head = git_head(root, guard)
    if recorded.get("git_head") != head:
        reasons.append("git HEAD changed since analysis")
    if recorded.get("inputs") != inputs_hash(root, guard):
        reasons.append("top-level project files changed since analysis")
    try:
        cfg = load_config(root)
    except Exception:
        cfg = None
    if cfg is not None and recorded.get("config") != cfg.digest():
        reasons.append("BuildDiet config changed since analysis")
    return reasons
