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

from . import __version__
from .config import CONFIG_DIR, MANIFEST_FILE, Config, load_config

MANIFEST_VERSION = 1
_INPUT_HASH_LIMIT = 8 << 20


class ManifestError(RuntimeError):
    pass


def git_head(root: Path) -> Optional[str]:
    if not (Path(root) / ".git").exists():
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


def inputs_hash(root: Path) -> str:
    """Hash of the top-level files (lockfiles, build scripts, manifests...)."""
    h = hashlib.sha256()
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        if not entry.is_file(follow_symlinks=False):
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


def environment(root: Path, cfg: Config) -> dict:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "git_head": git_head(root),
        "inputs": inputs_hash(root),
        "config": cfg.digest(),
    }


def build(root: Path, cfg: Config, *, entries, baseline, total_bytes, warnings) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "tool": f"builddiet {__version__}",
        "project": str(Path(root).resolve()),
        "name": Path(root).resolve().name,
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": environment(root, cfg),
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


def find(path: Path, max_depth: int = 4) -> list:
    """Project roots at or below ``path`` that contain a manifest."""
    path = Path(path).resolve()
    found = []

    def walk(current: Path, depth: int) -> None:
        if manifest_path(current).is_file():
            found.append(current)
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(
                e.path for e in os.scandir(current)
                if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                and e.name not in ("node_modules", "target", "__pycache__")
            )
        except OSError:
            return
        for child in children:
            walk(Path(child), depth + 1)

    walk(path, 0)
    return found


def staleness(manifest: dict) -> list:
    """Reasons why the proofs in ``manifest`` may no longer hold."""
    root = Path(manifest["project"])
    if not root.is_dir():
        return [f"project directory {root} no longer exists"]
    recorded = manifest.get("environment", {})
    reasons = []
    if recorded.get("platform") != platform.platform():
        reasons.append("platform/toolchain host changed")
    head = git_head(root)
    if recorded.get("git_head") != head:
        reasons.append("git HEAD changed since analysis")
    if recorded.get("inputs") != inputs_hash(root):
        reasons.append("top-level project files changed since analysis")
    try:
        cfg = load_config(root)
    except Exception:
        cfg = None
    if cfg is not None and recorded.get("config") != cfg.digest():
        reasons.append("BuildDiet config changed since analysis")
    return reasons
