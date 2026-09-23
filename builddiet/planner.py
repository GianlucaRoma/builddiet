"""Choose what to reclaim: the cheapest set of PROVEN directories that frees
at least the requested number of bytes.

This is a min-cost covering knapsack. It is solved exactly (up to a size
resolution of target/4000) with dynamic programming. Sizes are rounded
*down*, so the chosen set is guaranteed to free at least the target.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from pathlib import Path

from .cost import expected_penalty, reuse_probability
from .model import PROVEN
from .verifier import IDENTICAL

RESOLUTION = 4000


@dataclass
class PlanItem:
    project: str
    root: str
    path: str
    bytes: int
    rebuild_seconds: float
    reuse: float = 1.0
    identity: str = IDENTICAL

    @property
    def cost(self) -> float:
        return expected_penalty(self.rebuild_seconds, self.reuse)

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "root": self.root,
            "path": self.path,
            "bytes": self.bytes,
            "rebuild_seconds": self.rebuild_seconds,
            "reuse": self.reuse,
            "expected_seconds": self.cost,
            "identity": self.identity,
        }


@dataclass
class Plan:
    target: int
    items: list = field(default_factory=list)
    feasible: bool = True

    @property
    def freed(self) -> int:
        return sum(i.bytes for i in self.items)

    @property
    def cost(self) -> float:
        return sum(i.cost for i in self.items)

    @property
    def rebuild_seconds(self) -> float:
        return sum(i.rebuild_seconds for i in self.items)

    def to_dict(self) -> dict:
        return {
            "target_bytes": self.target,
            "feasible": self.feasible,
            "freed_bytes": self.freed,
            "expected_rebuild_seconds": self.cost,
            "worst_case_rebuild_seconds": self.rebuild_seconds,
            "items": [i.to_dict() for i in self.items],
        }


def collect_items(manifests, strict: bool = False) -> list:
    """PROVEN entries that still exist on disk, as plan items."""
    items = []
    for m in manifests:
        root = Path(m["project"])
        reuse = m.get("reuse", {})
        for e in m["entries"]:
            if e["verdict"] != PROVEN or e["bytes"] <= 0:
                continue
            if strict and e.get("identity") != IDENTICAL:
                continue
            if not (root / e["path"]).exists():
                continue
            items.append(
                PlanItem(
                    project=m.get("name", root.name),
                    root=str(root),
                    path=e["path"],
                    bytes=e["bytes"],
                    rebuild_seconds=e.get("rebuild_seconds") or 0.0,
                    reuse=reuse_probability(reuse, e["path"]),
                    identity=e.get("identity") or IDENTICAL,
                )
            )
    return items


def _efficiency_order(items) -> list:
    return sorted(items, key=lambda i: (i.cost / max(i.bytes, 1), -i.bytes))


def greedy(items, target: int) -> Plan:
    chosen, freed = [], 0
    for item in _efficiency_order(items):
        if freed >= target:
            break
        chosen.append(item)
        freed += item.bytes
    return Plan(target, chosen, freed >= target)


def size_first(items, target: int) -> Plan:
    """Naive baseline for comparison: delete the biggest things first."""
    chosen, freed = [], 0
    for item in sorted(items, key=lambda i: -i.bytes):
        if freed >= target:
            break
        chosen.append(item)
        freed += item.bytes
    return Plan(target, chosen, freed >= target)


def solve(items, target: int, resolution: int = RESOLUTION) -> Plan:
    items = [i for i in items if i.bytes > 0]
    if target <= 0:
        return Plan(target, [], True)
    if sum(i.bytes for i in items) < target:
        return Plan(target, _efficiency_order(items), False)

    unit = max(1, -(-target // resolution))
    cap = -(-target // unit)
    usable = [(i, i.bytes // unit) for i in items if i.bytes // unit > 0]
    inf = float("inf")
    initial = array("d", [inf]) * (cap + 1)
    initial[0] = 0.0
    rows = []
    dp = initial
    for item, size in usable:
        new = array("d", dp)
        cost = item.cost
        for t in range(cap + 1):
            current = dp[t]
            if current == inf:
                continue
            nt = t + size
            if nt > cap:
                nt = cap
            value = current + cost
            if value < new[nt]:
                new[nt] = value
        rows.append(new)
        dp = new

    if dp[cap] == inf:  # only possible through rounding: fall back
        return greedy(items, target)

    chosen = []
    t = cap
    for idx in range(len(usable) - 1, -1, -1):
        prev = rows[idx - 1] if idx > 0 else initial
        cur = rows[idx]
        if cur[t] == prev[t]:
            continue
        item, size = usable[idx]
        if t < cap:
            t -= size
        else:
            t = next(
                tp for tp in range(max(0, cap - size), cap)
                if prev[tp] + item.cost == cur[cap]
            )
        chosen.append(item)
    chosen.reverse()
    return Plan(target, chosen, True)
