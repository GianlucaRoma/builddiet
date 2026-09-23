"""Choose what to reclaim: the cheapest set of PROVEN directories and files that frees
at least the requested number of bytes.

This is a min-cost covering knapsack. It is solved exactly (up to a size
resolution of target/4000) with dynamic programming. Sizes are rounded
*down*, so the chosen set is guaranteed to free at least the target.

Items are proven one at a time (leave-one-out), which says nothing about
removing them together: two artifacts that regenerate each other are each
PROVEN, but deleting both loses them. :func:`search_verified` therefore
treats every solution as a CANDIDATE and only accepts it once a joint
removal check passes, trying the next-cheapest candidates otherwise.
"""

from __future__ import annotations

import heapq
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .cost import expected_penalty, reuse_probability
from .model import IN_GIT, PROVEN, how
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
    kind: str = "dir"
    how: str = ""

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
            "kind": self.kind,
            "how": self.how,
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


def collect_items(manifests, strict: bool = False, include_git: bool = False, guard=None) -> list:
    """PROVEN entries (and IN_GIT ones, if asked) that still exist on disk, as plan items.
    Anything protected or excluded (``guard``), even if proven earlier, is left out."""
    allowed = {PROVEN, IN_GIT} if include_git else {PROVEN}
    items = []
    for m in manifests:
        root = Path(m["project"])
        if guard is not None and guard.status(root) != "clear":
            continue
        reuse = m.get("reuse", {})
        for e in m["entries"]:
            if e["verdict"] not in allowed or e["bytes"] <= 0:
                continue
            if strict and e.get("identity") != IDENTICAL:
                continue
            if not (root / e["path"]).exists():
                continue
            if guard is not None and guard.delete_verdict(root / e["path"]):
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
                    kind=e.get("kind", "dir"),
                    how=how(e),
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

    fallback = greedy(items, target)
    if dp[cap] == inf:  # only possible through rounding
        return fallback

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
    exact = Plan(target, chosen, True)
    # Sizes were rounded down, so a target that is exactly reachable can look
    # unreachable to the DP; never return something costlier than the greedy plan.
    if fallback.feasible and fallback.cost < exact.cost:
        return fallback
    return exact


@dataclass
class JointCheck:
    """Outcome of removing a group of plan items together in a sandbox."""

    ok: bool
    detail: str
    fatal: bool = False  # no plan for this project can be verified (e.g. baseline fails)
    rebuild_seconds: Optional[float] = None
    identities: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "fatal": self.fatal,
            "rebuild_seconds": self.rebuild_seconds,
            "identities": self.identities,
        }


@dataclass
class Attempt:
    plan: Plan
    checks: dict  # project root -> JointCheck

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks.values())

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "jointly_verified": self.ok,
            "checks": {root: c.to_dict() for root, c in self.checks.items()},
        }


@dataclass
class PlanSearch:
    target: int
    first: Plan
    attempts: list = field(default_factory=list)
    verified: Optional[Attempt] = None
    stopped: str = ""

    def to_dict(self) -> dict:
        return {
            "target_bytes": self.target,
            "feasible": self.first.feasible,
            "jointly_verified": self.verified is not None,
            "verified_plan": self.verified.to_dict() if self.verified else None,
            "candidates": [a.to_dict() for a in self.attempts],
            "stopped": self.stopped,
        }


def item_key(item: PlanItem) -> tuple:
    return (item.root, item.path)


def search_verified(
    items,
    target: int,
    verify_group: Callable[[str, list], JointCheck],
    max_attempts: int = 5,
) -> PlanSearch:
    """Cheapest plan whose items survive being removed together.

    Candidates are explored cheapest first. When a candidate fails, its
    failing items are excluded one at a time and the knapsack is solved again
    (Lawler-style branching), so every plan that avoids at least one of them
    remains reachable. Supersets of a failed set are not tried: removing more
    cannot bring back what a smaller removal lost. ``verify_group(root, items)``
    is called at most once per distinct (project, item set).
    """
    first = solve(items, target)
    search = PlanSearch(target, first)
    if not first.feasible:
        search.stopped = "not enough proven space for this target"
        return search

    memo: dict = {}
    seen: set = set()
    counter = 0
    heap = [(first.cost, counter, first, frozenset())]
    while heap:
        if len(search.attempts) >= max_attempts:
            search.stopped = f"stopped after {max_attempts} candidate plans (--max-attempts)"
            return search
        _, _, plan, excluded = heapq.heappop(heap)
        keys = frozenset(item_key(i) for i in plan.items)
        if keys in seen:
            continue
        seen.add(keys)

        groups: dict = {}
        for item in plan.items:
            groups.setdefault(item.root, []).append(item)
        checks = {}
        for root, group in groups.items():
            memo_key = (root, frozenset(i.path for i in group))
            if memo_key not in memo:
                memo[memo_key] = verify_group(root, group)
            checks[root] = memo[memo_key]
        attempt = Attempt(plan, checks)
        search.attempts.append(attempt)
        if attempt.ok:
            search.verified = attempt
            return search
        if any(c.fatal for c in checks.values()):
            search.stopped = "joint verification cannot run: " + next(
                c.detail for c in checks.values() if c.fatal
            )
            return search

        failing = [i for i in plan.items if not checks[i.root].ok]
        for item in failing:
            banned = excluded | {item_key(item)}
            alternative = solve([i for i in items if item_key(i) not in banned], target)
            if alternative.feasible:
                counter += 1
                heapq.heappush(heap, (alternative.cost, counter, alternative, banned))
    search.stopped = "no other candidate plan reaches the target"
    return search
