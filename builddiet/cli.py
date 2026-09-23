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
from .config import Config, ConfigError, config_path, load_config, write_config
from .experiment import AnalysisError, analyze, verify_joint
from .model import BUCKET_REGENERABLE, bucket, display
from .planner import PlanSearch, collect_items, search_verified, size_first, solve
from .report import render_backup, render_plan, render_report, render_scan, table
from .sandbox import SandboxError
from .scanner import scan
from .units import format_size, parse_size


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
    if not cfg.regenerate and not cfg.verify and not config_path(root).exists():
        raise ConfigError(f"{root} has no BuildDiet config: run `builddiet init` first")
    return cfg.validate()


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
    manifest = analyze(
        root,
        cfg,
        sandbox_dir=Path(args.sandbox_dir) if args.sandbox_dir else None,
        keep_sandbox=args.keep_sandbox,
        only=args.only,
        force=args.force,
        log=_log,
    )
    if not args.no_save:
        _log(f"saved     {manifest_mod.save(root, manifest)}")
    print(json.dumps(manifest, indent=2) if args.json else render_report(manifest))
    return 0


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


def cmd_plan(args) -> int:
    target = parse_size(args.free)
    manifests, skipped = [], []
    for path in args.paths:
        roots = manifest_mod.find(Path(path))
        if not roots:
            skipped.append(f"{path}: no analysis found")
        for root in roots:
            m = manifest_mod.load(root)
            reasons = manifest_mod.staleness(m)
            if reasons and not args.allow_stale:
                skipped.append(f"{m['name']}: stale ({'; '.join(reasons)}); use --allow-stale")
                continue
            manifests.append(m)
    items = collect_items(manifests, strict=args.strict)
    by_root = {str(Path(m["project"])): m for m in manifests}

    def verify_group(root: str, group: list):
        m = by_root[root]
        cfg = load_config(Path(root)) or Config(
            regenerate=m["commands"].get("regenerate"),
            verify=m["commands"].get("verify"),
            hash_mode=m.get("hash_mode", "full"),
        )
        identities = {e["path"]: e.get("identity") for e in m["entries"]}
        return verify_joint(
            Path(root), cfg, [i.path for i in group], identities,
            total_bytes=m["total_bytes"],
            sandbox_dir=Path(args.sandbox_dir) if args.sandbox_dir else None,
            force=args.force,
            log=_log,
        )

    if args.no_verify:
        search = PlanSearch(target, solve(items, target))
    else:
        search = search_verified(items, target, verify_group, max_attempts=args.max_attempts)
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

    p = sub.add_parser("init", help="create .builddiet/config.toml with the verification workflow")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--regenerate", help="command that recreates derived data")
    p.add_argument("--verify", help="command whose success defines 'still works'")
    p.add_argument("--min-size", help="smallest directory worth an experiment (default 10MB)")
    p.add_argument("--yes", "-y", action="store_true", help="accept suggestions without prompting")
    p.add_argument("--force", action="store_true", help="overwrite an existing config")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("analyze", help="run deletion experiments in a sandbox copy")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--regenerate", help="override the configured regenerate command")
    p.add_argument("--verify", help="override the configured verify command")
    p.add_argument("--include", action="append", help="extra directory to test (repeatable)")
    p.add_argument("--only", action="append", help="experiment only on this candidate (repeatable)")
    p.add_argument("--min-size", help="override candidates.min_size")
    p.add_argument("--hash", choices=("full", "meta"), help="identity check strength")
    p.add_argument("--timeout", type=int, help="seconds allowed per workflow run")
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
    p.add_argument("--allow-stale", action="store_true", help="use analyses whose environment changed")
    p.add_argument("--no-verify", action="store_true",
                   help="show the candidate plan without the joint sandbox verification")
    p.add_argument("--max-attempts", type=int, default=5,
                   help="candidate plans to verify before giving up (default 5)")
    p.add_argument("--sandbox-dir", help="where to create verification sandboxes")
    p.add_argument("--force", action="store_true", help="skip the free-space check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("scan", help="summarize every analyzed project under a folder")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("backup-plan", help="which bytes are irreproducible")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--excludes", action="store_true", help="print proven-regenerable paths, one per line")
    p.set_defaults(func=cmd_backup_plan)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, AnalysisError, SandboxError, manifest_mod.ManifestError, ValueError) as exc:
        print(f"builddiet: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("builddiet: interrupted (the original project was not modified)", file=sys.stderr)
        return 130
