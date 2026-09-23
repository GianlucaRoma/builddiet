"""`builddiet watch DIR`: keep the disk healthy without being asked.

Every cycle:

    free >= prepare_below   keep the "market" of proofs up to date (analyze new or
                            stale workspaces while there is room for a sandbox)
    free <  prepare_below   compute the three JOINTLY VERIFIED options (LEGGERO /
                            NORMALE / ESTREMO) and the one to propose: the smallest
                            that gets back to keep_free (LEGGERO or NORMALE)
    free <  reclaim_below   offer it (dialog / terminal), or reclaim it with --auto
                            if it is not above --auto-max (default NORMALE)
    free <  aggressive_below  ESTREMO may be proposed too

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
from .protect import Guard
from .reclaim import reclaim
from .sandbox import SandboxError
from .options import LIGHT, NORMAL, ORDER, TierRule, pick_for_need
from .service import load_manifests, reclaim_options
from .units import format_duration, format_size

OK, PREPARE, RECLAIM, AGGRESSIVE = "ok", "prepare", "reclaim", "aggressive"


@dataclass
class WatchSettings:
    prepare_below: float = 15.0  # percent free
    reclaim_below: float = 10.0
    aggressive_below: float = 5.0
    keep_free: float = 20.0  # percent free to get back to
    light_max: float = 1.0  # LEGGERO / NORMALE boundary (see options.py)
    normal_max: float = 300.0  # NORMALE / ESTREMO boundary
    auto: bool = False
    auto_max: str = "NORMALE"  # --auto never reclaims a bigger option than this
    allow_recipes: bool = False  # run discovered recipes unattended (sandbox only)
    agent_logs: bool = False
    min_size: int = 100_000_000
    sandbox_dir: Optional[Path] = None
    dialog: bool = True
    max_attempts: int = 5
    interval: float = 600.0
    excludes: tuple = ()  # temporary exclusions (--exclude); protections always apply


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


def refresh_market(root: Path, s: WatchSettings, log: Callable[[str], None], guard: Guard) -> list:
    """Analyze workspaces with no analysis or a stale one; return the fresh manifests."""
    for ws in workspaces.discover(root, guard=guard):
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
                guard=guard,
            )
        except (AnalysisError, SandboxError, OSError, ValueError) as exc:
            log(f"market    skipped {ws.name}: {exc}")
            continue
        manifest_mod.save(ws, m)
    manifests, _skipped = load_manifests([root], guard=guard)
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


def market(manifests: list, include_git: bool = False, guard=None) -> list:
    """What `watch` could reclaim across projects (byte-identical items), cheapest first."""
    return sorted(collect_items(manifests, strict=True, include_git=include_git, guard=guard),
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
    option: Optional[str] = None
    options: list = field(default_factory=list)


def cycle(root: Path, s: WatchSettings, *, usage: Callable = shutil.disk_usage,
          ask: Callable = notify.ask, log: Callable[[str], None] = print,
          interactive: Optional[bool] = None) -> CycleResult:
    guard = Guard(s.excludes)  # re-read every cycle: a protection added meanwhile applies at once
    if guard.status(root) != "clear":
        log(f"refused   {root} is protected or excluded; watching nothing")
        return CycleResult(OK, 100.0, message="watched folder is protected")
    root = Path(root).resolve()
    status = disk_status(root, usage)
    lvl = level(status, s)
    log(f"disk      {status.free_pct:.1f}% free ({format_size(status.free)} of "
        f"{format_size(status.total)}) -> {lvl}")
    manifests = refresh_market(root, s, log, guard)
    result = CycleResult(lvl, status.free_pct)
    if lvl == OK:
        return result

    result.needed = needed_bytes(status, s)
    rule = TierRule(light_max=s.light_max, normal_max=s.normal_max)
    options = reclaim_options(manifests, sandbox_dir=s.sandbox_dir, max_attempts=s.max_attempts,
                              rule=rule, log=log, guard=guard)
    result.options = options
    allowed = ORDER if lvl == AGGRESSIVE else (LIGHT, NORMAL)
    chosen = pick_for_need(options, result.needed, allowed)
    lines = [f"Disk space is low ({status.free_pct:.0f}% free); {format_size(result.needed)} needed "
             f"to get back to {s.keep_free:.0f}% free."]
    for o in options:
        state = "PASS" if o.verified else "-"
        lines.append(f"  {o.name:8} {format_size(o.freed):>9}   rebuild {format_duration(o.rebuild_seconds):>7}"
                     f"   {len(o.items)} items   joint verification {state}")
    if chosen is None:
        result.message = "\n".join(lines + ["Nothing is jointly verified to reclaim."])
        log(result.message)
        return result
    result.verified = True
    result.option = chosen.name
    result.plan_bytes, result.plan_cost = chosen.freed, chosen.rebuild_seconds
    short = "" if chosen.freed >= result.needed else " (not enough to reach the target, but the most allowed now)"
    lines.append(f"Proposed: {chosen.name}: BuildDiet can safely reclaim {format_size(chosen.freed)}; "
                 f"expected worst-case rebuild {format_duration(chosen.rebuild_seconds)}.{short}")
    result.message = "\n".join(lines)
    log(result.message)
    for item in chosen.items:
        try:
            where = Path(item.root).resolve().relative_to(root).as_posix()
        except ValueError:
            where = item.project
        where = "" if where == "." else where + "/"
        log(f"          {where}{item.path}  {format_size(item.bytes)}  {item.how}")
    if lvl == PREPARE:
        return result

    within_auto = ORDER.index(chosen.name) <= ORDER.index(s.auto_max)
    if s.auto and within_auto:
        go = True
    else:
        if s.auto:
            log(f"auto      {chosen.name} is above --auto-max {s.auto_max}: asking instead")
        go = ask("BuildDiet", result.message, f"Reclaim {chosen.name} ({format_size(chosen.freed)})") \
            if s.dialog else None
        if go is None and (interactive if interactive is not None else sys.stdin.isatty()):
            try:
                go = input(f"Reclaim {chosen.name} ({format_size(chosen.freed)}) now? [y/N] ").strip().lower() \
                    in ("y", "yes", "s", "si")
            except EOFError:
                go = False
    if not go:
        log("reclaim   not approved; nothing deleted")
        return result
    done = reclaim(chosen.as_search(), manifests, log, excludes=s.excludes)
    result.reclaimed = done.freed
    result.refused = done.refused
    log(f"reclaim   {chosen.name}: freed {format_size(done.freed)}"
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
