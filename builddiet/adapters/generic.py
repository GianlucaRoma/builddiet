from __future__ import annotations

import re
from pathlib import Path

from .base import Adapter, Suggestion, read_text


class GenericAdapter(Adapter):
    """Always active. Knows a few universal cache names and Makefiles."""

    name = "generic"
    known = {".cache": "generic cache directory"}

    def detect(self, root: Path) -> bool:
        return True

    def suggest(self, root: Path):
        makefile = root / "Makefile"
        if not makefile.exists():
            return None
        text = read_text(makefile)
        verify = None
        for target in ("test", "check"):
            if re.search(rf"^{target}\s*:", text, re.MULTILINE):
                verify = f"make {target}"
                break
        return Suggestion(self.name, "make", verify, "Makefile found")
