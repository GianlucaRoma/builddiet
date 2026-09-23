"""The experiment loop.

    copy project -> sandbox
    baseline:   regenerate + verify (cold), then again (warm)  -> must PASS
    for each candidate directory:
        fingerprint it
        move it aside            (inside the sandbox)
        regenerate + verify      (timed)
        fingerprint what came back, compare
        restore the original bytes
    -> one verdict per candidate
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
from .model import INCONCLUSIVE, NOT_REGENERATED, PROVEN, REQUIRED, UNTESTED
from .sandbox import Sandbox
from .scanner import CANDIDATE, STATUS_DETAIL, scan
from .units import format_duration, format_size
from .verifier import (
    IDENTICAL,
    RECREATED,
    compare,
    fingerprint,
    snapshot,
    snapshot_diff,
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

    base = Path(sandbox_dir) if sandbox_dir else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(base)).free
    needed = int(total * 1.1) + (32 << 20)
    if free < needed and not force:
        raise AnalysisError(
            f"the sandbox needs about {format_size(needed)} but only {format_size(free)} "
            f"is free in {base}; use --sandbox-dir on another drive or --force"
        )

    warnings = []
    original_before = snapshot(root, skip_top=(CONFIG_DIR,))
    env = dict(os.environ)
    env["BUILDDIET"] = "1"

    with Sandbox(root, base=sandbox_dir, keep=keep_sandbox) as sb:
        env["BUILDDIET_SANDBOX"] = str(sb.project)
        log(f"sandbox   {sb.root}")
        log(f"copying   {format_size(total)} ...")
        sb.populate()

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
            before = fingerprint(path, cfg.hash_mode)
            if not before.entries:
                entry.verdict = INCONCLUSIVE
                entry.detail = "missing or empty in the sandbox after the baseline run"
                log(f"{prefix} INCONCLUSIVE: {entry.detail}")
                continue
            token = sb.set_aside(region.path)
            try:
                res = run_workflow(cfg, sb.project, sb.logs / f"experiment-{index}.log", env)
                after = fingerprint(path, cfg.hash_mode)
            finally:
                sb.restore(region.path, token)
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
                identity, detail = compare(before, after)
                entry.identity = identity
                if identity in (IDENTICAL, RECREATED):
                    entry.verdict = PROVEN
                    entry.rebuild_seconds = round(rebuild_penalty(res.seconds, warm.seconds), 3)
                    entry.detail = detail
                else:
                    entry.verdict = NOT_REGENERATED
                    entry.detail = f"the workflow passes without it but does not recreate it: {detail}"
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
