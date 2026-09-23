"""Cost model.

rebuild penalty  = time of the workflow with the directory removed
                   - time of a warm workflow with everything present
expected penalty = reuse probability x rebuild penalty

The warm baseline is subtracted so that a 10 minute test suite does not make
every directory look like it costs 10 minutes to rebuild.
"""

from __future__ import annotations

import fnmatch
from typing import Optional


def rebuild_penalty(run_seconds: float, warm_seconds: float) -> float:
    return max(0.0, run_seconds - warm_seconds)


def reuse_probability(reuse: dict, path: str) -> float:
    """Most specific matching entry of the [reuse] table wins; default 1.0."""
    best: Optional[tuple] = None
    for pattern, p in reuse.items():
        pat = pattern.replace("\\", "/").rstrip("/")
        if path == pat or path.startswith(pat + "/") or fnmatch.fnmatch(path, pat):
            if best is None or len(pat) > len(best[0]):
                best = (pat, float(p))
    return best[1] if best else 1.0


def expected_penalty(rebuild_seconds: float, probability: float) -> float:
    return rebuild_seconds * probability
