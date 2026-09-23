"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import adapters as adapters_mod
from . import manifest as manifest_mod
from .config import Config, ConfigError, load_config, write_config
from .agentlogs import default_log_dirs
from .experiment import AnalysisError, analyze
from .model import BUCKET_REGENERABLE, bucket, display
from . import reclaim as reclaim_mod
from .model import how
from .planner import size_first
from .sandbox import default_sandbox_base
from .service import load_manifests, verified_plan
from .watch import WatchSettings, market, value
from .watch import run as watch_run
from .report import render_backup, render_plan, render_report, render_scan, table
from .sandbox import SandboxError
from .scanner import scan
from .units import format_duration, format_size, parse_duration, parse_size, shorten


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ask(question: str, default):
    shown = f" [{default}]" if default else ""
    answer = input(f"{question}{shown}: ").strip()
    if answer == "-":
        return None
    return answer or default


def cmd_init(args) -> int:
    root = Path(args.path).resolve()
    suggestions = adapters_mod.suggestions(root)
    regenerate = args.regenerate
    verify = args.verify
    for s in suggestions:
        _log(f"detected  {s.adapter}: {s.reason}")
        regenerate = regenerate or s.regenerate
        verify = verify or s.verify
    if sys.stdin.isatty() and not args.yes:
        _log("Commands run only inside a sandbox copy. Enter '-' for none.")
        regenerate = _ask("Regeneration command (recreates derived data)", regenerate)
        verify = _ask("Verification command (the gate that must keep passing)", verify)
    cfg = Config(regenerate=regenerate, verify=verify)
    if args.min_size:
        cfg.min_size = parse_size(args.min_size)
    cfg.validate()
    path = write_config(root, cfg, overwrite=args.force)
    print(f"wrote {path}")
    print("Review candidates/exclusions there, then run:  builddiet analyze " + _quote_arg(args.path))
    return 0


def _quote_arg(value: str) -> str:
    return f'"{value}"' if " " in value else value


def _effective_config(args) -> Config:
    root = Path(args.path).resolve()
    cfg = load_config(root) or Config()
    if args.regenerate is not None:
        cfg.regenerate = args.regenerate or None
    if args.verify is not None:
        cfg.verify = args.verify or None
    if args.hash:
        cfg.hash_mode = args.hash
    if args.timeout:
        cfg.timeout = args.timeout
    if args.min_size:
        cfg.min_size = parse_size(args.min_size)
    for inc in args.include or []:
        cfg.include.append(inc)
    if args.depth:
        cfg.depth = args.depth
    return cfg.validate(require_workflow=False)


def cmd_analyze(args) -> int:
    root = Path(args.path).resolve()
    cfg = _effective_config(args)
    if args.dry_run:
        regions = scan(root, cfg)
        rows = [[r.display, format_size(r.bytes), r.status, r.known or ""]
                for r in sorted(regions, key=lambda r: -r.bytes)]
        print(table(rows, ["path", "size", "status", "known"], right=(1,)))
        n = sum(1 for r in regions if r.status == "candidate")
        print(f"\n{n} candidates would be experimented on. Nothing was run.")
        return 0
    agent_dirs = None
    if args.agent_logs or args.agent_logs_dir:
        agent_dirs = [Path(d) for d in args.agent_logs_dir] if args.agent_logs_dir else default_log_dirs()
    manifest = analyze(
        root,
        cfg,
        sandbox_dir=Path(args.sandbox_dir) if args.sandbox_dir else None,
        keep_sandbox=args.keep_sandbox,
        only=args.only,
        force=args.force,
        log=_log,
        recipes_enabled=not args.no_recipes,
        agent_log_dirs=agent_dirs,
        confirm=lambda found, rejected: _confirm_recipes(found, rejected, args.yes),
        max_tries=args.max_tries,
    )
    if not args.no_save:
        _log(f"saved     {manifest_mod.save(root, manifest)}")
    print(json.dumps(manifest, indent=2) if args.json else render_report(manifest))
    return 0


def _confirm_recipes(found: list, rejected: list, yes: bool) -> bool:
    _log("")
    _log(f"Found {len(found)} possible recipes. They run ONLY inside a sandbox copy;")
    _log("a file counts as proven only if a recipe recreates it byte-for-byte")
    _log("without changing anything else.")
    for r in sorted(found, key=lambda r: -r.strength)[:30]:
        where = f" (in {r.cwd})" if r.cwd else ""
        _log(f"  {shorten(r.command + where, 90)}   <- {r.origin}")
    if len(found) > 30:
        _log(f"  ... and {len(found) - 30} more")
    for command, reason in rejected[:10]:
        _log(f"  never run: {shorten(command, 70)}   <- {reason}")
    if yes:
        return True
    if not sys.stdin.isatty():
        _log("Not run: use --yes to allow these commands in non-interactive mode.")
        return False
    try:
        answer = input("Try them in the sandbox? [Y/n] ").strip().lower()
    except EOFError:
        return False
    return answer in ("", "y", "yes", "s", "si")


