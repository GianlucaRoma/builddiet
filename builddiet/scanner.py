"""Partition a project tree into regions and pick experiment candidates.

The regions returned by :func:`scan` never overlap and together cover the
whole project, so bucket totals always add up to the workspace size.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import adapters as adapters_mod
from . import fs
from .config import CONFIG_DIR, Config

METADATA_DIRS = (".git", ".hg", ".svn", CONFIG_DIR)

CANDIDATE = "candidate"
EXCLUDED = "excluded"
SMALL = "small"
METADATA = "metadata"
LOOSE = "loose"
NOT_SELECTED = "not-selected"

PROTECTED = "protected"
USER_EXCLUDED = "user-excluded"
LINK = "link"

STATUS_DETAIL = {
    PROTECTED: "protected (builddiet protect): never read, never touched",
    USER_EXCLUDED: "excluded with --exclude: not read, not touched",
    LINK: "symlink / junction: never followed, never touched",
    CANDIDATE: "not tested",
    EXCLUDED: "excluded by config",
    SMALL: "below min_size, not tested",
    METADATA: "version control / BuildDiet metadata",
    LOOSE: "loose files, not tested",
    NOT_SELECTED: "not selected as a candidate",
}


@dataclass
class Region:
    path: str
    kind: str  # "dir", "file", or "files" (a group of loose files)
    bytes: int
    files: int
    status: str
    known: Optional[str] = None

    @property
    def display(self) -> str:
        return self.path + "/" if self.kind in ("dir", "guarded") else self.path


def tree_size(path: Path) -> tuple:
    """Total bytes and file count below ``path`` without following links."""
    total = files = 0
    for dirpath, _dirs, names in fs.walk(path):
        for name in names:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
                files += 1
            except OSError:
                pass
    return total, files


def _ancestors(rel: str) -> list:
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def scan(root: Path, cfg: Config, adapters=None, guard=None) -> list:
    """Partition ``root``. Protected / excluded areas (``guard``) are listed with
    their path only: they are never read, sized or offered as candidates, and a
    folder that contains one is split so that only its unprotected parts count."""
    root = Path(root).resolve()
    if adapters is None:
        adapters = adapters_mod.detect(root)
    includes = set(cfg.include)
    split_targets = {a for inc in includes for a in _ancestors(inc)}
    regions: list = []

    def excluded(rel: str) -> bool:
        return any(
            fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel + "/", pat)
            for pat in cfg.exclude
        )

    def add_dir(rel: str, status: str) -> None:
        size, files = tree_size(root / rel)
        if status == CANDIDATE and size < cfg.min_size:
            status = SMALL
        known = adapters_mod.known_reason(rel, adapters)
        regions.append(Region(rel, "dir", size, files, status, known))

    def visit(reldir: str, level: int) -> None:
        loose_bytes = loose_files = 0
        try:
            with os.scandir(root / reldir if reldir else root) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            rel = f"{reldir}/{entry.name}" if reldir else entry.name
            if guard is not None:
                state = guard.status(entry.path)
                if state != "clear":  # checked before anything about the entry is read
                    regions.append(Region(rel, "guarded", 0, 0, PROTECTED if state != "excluded" else USER_EXCLUDED))
                    continue
            if fs.is_link(entry):  # symlink / junction: never followed, never a candidate
                regions.append(Region(rel, "link", 0, 0, LINK))
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                size = 0 if is_dir else entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if not is_dir:
                # Files are candidates when named in `include`, or in auto mode
                # when they are at least min_size. The rest is grouped.
                if excluded(rel):
                    regions.append(Region(rel, "file", size, 1, EXCLUDED))
                elif rel in includes or (cfg.auto and size >= cfg.min_size):
                    status = CANDIDATE if size >= cfg.min_size else SMALL
                    regions.append(Region(rel, "file", size, 1, status))
                else:
                    loose_bytes += size
                    loose_files += 1
                continue
            if guard is not None and guard.contains_guarded(entry.path):
                visit(rel, level + 1)  # keep the protected part out, look at its siblings
            elif not reldir and entry.name in METADATA_DIRS:
                add_dir(rel, METADATA)
            elif excluded(rel):
                add_dir(rel, EXCLUDED)
            elif rel in includes:
                add_dir(rel, CANDIDATE)
            elif rel in split_targets or (cfg.auto and level < cfg.depth):
                visit(rel, level + 1)
            elif cfg.auto:
                add_dir(rel, CANDIDATE)
            else:
                add_dir(rel, NOT_SELECTED)
        if loose_files:
            path = f"{reldir}/*" if reldir else "*"
            regions.append(Region(path, "files", loose_bytes, loose_files, LOOSE))

    visit("", 1)
    return regions
