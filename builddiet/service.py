"""Operations shared by `plan`, `reclaim`, `market` and `watch`."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import manifest as manifest_mod
from .config import Config, load_config
from .experiment import verify_joint
from .planner import PlanSearch, collect_items, search_verified, solve
from .protect import Guard


def load_manifests(paths, allow_stale: bool = False, guard: Optional[Guard] = None) -> tuple:
    """(fresh manifests, list of 'skipped ...' reasons) for projects at or below ``paths``.
    Protected / excluded folders are not entered."""
    guard = guard if guard is not None else Guard()
    manifests, skipped = [], []
    for path in paths:
        state = guard.status(path)
        if state != "clear":
            skipped.append(f"{path}: {state}")
            continue
        roots = manifest_mod.find(Path(path), guard=guard)
        if not roots:
            skipped.append(f"{path}: no analysis found")
        for root in roots:
            m = manifest_mod.load(root)
            reasons = manifest_mod.staleness(m, guard)
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
    guard: Optional[Guard] = None,
) -> tuple:
    """(PlanSearch, plan items) for ``target`` bytes across ``manifests``."""
    guard = guard if guard is not None else Guard()
    items = collect_items(manifests, strict=strict, include_git=include_git, guard=guard)
    verify_group = joint_verifier(manifests, sandbox_dir=sandbox_dir, force=force, log=log, guard=guard)
    if not verify:
        return PlanSearch(target, solve(items, target)), items
    return search_verified(items, target, verify_group, max_attempts=max_attempts), items


def joint_verifier(manifests: list, *, sandbox_dir: Optional[Path] = None, force: bool = False,
                   log: Callable[[str], None] = lambda _m: None, guard: Optional[Guard] = None) -> Callable:
    """verify_group(root, plan_items) -> JointCheck, for projects in ``manifests``."""
    guard = guard if guard is not None else Guard()
    by_root = {str(Path(m["project"])): m for m in manifests}

    def verify_group(root: str, group: list):
        m = by_root[root]
        by_path = {e["path"]: e for e in m["entries"]}
        return verify_joint(
            Path(root), project_config(m), [by_path[i.path] for i in group],
            total_bytes=m["total_bytes"], sandbox_dir=sandbox_dir, force=force, log=log, guard=guard,
        )

    return verify_group


def reclaim_options(manifests: list, *, sandbox_dir: Optional[Path] = None, force: bool = False,
                    include_git: bool = False, rule=None, max_attempts: int = 5,
                    log: Callable[[str], None] = lambda _m: None, guard: Optional[Guard] = None) -> list:
    """LEGGERO / NORMALE / ESTREMO for ``manifests``, each jointly verified."""
    from .options import build_options

    guard = guard if guard is not None else Guard()
    verify_group = joint_verifier(manifests, sandbox_dir=sandbox_dir, force=force, log=log, guard=guard)
    return build_options(manifests, verify_group, guard=guard, rule=rule, include_git=include_git,
                         max_attempts=max_attempts)
