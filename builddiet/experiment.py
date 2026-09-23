"""Orchestration: analyze a project, and jointly verify plans.

    level 0  hash proofs, nothing runs              (level0.py)
    level 1  discovered recipes, byte-identical     (recipes.py, autoprove.py)
    level 2  a declared workflow (`builddiet init`), the loop below:

        copy project -> sandbox
        fingerprint every candidate          (= the user's original bytes)
        baseline:   regenerate + verify (cold), then again (warm)  -> must PASS
        for each candidate directory or file:
            move it aside, regenerate + verify (timed), compare with the
            ORIGINAL bytes, restore; if different, repeat once more to tell a
            stale/corrupt original (same bytes both times) from nondeterminism

Fingerprints are taken before any run on purpose: a run may refresh a stale
file, and the claim BuildDiet makes is about the bytes the user has on disk.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import adapters as adapters_mod
from . import level0, recipes
from . import manifest as manifest_mod
from .autoprove import RecipeLab, prove_candidate
from .config import CONFIG_DIR, Config
from .cost import rebuild_penalty
from .model import IN_GIT, INCONCLUSIVE, NOT_REGENERATED, PROVEN, REQUIRED, STALE, UNTESTED
from .planner import JointCheck
from .sandbox import Sandbox, default_sandbox_base
from .protect import Guard
from .scanner import CANDIDATE, EXCLUDED, LINK, PROTECTED, STATUS_DETAIL, USER_EXCLUDED, scan
from .units import format_duration, format_size, shorten
from .verifier import (
    IDENTICAL,
    RECREATED,
    STALE_COPY,
    compare,
    example,
    fingerprint,
    snapshot,
    signature,
    snapshot_diff,
    split_differences,
)

_TAIL_BYTES = 4000


class AnalysisError(RuntimeError):
    pass


@dataclass
class RunResult:
    ok: bool
    seconds: float
    failed_step: Optional[str] = None
    returncode: Optional[int] = None
    timed_out: bool = False
    log_tail: str = ""


@dataclass
class Entry:
    path: str
    kind: str
    bytes: int
    files: int
    verdict: str = UNTESTED
    detail: str = ""
    identity: Optional[str] = None
    rebuild_seconds: Optional[float] = None
    run_seconds: Optional[float] = None
    failed_step: Optional[str] = None
    known: Optional[str] = None
    log_tail: str = ""
    method: Optional[str] = None  # duplicate / archive / git (level 0), recipe (1), workflow (2)
    recipe: Optional[str] = None  # level 1: the command that recreated it
    recipe_cwd: str = ""
    recipe_origin: str = ""
    recovery: Optional[dict] = None  # level 0: how to restore it
    signature: Optional[str] = None  # SHA-256 of the proven bytes; reclaim deletes only these


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _TAIL_BYTES))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def run_workflow(cfg: Config, cwd: Path, log_path: Path, env: Optional[dict] = None) -> RunResult:
    """Run regenerate then verify in ``cwd``. Output goes to ``log_path``."""
    steps = [(n, c) for n, c in (("regenerate", cfg.regenerate), ("verify", cfg.verify)) if c]
    start = time.perf_counter()
    deadline = start + cfg.timeout
    popen_extra = {} if os.name == "nt" else {"start_new_session": True}
    with open(log_path, "wb") as log:
        for name, command in steps:
            log.write(f"$ {command}\n".encode())
            log.flush()
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                **popen_extra,
            )
            try:
                rc = proc.wait(timeout=max(0.1, deadline - time.perf_counter()))
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.wait()
                log.close()
                return RunResult(False, time.perf_counter() - start, name, None, True, _tail(log_path))
            except BaseException:
                _kill_tree(proc)
                raise
            if rc != 0:
                log.close()
                return RunResult(False, time.perf_counter() - start, name, rc, False, _tail(log_path))
    return RunResult(True, time.perf_counter() - start)


def _describe_failure(res: RunResult) -> str:
    if res.timed_out:
        return f"{res.failed_step} timed out"
    return f"{res.failed_step} failed (exit {res.returncode})"


def _check_space(total: int, sandbox_dir: Optional[Path], force: bool) -> None:
    base = Path(sandbox_dir) if sandbox_dir else default_sandbox_base()
    base.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(base)).free
    needed = int(total * 1.1) + (32 << 20)
    if free < needed and not force:
        raise AnalysisError(
            f"the sandbox needs about {format_size(needed)} but only {format_size(free)} "
            f"is free in {base}; use --sandbox-dir on another drive or --force"
        )


def _items(paths) -> str:
    return "the item" if len(paths) == 1 else f"all {len(paths)} items"


def _workflow_env() -> dict:
    env = dict(os.environ)
    env["BUILDDIET"] = "1"
    return env


def verify_joint(
    root: Path,
    cfg: Config,
    entries: list,
    *,
    total_bytes: int,
    sandbox_dir: Optional[Path] = None,
    force: bool = False,
    log: Callable[[str], None] = lambda _msg: None,
    guard: Optional[Guard] = None,
) -> JointCheck:
    """Remove every plan item of one project together and re-check the PROVEN invariants.

    ``entries`` are the manifest entries of the plan items. Level-0 items are
    restored from their copy / archive / git, level-1 items by re-running their
    recipes, level-2 items by the workflow. Every item must then be
    byte-identical to the user's copy (workflow items individually proven
    nondeterministic: fully recreated). Without a workflow, no other existing
    file may change either.
    """
    root = Path(root).resolve()
    cfg.validate(require_workflow=False)
    guard = guard if guard is not None else Guard()
    paths = [e["path"] for e in entries]
    for p in paths:
        reason = guard.delete_verdict(root / p)
        if reason:
            return JointCheck(False, f"refused: {reason}")

    def inside_plan(rel: str) -> bool:
        return any(rel == p or rel.startswith(p + "/") or p.startswith(rel + "/") for p in paths)

    restored = [e for e in entries if e.get("recovery")]
    for e in restored:
        src = e["recovery"].get("source")
        if src and inside_plan(src):
            return JointCheck(False, f"{e['path']} is restored from {src}, which this plan also removes")
        if not level0.source_unchanged(root, e["recovery"]):
            return JointCheck(False, f"the source of {e['path']} ({src or 'git HEAD'}) changed since "
                                     "the analysis; re-run `builddiet analyze`")
    rebuilt = [e for e in entries if not e.get("recovery")]
    if not rebuilt:
        return JointCheck(
            True,
            ("the item has" if len(paths) == 1 else f"all {len(paths)} items have")
            + " an identical copy, archive or git source outside the plan, unchanged since the analysis",
            rebuild_seconds=round(sum(e.get("rebuild_seconds") or 0 for e in entries), 3),
        )

    workflow = bool(cfg.regenerate or cfg.verify)
    recipe_items = [e for e in rebuilt if e.get("method") == "recipe"]
    if any(e.get("method") != "recipe" for e in rebuilt) and not workflow:
        return JointCheck(False, "the workflow these items were proven with is no longer configured", fatal=True)

    _check_space(total_bytes, sandbox_dir, force)
    original_before = snapshot(root, skip_top=(CONFIG_DIR,), guard=guard)
    env = _workflow_env()
    with Sandbox(root, base=sandbox_dir, guard=guard) as sb:
        env["BUILDDIET_SANDBOX"] = str(sb.project)
        log(f"joint     {len(paths)} items of {root.name}: copying {format_size(total_bytes)} ...")
        sb.populate()
        targets = {p: sb.target(p) for p in paths}
        originals = {p: fingerprint(t, "full") for p, t in targets.items()}
        gone = [p for p, fp in originals.items() if not fp.entries]
        if gone:
            return JointCheck(False, f"{gone[0]} is missing or empty now; re-run `builddiet analyze`")

        warm_seconds = 0.0
        if workflow:
            for name in ("cold", "warm"):
                base_run = run_workflow(cfg, sb.project, sb.logs / f"joint-baseline-{name}.log", env)
                if not base_run.ok:
                    return JointCheck(
                        False,
                        f"the workflow no longer passes on the untouched copy "
                        f"({_describe_failure(base_run)}); re-run `builddiet analyze`",
                        fatal=True,
                    )
            warm_seconds = base_run.seconds
        lab = RecipeLab(root, sb, cfg, env, run_command) if not workflow else None

        for p in paths:
            sb.set_aside(p)
        start = time.perf_counter()
        for e in restored:
            try:
                level0.restore(e["recovery"], sb.project, e["path"])
            except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError) as exc:
                return JointCheck(False, f"restoring {e['path']} from {e['recovery'].get('source') or 'git'} "
                                         f"failed: {exc}")
        recipes_seen: list = []
        for e in recipe_items:
            key = (e.get("recipe"), e.get("recipe_cwd", ""))
            if key not in recipes_seen:
                recipes_seen.append(key)
        for _ in range(2):  # a second pass covers recipes that consume each other's outputs
            if all(os.path.lexists(targets[e["path"]]) for e in recipe_items):
                break
            for command, cwd in recipes_seen:
                recipe = recipes.Recipe(command, "plan", 0, cwd or "")
                ok, _seconds, tail = lab.run_recipe(recipe, "-joint")
                if not ok:
                    return JointCheck(False, f"with {_items(paths)} removed together, `{command}` failed: "
                                             f"{tail.strip()[-200:]}")
        if workflow and any(e.get("method") != "recipe" for e in rebuilt):
            res = run_workflow(cfg, sb.project, sb.logs / "joint.log", env)
            if not res.ok:
                return JointCheck(False, f"with {_items(paths)} removed together, {_describe_failure(res)}")
        elapsed = time.perf_counter() - start

        identities, problems = {}, []
        for e in entries:
            p = e["path"]
            identity, detail = compare(originals[p], fingerprint(targets[p], "full"))
            identities[p] = identity
            if identity == IDENTICAL:
                continue
            if identity == RECREATED and e.get("method") == "workflow" and e.get("identity") == RECREATED:
                continue  # individually proven nondeterministic: full recreation is the invariant
            problems.append(f"{p}: {detail}")
        if problems:
            return JointCheck(
                False,
                f"with {_items(paths)} removed together, not everything came back: " + "; ".join(problems),
                identities=identities,
            )
        if lab is not None:
            changed = [c for c in lab.changed_files() if not inside_plan(c)]
            if changed:
                return JointCheck(False, f"restoring the plan also changed {changed[0]}", identities=identities)
        rebuild = round(rebuild_penalty(elapsed, warm_seconds), 3)

    changed = snapshot_diff(original_before, snapshot(root, skip_top=(CONFIG_DIR,), guard=guard))
    if changed:
        return JointCheck(
            False,
            f"{len(changed)} files in the ORIGINAL project changed during verification "
            f"(e.g. {changed[0]}); the result cannot be trusted",
            fatal=True,
        )
    return JointCheck(
        True,
        f"with {_items(paths)} removed together, everything was restored byte-for-byte"
        + (" and verify passed" if workflow else ""),
        rebuild_seconds=rebuild,
        identities=identities,
    )


def run_command(command: str, cwd: Path, log_path: Path, timeout: float, env: Optional[dict] = None) -> tuple:
    """Run one shell command. Returns (ok, seconds, output tail)."""
    cfg = Config(regenerate=command, timeout=int(max(1, timeout)))
    res = run_workflow(cfg, cwd, log_path, env)
    return res.ok, res.seconds, res.log_tail


def _level0(root: Path, targets: list, entries: dict, log: Callable[[str], None], guard=None) -> set:
    recoveries = level0.find_recoverable(root, targets, log, guard)
    for path, rec in recoveries.items():
        entry = entries[path]
        entry.verdict = IN_GIT if rec.method == level0.GIT else PROVEN
        entry.method = rec.method
        entry.identity = IDENTICAL
        entry.rebuild_seconds = round(rec.seconds, 3)
        entry.detail = rec.detail + (" (restore time estimated)" if rec.estimated else "")
        entry.recovery = rec.to_dict()
    return set(recoveries)


def _keep_notes(entries: dict) -> None:
    """Candidates that hold the source of a level-0 recovery must be kept: say so."""
    for path, entry in list(entries.items()):
        source = (entry.recovery or {}).get("source")
        if not source:
            continue
        owner = next((p for p in entries if source == p or source.startswith(p + "/")), None)
        if owner and entries[owner].verdict not in (PROVEN, IN_GIT):
            note = (f"keep: the source of {path} (an identical copy)" if owner == source
                    else f"keep: {path} is restored from {source}")
            entries[owner].detail = f"{note}; {entries[owner].detail}" if entries[owner].detail else note


def _workflow_mode(cfg, sb, targets, entries, env, log) -> dict:
    originals = {r.path: fingerprint(sb.target(r.path), cfg.hash_mode) for r in targets}
    log("baseline  cold run ...")
    cold = run_workflow(cfg, sb.project, sb.logs / "baseline-cold.log", env)
    if not cold.ok:
        raise AnalysisError(
            f"baseline failed: {_describe_failure(cold)} on the untouched copy. "
            f"BuildDiet can only prove things against a passing workflow.\n{cold.log_tail}"
        )
    log(f"baseline  cold PASS {format_duration(cold.seconds)}; warm run ...")
    warm = run_workflow(cfg, sb.project, sb.logs / "baseline-warm.log", env)
    if not warm.ok:
        raise AnalysisError(
            f"second baseline run failed ({_describe_failure(warm)}): the workflow is "
            f"not repeatable, so experiments would be meaningless.\n{warm.log_tail}"
        )
    log(f"baseline  warm PASS {format_duration(warm.seconds)}")

    for index, region in enumerate(targets, 1):
        entry = entries[region.path]
        entry.method = "workflow"
        prefix = f"[{index}/{len(targets)}] {region.display} ({format_size(region.bytes)})"
        path = sb.target(region.path)
        original = originals[region.path]
        if not original.entries or not os.path.lexists(path):
            entry.verdict = INCONCLUSIVE
            entry.detail = "empty, or removed by the baseline run itself"
            log(f"{prefix} INCONCLUSIVE: {entry.detail}")
            continue

        def trial(tag: str):
            token = sb.set_aside(region.path)
            try:
                result = run_workflow(cfg, sb.project, sb.logs / f"experiment-{index}{tag}.log", env)
                return result, fingerprint(path, cfg.hash_mode)
            finally:
                sb.restore(region.path, token)

        res, after = trial("")
        entry.run_seconds = round(res.seconds, 3)
        if res.timed_out:
            entry.verdict = INCONCLUSIVE
            entry.detail = f"{res.failed_step} timed out after {format_duration(cfg.timeout)}"
            entry.log_tail = res.log_tail
        elif not res.ok:
            entry.verdict = REQUIRED
            entry.failed_step = res.failed_step
            entry.detail = f"without it, {_describe_failure(res)}"
            entry.log_tail = res.log_tail
        else:
            identity, detail = compare(original, after)
            entry.identity = identity
            entry.detail = detail
            if identity == IDENTICAL:
                entry.verdict = PROVEN
            elif identity == RECREATED:
                res2, after2 = trial("-repeat")
                stale, varying = split_differences(original, after, after2)
                if not res2.ok:
                    entry.verdict = INCONCLUSIVE
                    entry.detail = f"a repeated regeneration failed ({_describe_failure(res2)}): flaky workflow"
                elif stale:
                    entry.verdict = STALE
                    entry.identity = STALE_COPY
                    entry.detail = (
                        f"{len(stale)} of {len(original)} files are regenerated identically twice "
                        f"but differ from the current copy{example(stale)}: stale or corrupt "
                        "output, or hand edits. The current bytes are not reproducible."
                    )
                else:
                    entry.verdict = PROVEN
                    entry.detail = (
                        f"all {len(original)} files recreated; {len(varying)} differ on every "
                        f"regeneration (nondeterministic output){example(varying)}"
                    )
            else:
                entry.verdict = NOT_REGENERATED
                entry.detail = f"the workflow passes without it but does not recreate it: {detail}"
            if entry.verdict in (PROVEN, STALE):
                entry.rebuild_seconds = round(rebuild_penalty(res.seconds, warm.seconds), 3)
        extra = f", rebuild {format_duration(entry.rebuild_seconds)}" if entry.verdict == PROVEN else ""
        log(f"{prefix} {entry.verdict.upper()}{extra}")
    return {"cold_seconds": round(cold.seconds, 3), "warm_seconds": round(warm.seconds, 3)}


def _recipe_mode(root, cfg, sb, targets, entries, discovery, env, log, max_tries) -> list:
    """Level 1. Returns a report of every recipe that was probed."""
    lab = RecipeLab(root, sb, cfg, env, run_command, candidates=[r.path for r in targets])
    originals = {r.path: fingerprint(sb.target(r.path), "full") for r in targets}
    probes: dict = {}  # recipe key -> (ok, warm_seconds, reason)
    report = []

    def probed(recipe):
        if recipe.key not in probes:
            log(f"probe     {shorten(recipe.command, 90)}  [{recipe.origin}]")
            probes[recipe.key] = lab.probe(recipe)
            ok, seconds, reason = probes[recipe.key]
            report.append({"command": recipe.command, "cwd": recipe.cwd, "origin": recipe.origin,
                           "usable": ok, "warm_seconds": round(seconds, 3), "reason": reason})
            if not ok:
                log(f"          rejected: {reason}")
        return probes[recipe.key][0]

    for index, region in enumerate(targets, 1):
        entry = entries[region.path]
        prefix = f"[{index}/{len(targets)}] {region.display} ({format_size(region.bytes)})"
        ranked = discovery.ranked_for(region.path, max_tries * 2)
        usable = [r for r in ranked if probed(r)][:max_tries]
        if not usable:
            entry.verdict = NOT_REGENERATED
            entry.detail = "no usable recipe found for it" if ranked else "no recipe found for it"
            log(f"{prefix} NOT PROVEN: {entry.detail}")
            continue
        outcome = prove_candidate(lab, region.path, originals[region.path], usable)
        entry.verdict = {"proven": PROVEN, "stale": STALE, "inconclusive": INCONCLUSIVE}.get(
            outcome.verdict, NOT_REGENERATED)
        entry.detail = outcome.detail
        entry.identity = outcome.identity
        entry.rebuild_seconds = outcome.rebuild_seconds
        entry.run_seconds = outcome.run_seconds
        if outcome.recipe is not None:
            entry.method = "recipe"
            entry.recipe = outcome.recipe.command
            entry.recipe_cwd = outcome.recipe.cwd
            entry.recipe_origin = outcome.recipe.origin
        extra = f", rebuild {format_duration(entry.rebuild_seconds)}" if entry.verdict == PROVEN else ""
        label = "NOT PROVEN" if entry.verdict == NOT_REGENERATED else entry.verdict.upper()
        log(f"{prefix} {label}{extra}")

    return report


def analyze(
    root: Path,
    cfg: Config,
    *,
    sandbox_dir: Optional[Path] = None,
    keep_sandbox: bool = False,
    only: Optional[list] = None,
    force: bool = False,
    log: Callable[[str], None] = lambda _msg: None,
    recipes_enabled: bool = True,
    agent_log_dirs: Optional[list] = None,
    confirm: Optional[Callable[[list, list], bool]] = None,
    max_tries: int = 3,
    guard: Optional[Guard] = None,
) -> dict:
    """Analyze ``root``.

    Level 0 (hash proofs) always runs. Then, for what is left: if the config
    declares a workflow, the workflow experiments run (level 2); otherwise
    recipes are discovered and, once ``confirm`` approves them, tested with
    the byte-identity invariant (level 1).
    """
    guard = guard if guard is not None else Guard()
    state = guard.status(root)  # before anything inside the folder is read
    if state != "clear":
        raise AnalysisError(f"{root} is {state if state != 'unknown' else 'of undetermined protection status'}; "
                            "it is not analyzed")
    root = Path(root).resolve()
    if not root.is_dir():
        raise AnalysisError(f"{root} is not a directory")
    cfg.validate(require_workflow=False)
    workflow = bool(cfg.regenerate or cfg.verify)
    adapters = adapters_mod.detect(root)
    regions = scan(root, cfg, adapters, guard)
    total = sum(r.bytes for r in regions)

    entries = {
        r.path: Entry(r.path, r.kind, r.bytes, r.files, detail=STATUS_DETAIL[r.status], known=r.known)
        for r in regions
    }
    only_set = {p.replace("\\", "/").rstrip("/") for p in only} if only else None
    targets = [r for r in regions if r.status == CANDIDATE and (only_set is None or r.path in only_set)]
    if only_set:
        missing = only_set - {r.path for r in targets}
        if missing:
            raise AnalysisError(
                "not experiment candidates: " + ", ".join(sorted(missing))
                + " (add them to candidates.include or lower min_size)"
            )

    warnings: list = []
    original_before = snapshot(root, skip_top=(CONFIG_DIR,), guard=guard)
    log(f"level 0   checking {len(targets)} candidates for identical copies, archives and git ...")
    done = _level0(root, targets, entries, log, guard)
    remaining = [r for r in targets if r.path not in done]

    baseline = None
    recipe_report: list = []
    rejected: list = []
    mode = "hash-only"
    discovery = None
    if remaining and not workflow and recipes_enabled:
        excluded = {r.path for r in regions if r.status in (EXCLUDED, PROTECTED, USER_EXCLUDED, LINK)}
        discovery = recipes.discover(root, [r.path for r in remaining], excluded, agent_log_dirs, guard)
        rejected = [{"command": c, "reason": why} for c, why in discovery.rejected]
        if not discovery.recipes:
            for r in remaining:
                entries[r.path].detail = "no recipe found for it"
            discovery = None
        elif confirm is None or not confirm(discovery.recipes, discovery.rejected):
            for r in remaining:
                entries[r.path].detail = "recipes were found but not run (re-run with --yes)"
            discovery = None

    if remaining and (workflow or discovery is not None):
        _check_space(total, sandbox_dir, force)
        env = _workflow_env()
        with Sandbox(root, base=sandbox_dir, keep=keep_sandbox, guard=guard) as sb:
            env["BUILDDIET_SANDBOX"] = str(sb.project)
            log(f"sandbox   {sb.root}")
            log(f"copying   {format_size(total)} ...")
            sb.populate()
            if workflow:
                mode = "workflow"
                baseline = _workflow_mode(cfg, sb, remaining, entries, env, log)
            else:
                mode = "recipes"
                recipe_report = _recipe_mode(root, cfg, sb, remaining, entries, discovery, env, log, max_tries)
            if keep_sandbox:
                warnings.append(f"sandbox kept at {sb.root}")

    _keep_notes(entries)
    for entry in entries.values():
        if entry.verdict in (PROVEN, IN_GIT):
            entry.signature = signature(fingerprint(root / entry.path, "full"))
    changed = snapshot_diff(original_before, snapshot(root, skip_top=(CONFIG_DIR,), guard=guard))
    if changed:
        warnings.append(
            f"{len(changed)} files in the ORIGINAL project changed during the analysis "
            f"(e.g. {changed[0]}). BuildDiet did not write them; your commands may use "
            "absolute paths or the project was edited meanwhile. Treat results with care."
        )

    manifest = manifest_mod.build(
        root,
        cfg,
        entries=[asdict(e) for e in entries.values()],
        baseline=baseline,
        total_bytes=total,
        warnings=warnings,
    )
    manifest["mode"] = mode
    manifest["recipes"] = recipe_report
    manifest["rejected_recipes"] = rejected
    return manifest
