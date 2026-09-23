"""`builddiet watch DIR`: keep the disk healthy without being asked.

Every cycle:

    free >= prepare_below   keep the "market" of proofs up to date (analyze new or
                            stale workspaces while there is room for a sandbox)
    free <  prepare_below   compute and JOINTLY VERIFY the cheapest plan that gets
                            back to keep_free, within max_penalty; report it
    free <  reclaim_below   offer it (dialog / terminal), or reclaim it with --auto
    free <  aggressive_below  same, with aggressive_max_penalty

Sandboxes go to the local drive with the most free space (or --sandbox-dir),
so verification still works when the watched drive is nearly full.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import manifest as manifest_mod
from . import notify, workspaces
from .agentlogs import default_log_dirs
from .config import Config, load_config
from .experiment import AnalysisError, analyze
from .planner import collect_items
from .reclaim import reclaim
from .service import affordable_target, load_manifests, verified_plan
from .units import format_duration, format_size

OK, PREPARE, RECLAIM, AGGRESSIVE = "ok", "prepare", "reclaim", "aggressive"


@dataclass
class WatchSettings:
    prepare_below: float = 15.0  # percent free
    reclaim_below: float = 10.0
    aggressive_below: float = 5.0
    keep_free: float = 20.0  # percent free to get back to
    max_penalty: float = 300.0  # seconds of rebuild a normal reclaim may cost
    aggressive_max_penalty: float = 3600.0
    auto: bool = False
    allow_recipes: bool = False  # run discovered recipes unattended (sandbox only)
    agent_logs: bool = False
    min_size: int = 100_000_000
    sandbox_dir: Optional[Path] = None
    dialog: bool = True
    max_attempts: int = 5
    interval: float = 600.0


@dataclass
class DiskStatus:
    total: int
    free: int

    @property
    def free_pct(self) -> float:
        return 100.0 * self.free / self.total if self.total else 100.0


def disk_status(path: Path, usage: Callable = shutil.disk_usage) -> DiskStatus:
    u = usage(str(path))
    return DiskStatus(u.total, u.free)


def level(status: DiskStatus, s: WatchSettings) -> str:
    pct = status.free_pct
    if pct < s.aggressive_below:
        return AGGRESSIVE
    if pct < s.reclaim_below:
        return RECLAIM
    if pct < s.prepare_below:
        return PREPARE
    return OK


def needed_bytes(status: DiskStatus, s: WatchSettings) -> int:
    return max(0, int(status.total * s.keep_free / 100.0) - status.free)


def refresh_market(root: Path, s: WatchSettings, log: Callable[[str], None]) -> list:
    """Analyze workspaces with no analysis or a stale one; return the fresh manifests."""
    for ws in workspaces.discover(root):
        try:
            current = manifest_mod.load(ws)
            if not manifest_mod.staleness(current):
                continue
        except manifest_mod.ManifestError:
            pass
        cfg = load_config(ws) or Config(min_size=s.min_size)
        log(f"market    analyzing {ws}")
        try:
            m = analyze(
                ws, cfg, sandbox_dir=s.sandbox_dir, log=lambda _m: None,
                recipes_enabled=s.allow_recipes,
                confirm=lambda *_: s.allow_recipes,
                agent_log_dirs=default_log_dirs() if s.agent_logs else None,
            )
        except (AnalysisError, OSError, ValueError) as exc:
            log(f"market    skipped {ws.name}: {exc}")
            continue
        manifest_mod.save(ws, m)
    manifests, _skipped = load_manifests([root])
    return manifests


VALUE_LOW, VALUE_MEDIUM, VALUE_HIGH = "LOW", "MEDIUM", "HIGH"


def value(item) -> str:
    """How precious an item is to keep: expected rebuild seconds per GB."""
    per_gb = item.cost / max(item.bytes / 1e9, 1e-9)
    if per_gb < 10:
        return VALUE_LOW
    if per_gb < 300:
        return VALUE_MEDIUM
    return VALUE_HIGH


def market(manifests: list, include_git: bool = False) -> list:
    """What `watch` could reclaim across projects (byte-identical items), cheapest first."""
    return sorted(collect_items(manifests, strict=True, include_git=include_git),
                  key=lambda i: (i.cost / max(i.bytes, 1), -i.bytes))


@dataclass
class CycleResult:
    level: str
    free_pct: float
    needed: int = 0
    plan_bytes: int = 0
    plan_cost: float = 0.0
    verified: bool = False
    reclaimed: int = 0
    message: str = ""
    refused: list = field(default_factory=list)


def cycle(root: Path, s: WatchSettings, *, usage: Callable = shutil.disk_usage,
          ask: Callable = notify.ask, log: Callable[[str], None] = print,
          interactive: Optional[bool] = None) -> CycleResult:
    root = Path(root).resolve()
    status = disk_status(root, usage)
    lvl = level(status, s)
    log(f"disk      {status.free_pct:.1f}% free ({format_size(status.free)} of "
        f"{format_size(status.total)}) -> {lvl}")
    manifests = refresh_market(root, s, log)
    result = CycleResult(lvl, status.free_pct)
    if lvl == OK:
        return result

    result.needed = needed_bytes(status, s)
    cap = s.aggressive_max_penalty if lvl == AGGRESSIVE else s.max_penalty
    items = collect_items(manifests, strict=True)  # watch only deletes what comes back byte-for-byte
    target = affordable_target(items, result.needed, cap)
    if target <= 0:
        result.message = (f"Disk space is low ({status.free_pct:.0f}% free), but nothing proven can be "
                          f"reclaimed within {format_duration(cap)} of rebuild.")
        log(result.message)
        return result
    search, _ = verified_plan(manifests, target, sandbox_dir=s.sandbox_dir,
                              max_attempts=s.max_attempts, strict=True, log=log)
    if search.verified is None:
        result.message = f"No jointly verified plan: {search.stopped}."
        log(result.message)
        return result
    plan = search.verified.plan
    if plan.cost > cap + 1e-9:
        result.message = (f"The cheapest verified plan would cost {format_duration(plan.cost)} of rebuild, "
                          f"more than the {format_duration(cap)} allowed; nothing proposed.")
        log(result.message)
        return result
    result.verified = True
    result.plan_bytes, result.plan_cost = plan.freed, plan.rebuild_seconds
    short = "" if plan.freed >= result.needed else (
        f"\n(needed {format_size(result.needed)} to get back to {s.keep_free:.0f}% free; only "
        f"{format_size(plan.freed)} is proven reclaimable within {format_duration(cap)} of rebuild)")
    result.message = (
        f"Disk space is low ({status.free_pct:.0f}% free).\n"
        f"BuildDiet can safely reclaim {format_size(plan.freed)}.\n"
        f"Expected worst-case rebuild cost: {format_duration(plan.rebuild_seconds)}.\n"
        f"Joint verification: PASS{short}"
    )
    log(result.message)
    for item in plan.items:
        try:
            where = Path(item.root).resolve().relative_to(root).as_posix()
        except ValueError:
            where = item.project
        where = "" if where == "." else where + "/"
        log(f"          {where}{item.path}  {format_size(item.bytes)}  {item.how}")
    if lvl == PREPARE:
        return result

    if s.auto:
        go = True
    else:
        go = ask("BuildDiet", result.message, f"Reclaim {format_size(plan.freed)}") if s.dialog else None
        if go is None and (interactive if interactive is not None else sys.stdin.isatty()):
            try:
                go = input(f"Reclaim {format_size(plan.freed)} now? [y/N] ").strip().lower() in ("y", "yes", "s", "si")
            except EOFError:
                go = False
    if not go:
        log("reclaim   not approved; nothing deleted")
        return result
    done = reclaim(search, manifests, log)
    result.reclaimed = done.freed
    result.refused = done.refused
    log(f"reclaim   freed {format_size(done.freed)}"
        + (f"; refused: {done.refused}" if done.refused else "")
        + ". Bring anything back with `builddiet restore <project>`.")
    return result


def run(root: Path, s: WatchSettings, once: bool = False, log: Callable[[str], None] = print) -> None:
    while True:
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        log(f"--- {started}")
        try:
            cycle(root, s, log=log)
        except Exception as exc:  # a watcher must survive one bad cycle
            log(f"error     {exc}")
        if once:
            return
        time.sleep(s.interval)
