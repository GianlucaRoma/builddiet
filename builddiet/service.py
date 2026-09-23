"""Operations shared by `plan`, `reclaim`, `market` and `watch`."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import manifest as manifest_mod
from .config import Config, load_config
from .experiment import verify_joint
from .planner import PlanSearch, collect_items, search_verified, solve


def load_manifests(paths, allow_stale: bool = False) -> tuple:
    """(fresh manifests, list of 'skipped ...' reasons) for projects at or below ``paths``."""
    manifests, skipped = [], []
    for path in paths:
        roots = manifest_mod.find(Path(path))
        if not roots:
            skipped.append(f"{path}: no analysis found")
        for root in roots:
            m = manifest_mod.load(root)
            reasons = manifest_mod.staleness(m)
            if reasons and not allow_stale:
                skipped.append(f"{m['name']}: stale ({'; '.join(reasons)}); re-run `builddiet analyze`")
                continue
            manifests.append(m)
    return manifests, skipped


def project_config(m: dict) -> Config:
    root = Path(m["project"])
    cfg = load_config(root) or Config(
        regenerate=m["commands"].get("regenerate"),
        verify=m["commands"].get("verify"),
        hash_mode=m.get("hash_mode", "full"),
    )
    return cfg.validate(require_workflow=False)


def affordable_target(items: list, target: int, max_cost: Optional[float]) -> int:
    """The target itself if its cheapest plan fits ``max_cost``; otherwise the most
    bytes a cheapest-per-byte selection can free within that budget."""
    if max_cost is None:
        return target
    best = solve(items, target)
    if best.feasible and best.cost <= max_cost:
        return target
    freed, spent = 0, 0.0
    for item in sorted(items, key=lambda i: i.cost / max(i.bytes, 1)):
        if spent + item.cost > max_cost:
            continue
        spent += item.cost
        freed += item.bytes
        if freed >= target:
            break
    return min(freed, target)


def verified_plan(
    manifests: list,
    target: int,
    *,
    sandbox_dir: Optional[Path] = None,
    force: bool = False,
    max_attempts: int = 5,
    include_git: bool = False,
    strict: bool = False,
    verify: bool = True,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple:
    """(PlanSearch, plan items) for ``target`` bytes across ``manifests``."""
    items = collect_items(manifests, strict=strict, include_git=include_git)
    by_root = {str(Path(m["project"])): m for m in manifests}

    def verify_group(root: str, group: list):
        m = by_root[root]
        by_path = {e["path"]: e for e in m["entries"]}
        return verify_joint(
            Path(root), project_config(m), [by_path[i.path] for i in group],
            total_bytes=m["total_bytes"], sandbox_dir=sandbox_dir, force=force, log=log,
        )

    if not verify:
        return PlanSearch(target, solve(items, target)), items
    return search_verified(items, target, verify_group, max_attempts=max_attempts), items
