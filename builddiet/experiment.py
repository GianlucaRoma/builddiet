"""The experiment loop.

    copy project -> sandbox
    fingerprint every candidate          (= the user's original bytes)
    baseline:   regenerate + verify (cold), then again (warm)  -> must PASS
    for each candidate directory or file:
        move it aside            (inside the sandbox)
        regenerate + verify      (timed)
        fingerprint what came back, compare with the ORIGINAL bytes
        restore
        if it came back different: repeat once more, to tell a stale/corrupt
        original (same bytes both times) from nondeterministic output
    -> one verdict per candidate

Fingerprints are taken before the baseline on purpose: the baseline may
refresh a stale file, and the claim BuildDiet makes is about the bytes the
user has on disk, not about the refreshed ones.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import adapters as adapters_mod
from . import manifest as manifest_mod
from .config import CONFIG_DIR, Config
from .cost import rebuild_penalty
from .model import INCONCLUSIVE, NOT_REGENERATED, PROVEN, REQUIRED, STALE, UNTESTED
from .planner import JointCheck
from .sandbox import Sandbox
from .scanner import CANDIDATE, STATUS_DETAIL, scan
from .units import format_duration, format_size
from .verifier import (
    IDENTICAL,
    RECREATED,
    STALE_COPY,
    compare,
    example,
    fingerprint,
    snapshot,
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
    base = Path(sandbox_dir) if sandbox_dir else Path(tempfile.gettempdir())
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
    paths: list,
    proven_identity: dict,
    *,
    total_bytes: int,
    sandbox_dir: Optional[Path] = None,
    force: bool = False,
    log: Callable[[str], None] = lambda _msg: None,
) -> JointCheck:
    """Remove all ``paths`` together in a fresh sandbox and apply the PROVEN invariants.

    Same rules as a single experiment: the untouched copy must pass twice,
    the workflow must pass with every item removed, every file of every item
    must come back, and each must be byte-identical to the user's copy
    (or, for items individually proven nondeterministic, fully recreated).
    """
    root = Path(root).resolve()
    cfg.validate()
    _check_space(total_bytes, sandbox_dir, force)
    original_before = snapshot(root, skip_top=(CONFIG_DIR,))
    env = _workflow_env()
    with Sandbox(root, base=sandbox_dir) as sb:
        env["BUILDDIET_SANDBOX"] = str(sb.project)
        log(f"joint     {len(paths)} items of {root.name}: copying {format_size(total_bytes)} ...")
        sb.populate()
        targets = {p: sb.target(p) for p in paths}
        originals = {p: fingerprint(t, cfg.hash_mode) for p, t in targets.items()}
        gone = [p for p, fp in originals.items() if not fp.entries]
        if gone:
            return JointCheck(False, f"{gone[0]} is missing or empty now; re-run `builddiet analyze`")

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

        for p in paths:
            sb.set_aside(p)
        res = run_workflow(cfg, sb.project, sb.logs / "joint.log", env)
        if not res.ok:
            return JointCheck(False, f"with {_items(paths)} removed together, {_describe_failure(res)}")

        identities, problems = {}, []
        for p in paths:
            identity, detail = compare(originals[p], fingerprint(targets[p], cfg.hash_mode))
            identities[p] = identity
            if identity == IDENTICAL:
                continue
            if identity == RECREATED and proven_identity.get(p) == RECREATED:
                continue  # individually proven nondeterministic: full recreation is the invariant
            problems.append(f"{p}: {detail}")
        if problems:
            return JointCheck(
                False,
                f"with {_items(paths)} removed together, the workflow passed but "
                + "; ".join(problems),
                identities=identities,
            )
        rebuild = round(rebuild_penalty(res.seconds, warm_seconds), 3)

    changed = snapshot_diff(original_before, snapshot(root, skip_top=(CONFIG_DIR,)))
    if changed:
        return JointCheck(
            False,
            f"{len(changed)} files in the ORIGINAL project changed during verification "
            f"(e.g. {changed[0]}); the result cannot be trusted",
            fatal=True,
        )
    return JointCheck(
        True,
        f"with {_items(paths)} removed together, everything was recreated and verify passed",
        rebuild_seconds=rebuild,
        identities=identities,
    )


def analyze(
    root: Path,
    cfg: Config,
    *,
    sandbox_dir: Optional[Path] = None,
    keep_sandbox: bool = False,
    only: Optional[list] = None,
    force: bool = False,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise AnalysisError(f"{root} is not a directory")
    cfg.validate()
    adapters = adapters_mod.detect(root)
    regions = scan(root, cfg, adapters)
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

    _check_space(total, sandbox_dir, force)
    warnings = []
    original_before = snapshot(root, skip_top=(CONFIG_DIR,))
    env = _workflow_env()

    with Sandbox(root, base=sandbox_dir, keep=keep_sandbox) as sb:
        env["BUILDDIET_SANDBOX"] = str(sb.project)
        log(f"sandbox   {sb.root}")
        log(f"copying   {format_size(total)} ...")
        sb.populate()
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

        if keep_sandbox:
            warnings.append(f"sandbox kept at {sb.root}")

    changed = snapshot_diff(original_before, snapshot(root, skip_top=(CONFIG_DIR,)))
    if changed:
        warnings.append(
            f"{len(changed)} files in the ORIGINAL project changed during the analysis "
            f"(e.g. {changed[0]}). BuildDiet did not write them; your commands may use "
            "absolute paths or the project was edited meanwhile. Treat results with care."
        )

    baseline = {
        "cold_seconds": round(cold.seconds, 3),
        "warm_seconds": round(warm.seconds, 3),
    }
    return manifest_mod.build(
        root,
        cfg,
        entries=[asdict(e) for e in entries.values()],
        baseline=baseline,
        total_bytes=total,
        warnings=warnings,
    )
