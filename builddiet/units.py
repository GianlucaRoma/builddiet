"""Human-friendly sizes and durations."""

from __future__ import annotations

import re
from typing import Optional, Union

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*([a-zA-Z]*)\s*$")

_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 10**3,
    "kb": 10**3,
    "m": 10**6,
    "mb": 10**6,
    "g": 10**9,
    "gb": 10**9,
    "t": 10**12,
    "tb": 10**12,
    "kib": 2**10,
    "mib": 2**20,
    "gib": 2**30,
    "tib": 2**40,
}


def parse_size(text: Union[str, int, float]) -> int:
    """Parse '20GB', '512 MiB', '1.5tb' or a plain number of bytes."""
    if isinstance(text, bool):
        raise ValueError(f"not a size: {text!r}")
    if isinstance(text, (int, float)):
        return int(text)
    match = _SIZE_RE.match(str(text))
    if not match:
        raise ValueError(f"not a size: {text!r}")
    unit = match.group(2).lower()
    if unit not in _MULTIPLIERS:
        raise ValueError(f"unknown size unit {match.group(2)!r} in {text!r}")
    return int(float(match.group(1)) * _MULTIPLIERS[unit])


def format_size(n: int) -> str:
    n = int(n)
    for unit, factor in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if abs(n) >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{n} B"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 10:
        return f"{seconds:.1f}s"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"