def _load_with_warning(root: Path) -> dict:
    m = manifest_mod.load(root)
    for reason in manifest_mod.staleness(m):
        _log(f"! stale analysis: {reason}. Re-run `builddiet analyze`.")
    return m


def cmd_report(args) -> int:
    m = _load_with_warning(Path(args.path).resolve())
    print(json.dumps(m, indent=2) if args.json else render_report(m))
    return 0


def cmd_backup_plan(args) -> int:
    m = _load_with_warning(Path(args.path).resolve())
    if args.excludes:
        for e in m["entries"]:
            if bucket(e) == BUCKET_REGENERABLE:
                print(display(e))
        return 0
    print(render_backup(m))
    return 0


def _plan(args, target: int):
    manifests, skipped = load_manifests(args.paths, allow_stale=args.allow_stale)
    search, items = verified_plan(
        manifests, target,
        sandbox_dir=Path(args.sandbox_dir) if args.sandbox_dir else None,
        force=args.force, max_attempts=args.max_attempts, include_git=args.include_git,
        strict=args.strict, verify=not getattr(args, "no_verify", False), log=_log,
    )
    return manifests, skipped, search, items


def cmd_plan(args) -> int:
    target = parse_size(args.free)
    _manifests, skipped, search, items = _plan(args, target)
    if args.json:
        print(json.dumps(search.to_dict(), indent=2))
    else:
        multi = len({i.root for i in items}) > 1
        print(render_plan(search, size_first(items, target), skipped, multi,
                          verified_requested=not args.no_verify))
    if not search.first.feasible:
        return 3
    if args.no_verify:
        return 0
    return 0 if search.verified else 4


