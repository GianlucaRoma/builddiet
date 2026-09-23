"""Adapter interface.

An adapter contributes two things, both *hints* only:

* ``suggest``: a regenerate/verify workflow proposal for ``builddiet init``;
* ``known``: a catalog of directory names that are regenerable by convention
  in that ecosystem. A catalog match is reported as KNOWN, never as PROVEN.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Suggestion:
    adapter: str
    regenerate: Optional[str]
    verify: Optional[str]
    reason: str


class Adapter:
    name = "base"
    markers: tuple = ()
    known: dict = {}

    def detect(self, root: Path) -> bool:
        return any((root / marker).exists() for marker in self.markers)

    def suggest(self, root: Path) -> Optional[Suggestion]:
        return None

    def known_reason(self, dirname: str) -> Optional[str]:
        lowered = dirname.lower()
        for pattern, reason in self.known.items():
            if fnmatch.fnmatchcase(lowered, pattern.lower()):
                return reason
        return None


def read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""
