"""Fingerprints and identity checks: did the workflow really recreate the bytes?"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field

from . import fs
from pathlib import Path

IDENTICAL = "identical"  # every file recreated with the same content
RECREATED = "recreated"  # every file recreated, some with different bytes
PARTIAL = "partial"  # some files never came back
ABSENT = "absent"  # nothing came back
STALE_COPY = "stale"  # recreated deterministically, but differs from the current copy

_CHUNK = 1 << 20


@dataclass
class Fingerprint:
    entries: dict = field(default_factory=dict)  # rel path -> (size, digest)
    total_bytes: int = 0
    mode: str = "full"

    def __len__(self) -> int:
        return len(self.entries)


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: Path, mode: str = "full") -> Fingerprint:
    """Record every file below ``path``. Symlinks are recorded, not followed."""
    fp = Fingerprint(mode=mode)
    base = str(path)
    if not os.path.lexists(base):
        return fp

    def record(full: str, rel: str) -> None:
        st = os.lstat(full)
        if fs.is_link(full):
            try:
                fp.entries[rel] = (0, "link:" + os.readlink(full))
            except OSError:
                fp.entries[rel] = (0, "link:?")
        elif stat.S_ISREG(st.st_mode):
            digest = _hash_file(full) if mode == "full" else None
            fp.entries[rel] = (st.st_size, digest)
            fp.total_bytes += st.st_size

    if not os.path.isdir(base) or fs.is_link(base):
        record(base, ".")
        return fp
    for dirpath, dirnames, filenames in fs.walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            try:
                record(full, rel)
            except OSError:
                fp.entries[rel] = (-1, "unreadable")
    return fp


def signature(fp: Fingerprint) -> str:
    """One SHA-256 over every (path, size, digest) of a fingerprint."""
    h = hashlib.sha256()
    for rel in sorted(fp.entries):
        size, digest = fp.entries[rel]
        h.update(f"{rel}\0{size}\0{digest}\n".encode())
    return h.hexdigest()


def example(paths) -> str:
    """' (e.g. x)' for a directory; nothing for a single file (its key is '.')."""
    return "" if not paths or paths[0] == "." else f" (e.g. {paths[0]})"


def compare(before: Fingerprint, after: Fingerprint) -> tuple:
    """Return (identity, human readable detail)."""
    if not after.entries:
        return ABSENT, "nothing was recreated"
    missing = [k for k in before.entries if k not in after.entries]
    if missing:
        return PARTIAL, (
            f"{len(missing)} of {len(before)} files were not recreated{example(missing)}"
        )
    changed = [k for k, v in before.entries.items() if after.entries[k] != v]
    if changed:
        return RECREATED, (
            f"all {len(before)} files recreated, {len(changed)} with different "
            f"content{example(changed)}"
        )
    how = "byte-for-byte" if before.mode == "full" else "same paths and sizes"
    return IDENTICAL, f"all {len(before)} files recreated {how}"


def split_differences(original: Fingerprint, first: Fingerprint, second: Fingerprint) -> tuple:
    """Classify files whose first regeneration differs from the original.

    Returns (stale, nondeterministic): files the workflow reproduces the same
    way twice (so the original copy is what differs), and files whose
    regenerated content varies from run to run.
    """
    differing = [k for k, v in original.entries.items() if first.entries.get(k) != v]
    stale = [k for k in differing if second.entries.get(k) == first.entries.get(k)]
    nondeterministic = [k for k in differing if k not in stale]
    return stale, nondeterministic


def snapshot(root: Path, skip_top=(), guard=None) -> dict:
    """Cheap metadata snapshot (size, mtime) used to detect writes to the original.
    Protected / excluded areas (``guard``) are not listed."""
    out = {}
    base = str(root)
    for dirpath, dirnames, filenames in fs.walk(base):
        if dirpath == base:
            dirnames[:] = [d for d in dirnames if d not in skip_top]
        if guard is not None:
            dirnames[:] = [d for d in dirnames if guard.status(os.path.join(dirpath, d)) == "clear"]
            filenames = [f for f in filenames if guard.status(os.path.join(dirpath, f)) == "clear"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            out[os.path.relpath(full, base)] = (st.st_size, st.st_mtime_ns)
    return out


def snapshot_diff(before: dict, after: dict) -> list:
    changed = [k for k in before if after.get(k) != before[k]]
    added = [k for k in after if k not in before]
    return sorted(changed + added)