def cmd_reclaim(args) -> int:
    target = parse_size(args.free)
    args.no_verify = False
    args.strict = not args.allow_nondeterministic  # by default only delete what comes back byte-for-byte
    manifests, skipped, search, items = _plan(args, target)
    multi = len({i.root for i in items}) > 1
    print(render_plan(search, size_first(items, target), skipped, multi))
    if search.verified is None:
        print("\nNothing deleted: there is no jointly verified plan.")
        return 3 if not search.first.feasible else 4
    plan = search.verified.plan
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nNothing deleted: pass --yes to reclaim non-interactively.")
            return 5
        try:
            answer = input(f"\nDelete these {len(plan.items)} items and free {format_size(plan.freed)}? "
                           "Type 'reclaim' to confirm: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "reclaim":
            print("Nothing deleted.")
            return 5
    done = reclaim_mod.reclaim(search, manifests, _log)
    print(f"\nFreed {format_size(done.freed)} ({len(done.deleted)} items).")
    for project, reason in done.refused:
        print(f"  ! {project}: nothing deleted, {reason}")
    if done.deleted:
        print("Bring anything back with:  builddiet restore <project> [path ...]")
    return 0 if not done.refused else 6


def cmd_restore(args) -> int:
    root = Path(args.path).resolve()
    records = reclaim_mod.read_log(root)
    if args.list or not records:
        if not records:
            print("Nothing has been reclaimed in this project.")
        for r in records:
            print(f"  {r['path']:40} {format_size(r['bytes']):>10}   deleted {r['deleted_at']}   "
                  f"{how(r)}")
        return 0
    done = reclaim_mod.restore(root, args.items or None, _log)
    for path in done.restored:
        print(f"  restored  {path}  (byte-identical to what was deleted)")
    for path, reason in done.failed:
        print(f"  FAILED    {path}: {reason}")
    if done.also_changed:
        print("  note: your declared workflow (builddiet init) also rewrote these existing files:")
        for rel in done.also_changed[:20]:
            print(f"            {rel}")
    return 0 if not done.failed else 7


def cmd_market(args) -> int:
    manifests, skipped = load_manifests([args.path], allow_stale=args.allow_stale)
    items = market(manifests, include_git=args.include_git)
    for reason in skipped:
        _log(f"! skipped {reason}")
    if not items:
        print("No proven reclaimable space yet. Run `builddiet analyze` or `builddiet watch`.")
        return 0
    base = Path(args.path).resolve()

    def label(i) -> str:
        try:
            rel = Path(i.root).resolve().relative_to(base).as_posix()
        except ValueError:
            rel = i.project
        return i.path if rel == "." else f"{rel}/{i.path}"

    rows = [[label(i), format_size(i.bytes), format_duration(i.rebuild_seconds),
             value(i), shorten(i.how, 50)] for i in items]
    print(table(rows, ["item", "size", "rebuild", "value", "how to get it back"], right=(1, 2)))
    print(f"\n  {format_size(sum(i.bytes for i in items))} provably reclaimable across "
          f"{len({i.root for i in items})} projects. LOW value = cheapest to give up.")
    return 0


def cmd_watch(args) -> int:
    settings = WatchSettings(
        prepare_below=args.prepare_below, reclaim_below=args.reclaim_below,
        aggressive_below=args.aggressive_below, keep_free=args.keep_free,
        max_penalty=parse_duration(args.max_penalty),
        aggressive_max_penalty=parse_duration(args.aggressive_max_penalty),
        auto=args.auto, allow_recipes=args.allow_recipes, agent_logs=args.agent_logs,
        min_size=parse_size(args.min_size),
        sandbox_dir=Path(args.sandbox_dir) if args.sandbox_dir else None,
        dialog=not args.no_dialog, interval=parse_duration(args.interval),
    )
    if not (settings.aggressive_below < settings.reclaim_below < settings.prepare_below <= settings.keep_free):
        raise ConfigError("thresholds must satisfy aggressive < reclaim < prepare <= keep-free")
    base = Path(args.path).resolve()
    _log(f"watching  {base}  (sandboxes: {settings.sandbox_dir or default_sandbox_base()})")
    _log(f"          prepare < {settings.prepare_below}%, reclaim < {settings.reclaim_below}%, "
         f"aggressive < {settings.aggressive_below}%, back to {settings.keep_free}% free")
    _log(f"          automatic reclaim: {'ON' if settings.auto else 'off (asks first)'}; max rebuild "
         f"{format_duration(settings.max_penalty)} ({format_duration(settings.aggressive_max_penalty)} "
         "when critical)")
    watch_run(base, settings, once=args.once, log=_log)
    return 0


def cmd_scan(args) -> int:
    base = Path(args.path).resolve()
    roots = manifest_mod.find(base)
    loaded = []
    for root in roots:
        m = manifest_mod.load(root)
        loaded.append((m, bool(manifest_mod.staleness(m))))
    analyzed = {Path(m["project"]).resolve() for m, _ in loaded}
    unanalyzed = []
    try:
        for entry in sorted(os.scandir(base), key=lambda e: e.name):
            if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                continue
            child = Path(entry.path).resolve()
            if not any(a == child or child in a.parents or a in child.parents for a in analyzed):
                unanalyzed.append(entry.name)
    except OSError:
        pass
    if not loaded:
        print(f"No analyzed projects under {base}.")
        if unanalyzed:
            print("Candidates: " + ", ".join(unanalyzed))
        print("Run `builddiet init <project>` then `builddiet analyze <project>`.")
        return 0
    print(render_scan(loaded, unanalyzed))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="builddiet",
        description="Prove what's disposable. Keep what matters.",
    )
    parser.add_argument("--version", action="version", version=f"builddiet {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="optional: declare a build + verify workflow (advanced mode)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--regenerate", help="command that recreates derived data")
    p.add_argument("--verify", help="command whose success defines 'still works'")
    p.add_argument("--min-size", help="smallest directory worth an experiment (default 10MB)")
    p.add_argument("--yes", "-y", action="store_true", help="accept suggestions without prompting")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("analyze", help="prove what can be deleted (no configuration needed)")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--yes", "-y", action="store_true",
                   help="run the discovered recipes (in the sandbox) without asking")
    p.add_argument("--no-recipes", action="store_true",
                   help="only hash proofs: identical copies, archives, git (runs nothing)")
    p.add_argument("--agent-logs", action="store_true",
                   help="also look for recipes in Codex / Claude Code session logs (read locally)")
    p.add_argument("--agent-logs-dir", action="append",
                   help="read agent session logs from this directory instead of the defaults")
    p.add_argument("--max-tries", type=int, default=3, help="recipes tried per candidate (default 3)")
    p.add_argument("--depth", type=int, help="candidate depth (default 1: top-level entries)")
    p.add_argument("--regenerate", help="override the configured regenerate command")
    p.add_argument("--verify", help="override the configured verify command")
    p.add_argument("--include", action="append", help="extra directory to test (repeatable)")
    p.add_argument("--only", action="append", help="experiment only on this candidate (repeatable)")
    p.add_argument("--min-size", help="override candidates.min_size")
    p.add_argument("--hash", choices=("full", "meta"), help="identity check strength")
    p.add_argument("--timeout", type=int, help="seconds allowed per workflow or recipe run")
    p.add_argument("--sandbox-dir", help="where to create the sandbox (outside the project)")
    p.add_argument("--keep-sandbox", action="store_true", help="keep the sandbox and logs")
    p.add_argument("--force", action="store_true", help="skip the free-space check")
    p.add_argument("--dry-run", action="store_true", help="only list candidates, run nothing")
    p.add_argument("--no-save", action="store_true", help="do not write .builddiet/manifest.json")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="show the last analysis")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("plan", help="cheapest jointly verified way to free SIZE")
    p.add_argument("paths", nargs="*", default=["."], help="projects or folders of projects")
    p.add_argument("--free", required=True, help="space to reclaim, e.g. 20GB")
    p.add_argument("--strict", action="store_true", help="only byte-identical regenerations")
    p.add_argument("--include-git", action="store_true",
                   help="also plan files that are tracked and clean in git (usually source code)")
    p.add_argument("--allow-stale", action="store_true", help="use analyses whose environment changed")
    p.add_argument("--no-verify", action="store_true",
                   help="show the candidate plan without the joint sandbox verification")
    p.add_argument("--max-attempts", type=int, default=5,
                   help="candidate plans to verify before giving up (default 5)")
    p.add_argument("--sandbox-dir", help="where to create verification sandboxes")
    p.add_argument("--force", action="store_true", help="skip the free-space check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("reclaim", help="delete a jointly verified plan (asks first)")
    p.add_argument("paths", nargs="*", default=["."], help="projects or folders of projects")
    p.add_argument("--free", required=True, help="space to reclaim, e.g. 20GB")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--allow-nondeterministic", action="store_true",
                   help="also delete workflow outputs that come back with different bytes (timestamps)")
    p.add_argument("--include-git", action="store_true", help="also delete files restorable from git")
    p.add_argument("--allow-stale", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--max-attempts", type=int, default=5)
    p.add_argument("--sandbox-dir", help="where to create verification sandboxes")
    p.add_argument("--force", action="store_true", help="skip the free-space check")
    p.set_defaults(func=cmd_reclaim)

    p = sub.add_parser("restore", help="bring back what `reclaim` deleted")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("items", nargs="*", help="paths to restore (default: all)")
    p.add_argument("--list", action="store_true", help="only list what was reclaimed")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("market", help="every provably reclaimable item under a folder, cheapest first")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--include-git", action="store_true")
    p.add_argument("--allow-stale", action="store_true")
    p.set_defaults(func=cmd_market)

    p = sub.add_parser("watch", help="watch the disk; prepare, offer or perform reclaims")
    p.add_argument("path", nargs="?", default=".", help="folder with your projects")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    p.add_argument("--interval", default="10m", help="time between cycles (default 10m)")
    p.add_argument("--prepare-below", type=float, default=15.0, help="%% free: prepare a plan (15)")
    p.add_argument("--reclaim-below", type=float, default=10.0, help="%% free: offer to reclaim (10)")
    p.add_argument("--aggressive-below", type=float, default=5.0, help="%% free: bigger budget (5)")
    p.add_argument("--keep-free", type=float, default=20.0, help="%% free to get back to (20)")
    p.add_argument("--max-penalty", default="5m", help="max rebuild time of a reclaim (5m)")
    p.add_argument("--aggressive-max-penalty", default="1h", help="when below --aggressive-below (1h)")
    p.add_argument("--auto", action="store_true", help="reclaim without asking, within the limits")
    p.add_argument("--allow-recipes", action="store_true",
                   help="let analyses run discovered recipes (sandbox only) without asking")
    p.add_argument("--agent-logs", action="store_true", help="also use Codex / Claude Code session logs")
    p.add_argument("--min-size", default="100MB", help="smallest candidate (100MB)")
    p.add_argument("--sandbox-dir", help="sandboxes (default: the local drive with most free space)")
    p.add_argument("--no-dialog", action="store_true", help="never show a desktop dialog")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("scan", help="summarize every analyzed project under a folder")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("backup-plan", help="which bytes are irreproducible")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--excludes", action="store_true", help="print proven-regenerable paths, one per line")
    p.set_defaults(func=cmd_backup_plan)
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # never crash on a console that cannot show a character (e.g. cp1252)
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, AnalysisError, SandboxError, manifest_mod.ManifestError,
            reclaim_mod.ReclaimError, ValueError) as exc:
        print(f"builddiet: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("builddiet: interrupted (the original project was not modified)", file=sys.stderr)
        return 130
