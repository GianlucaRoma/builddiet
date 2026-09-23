"""Protected paths: areas BuildDiet never reads, copies, analyzes or deletes.

* Persistent and global: stored in ``~/.builddiet/protected.json``
  (``$BUILDDIET_HOME`` overrides the folder).
* Absolute precedence: nothing (config `include`, `--include-git`, `--auto`,
  a manifest written before the protection) can override a protection.
  `--exclude` only adds temporary exclusions on top.
* Canonical comparison: every path is resolved with ``realpath`` (symlinks,
  junctions, ``..``, 8.3 short names) and ``normcase`` (case on Windows), so a
  protection cannot be bypassed by spelling a path differently or by reaching
  it through a link.
* Fail closed: if a path cannot be resolved, or the store cannot be read,
  BuildDiet treats it as protected / refuses to run.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Iterable, Optional

from . import fs

HOME_ENV = "BUILDDIET_HOME"
STORE_FILE = "protected.json"

CLEAR = "clear"
PROTECTED = "protected"
EXCLUDED = "excluded"
UNKNOWN = "unknown"  # could not be resolved: treated as protected


class ProtectionError(RuntimeError):
    pass


def home_dir() -> Path:
    env = os.environ.get(HOME_ENV)
    return Path(env) if env else Path.home() / ".builddiet"


def store_path() -> Path:
    return home_dir() / STORE_FILE


def canonical(path) -> str:
    """Resolved, case-normalised absolute path. Raises OSError/ValueError if it cannot be resolved."""
    raw = os.fspath(path)
    if not raw or "\0" in raw:
        raise ValueError(f"invalid path {path!r}")
    resolved = os.path.normcase(os.path.realpath(os.path.abspath(raw)))
    if os.name == "nt" and resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    stripped = resolved.rstrip("\\/")
    return stripped if stripped and not stripped.endswith(":") else resolved


def _inside(child: str, parent: str) -> bool:
    if child == parent:
        return True
    sep = "\\" if os.name == "nt" else "/"
    prefix = parent if parent.endswith(sep) else parent + sep
    return child.startswith(prefix)


def load() -> list:
    path = store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(d, dict) and "canonical" in d for d in data):
            raise ValueError("unexpected format")
        return data
    except (OSError, ValueError) as exc:
        raise ProtectionError(f"cannot read the protection list {path}: {exc}. Refusing to run "
                              "(fail closed); fix or remove that file.") from exc


def _save(records: list) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def protect(path) -> dict:
    c = canonical(path)
    records = load()
    for r in records:
        if r["canonical"] == c:
            return r
    record = {"path": os.path.abspath(os.fspath(path)), "canonical": c,
              "exists": os.path.lexists(os.fspath(path)),
              "added": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    records.append(record)
    _save(records)
    return record


def unprotect(path) -> list:
    records = load()
    try:
        c = canonical(path)
    except (OSError, ValueError):
        c = None
    given = os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")
    keep, removed = [], []
    for r in records:
        match = r["canonical"] == c or os.path.normcase(r["path"]).rstrip("\\/") == given
        (removed if match else keep).append(r)
    if removed:
        _save(keep)
    return removed


class Guard:
    """Answers "may BuildDiet touch this path?" for one command run.

    The protection list is read once, when the guard is created; `reclaim`
    creates a fresh guard right before deleting.
    """

    def __init__(self, excludes: Iterable = ()):
        self.records = load()
        self.protected = []
        for r in self.records:
            self.protected.append(r["canonical"])
            try:  # the protected path itself may be (or have become) a link: cover its target too
                now = canonical(r["path"])
                if now != r["canonical"]:
                    self.protected.append(now)
            except (OSError, ValueError):
                pass
        self.excluded = []
        for e in excludes or ():
            try:
                self.excluded.append(canonical(e))
            except (OSError, ValueError) as exc:
                raise ProtectionError(f"cannot resolve --exclude {e!r}: {exc}") from exc

    def status(self, path) -> str:
        try:
            c = canonical(path)
        except (OSError, ValueError):
            return UNKNOWN
        if any(_inside(c, p) for p in self.protected):
            return PROTECTED
        if any(_inside(c, e) for e in self.excluded):
            return EXCLUDED
        return CLEAR

    def contains_guarded(self, path) -> bool:
        """True if a protected or excluded area lies strictly inside ``path`` (or if unsure)."""
        try:
            c = canonical(path)
        except (OSError, ValueError):
            return True
        return any(_inside(p, c) and p != c for p in self.protected + self.excluded)

    def allows_touch(self, path) -> bool:
        """May BuildDiet read / analyze / list this path as a whole?"""
        return self.status(path) == CLEAR and not self.contains_guarded(path)

    def delete_verdict(self, path) -> Optional[str]:
        """None if ``path`` may be deleted, else the reason. Checks the path, what
        it contains, and where every link inside it points (without following)."""
        st = self.status(path)
        if st != CLEAR:
            return f"{path} is {st if st != UNKNOWN else 'of undetermined protection status'}"
        if self.contains_guarded(path):
            return f"{path} contains a protected or excluded path"
        if fs.is_link(path):
            return f"{path} is a link; BuildDiet never deletes links it did not create"
        for dirpath, _dirs, files in fs.walk(path):
            for name in files:
                full = os.path.join(dirpath, name)
                if fs.is_link(full):
                    if self.status(full) != CLEAR:
                        return f"{full} links into a protected area"
                    try:
                        if any(_inside(p, canonical(full)) for p in self.protected):
                            return f"{full} links to a folder containing a protected area"
                    except (OSError, ValueError):
                        return f"{full} is a link whose target cannot be resolved"
        return None
