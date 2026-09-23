"""Fingerprints and identity checks: did the workflow really recreate the bytes?"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

IDENTICAL = "identical"  # every file recreated with the same content
RECREATED = "recreated"  # every file recreated, some with different bytes
PARTIAL = "partial"  # some files never came back
ABSENT = "absent"  # nothing came back

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
        if stat.S_ISLNK(st.st_mode):
            fp.entries[rel] = (0, "link:" + os.readlink(full))
        elif stat.S_ISREG(st.st_mode):
            digest = _hash_file(full) if mode == "full" else None
            fp.entries[rel] = (st.st_size, digest)
            fp.total_bytes += st.st_size

    if not os.path.isdir(base) or os.path.islink(base):
        record(base, ".")
        return fp
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            try:
                record(full, rel)
            except OSError:
                fp.entries[rel] = (-1, "unreadable")
    return fp


def compare(before: Fingerprint, after: Fingerprint) -> tuple:
    """Return (identity, human readable detail)."""
    if not after.entries:
        return ABSENT, "nothing was recreated"
    missing = [k for k in before.entries if k not in after.entries]
    if missing:
        return PARTIAL, (
            f"{len(missing)} of {len(before)} files were not recreated "
            f"(e.g. {missing[0]})"
        )
    changed = [k for k, v in before.entries.items() if after.entries[k] != v]
    if changed:
        return RECREATED, (
            f"all {len(before)} files recreated, {len(changed)} with different "
            f"content (e.g. {changed[0]})"
        )
    how = "byte-for-byte" if before.mode == "full" else "same paths and sizes"
    return IDENTICAL, f"all {len(before)} files recreated {how}"


def snapshot(root: Path, skip_top=()) -> dict:
    """Cheap metadata snapshot (size, mtime) used to detect writes to the original."""
    out = {}
    base = str(root)
    for dirpath, dirnames, filenames in os.walk(base):
        if dirpath == base:
            dirnames[:] = [d for d in dirnames if d not in skip_top]
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
