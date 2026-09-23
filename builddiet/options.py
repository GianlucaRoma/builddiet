"""The three reclaim options: LEGGERO / NORMALE / ESTREMO.

The user never says how many GB to delete. BuildDiet computes, from what is
proven, three nested options and presents them.

How an item is placed: by the measured cost of getting *that item* back.
Only byte-identical PROVEN items are considered (the same rule as `reclaim`
and `watch`), and never protected or excluded ones.

    LEGGERO  restore copies bytes that already exist (identical copy, archive,
             git), or the item's measured rebuild time is <= light_max (1s)
    NORMALE  the item's measured rebuild time is <= normal_max (5m)
    ESTREMO  every PROVEN item, whatever its rebuild time

The options are cumulative: LEGGERO ⊆ NORMALE ⊆ ESTREMO. Each option is
jointly verified per project: all of its items are removed together in a
sandbox and must come back byte-for-byte. If that fails, the option is
retried without one item at a time (smallest first), up to max_attempts;
dropped items are reported. NORMALE is the recommended option.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .planner import Attempt, JointCheck, Plan, PlanSearch, collect_items

LIGHT, NORMAL, EXTREME = "LEGGERO", "NORMALE", "ESTREMO"
ORDER = (LIGHT, NORMAL, EXTREME)
ALIASES = {
    "leggero": LIGHT, "light": LIGHT, "1": LIGHT,
    "normale": NORMAL, "normal": NORMAL, "2": NORMAL,
    "estremo": EXTREME, "extreme": EXTREME, "3": EXTREME,
}
RESTORE_BY_COPY = ("duplicate", "archive", "git")


@dataclass
class TierRule:
    light_max: float = 1.0  # seconds
    normal_max: float = 300.0  # seconds

    def tier(self, item, method: Optional[str]) -> str:
        if method in RESTORE_BY_COPY or item.rebuild_seconds <= self.light_max:
            return LIGHT
        if item.rebuild_seconds <= self.normal_max:
            return NORMAL
        return EXTREME


@dataclass
class Option:
    name: str
    items: list = field(default_factory=list)  # jointly verified plan items
    dropped: list = field(default_factory=list)  # (item, reason): eligible but not verified together
    checks: dict = field(default_factory=dict)  # project root -> JointCheck
    methods: dict = field(default_factory=dict)  # item path key -> method
    recommended: bool = False

    @property
    def freed(self) -> int:
        return sum(i.bytes for i in self.items)

    @property
    def rebuild_seconds(self) -> float:
        return sum(i.rebuild_seconds for i in self.items)

    @property
    def verified(self) -> bool:
        return bool(self.items) and all(c.ok for c in self.checks.values())

    def breakdown(self) -> dict:
        out: dict = {}
        for i in self.items:
            kind = self.methods.get((i.root, i.path)) or "workflow"
            label = {"duplicate": "copies", "archive": "archives", "git": "git", "recipe": "recipes"}.get(kind, "workflow")
            out[label] = out.get(label, 0) + 1
        return out

    def as_search(self) -> PlanSearch:
        """The option in the shape `reclaim` accepts (a jointly verified plan)."""
        plan = Plan(self.freed, list(self.items), True)
        return PlanSearch(self.freed, plan, [Attempt(plan, self.checks)], Attempt(plan, self.checks))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "recommended": self.recommended, "verified": self.verified,
            "freed_bytes": self.freed, "rebuild_seconds": round(self.rebuild_seconds, 3),
            "items": [i.to_dict() for i in self.items],
            "dropped": [{"path": i.path, "project": i.project, "reason": r} for i, r in self.dropped],
            "checks": {root: c.to_dict() for root, c in self.checks.items()},
        }


def build_options(
    manifests: list,
    verify_group: Callable[[str, list], JointCheck],
    *,
    guard=None,
    rule: Optional[TierRule] = None,
    include_git: bool = False,
    max_attempts: int = 5,
) -> list:
    """The three options, each jointly verified (``verify_group(root, items)``)."""
    rule = rule or TierRule()
    items = collect_items(manifests, strict=True, include_git=include_git, guard=guard)
    methods = {}
    for m in manifests:
        for e in m["entries"]:
            methods[(str(Path(m["project"])), e["path"])] = e.get("method")
    tiers = {(i.root, i.path): rule.tier(i, methods.get((i.root, i.path))) for i in items}
    memo: dict = {}

    def verify(root: str, group: list) -> JointCheck:
        key = (root, frozenset(i.path for i in group))
        if key not in memo:
            memo[key] = verify_group(root, group)
        return memo[key]

    options = []
    for level, name in enumerate(ORDER):
        eligible = [i for i in items if ORDER.index(tiers[(i.root, i.path)]) <= level]
        option = Option(name, methods=methods, recommended=(name == NORMAL))
        by_root: dict = {}
        for i in eligible:
            by_root.setdefault(i.root, []).append(i)
        for root, group in by_root.items():
            attempts = [group] + [
                [x for x in group if x is not drop]
                for drop in sorted(group, key=lambda i: i.bytes)
            ][: max(0, max_attempts - 1)]
            check = None
            for candidate in attempts:
                if not candidate:
                    continue
                check = verify(root, candidate)
                if check.ok:
                    option.items += candidate
                    option.dropped += [(x, "not verified together with the others: " + memo_reason(memo, root, group))
                                       for x in group if x not in candidate]
                    option.checks[root] = check
                    break
                if check.fatal:
                    break
            else:
                check = check or JointCheck(False, "nothing to verify")
            if root not in option.checks:
                option.dropped += [(x, check.detail if check else "not verified") for x in group]
        options.append(option)
    if not options[1].items and options[0].items:
        options[1].recommended = False
        options[0].recommended = True
    return options


def memo_reason(memo: dict, root: str, group: list) -> str:
    check = memo.get((root, frozenset(i.path for i in group)))
    return check.detail if check else "joint check failed"


def pick_for_need(options: list, needed: int, allowed: tuple) -> Optional[Option]:
    """The smallest allowed option that frees ``needed`` bytes, else the largest allowed one."""
    usable = [o for o in options if o.name in allowed and o.verified]
    for o in usable:
        if o.freed >= needed:
            return o
    return max(usable, key=lambda o: o.freed, default=None)
