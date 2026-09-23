"""Level 1 experiments: prove a candidate with a discovered recipe, no verify command.

The invariant replaces "the tests pass" with something stronger:

    remove C in the sandbox, run recipe R
    -> C comes back byte-for-byte identical to the user's copy, and
    -> no other file the user had changed content or disappeared

If both hold, the project after regeneration is byte-for-byte the project
before (new side files such as logs or __pycache__ are allowed, and removed
again). Nothing about the project's behaviour needs to be assumed.

Every recipe is first probed on the untouched copy: it must exit 0 and must
not change any existing file. A recipe that fails the probe is never used.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import fs
from .config import CONFIG_DIR, Config
from .recipes import Recipe, project_python, render
from .sandbox import Sandbox, force_remove
from .units import shorten
from .verifier import IDENTICAL, RECREATED, compare, fingerprint, split_differences

_TAIL = 2000

# Files a recipe may rewrite as a side effect without it counting as "changed
# something else": logs, bytecode and tool caches. This list only tolerates side
# effects; it never proves anything.
VOLATILE_DIRS = {"logs", "log", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                 ".cache", ".tox", ".nox", "tmp", "temp"}
VOLATILE_SUFFIXES = (".log", ".pyc", ".pyo", ".tmp", ".pid")


def is_volatile(rel: str) -> bool:
    parts = rel.split("/")
    return any(p.lower() in VOLATILE_DIRS for p in parts[:-1]) or parts[-1].lower().endswith(VOLATILE_SUFFIXES)


@dataclass
class State:
    files: dict  # posix rel path -> (size, mtime_ns)
    dirs: set


def snapshot(project: Path) -> State:
    files, dirs = {}, set()
    base = str(project)
    for dirpath, dirnames, filenames in fs.walk(base):
        rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        if not rel_dir:
            dirnames[:] = [d for d in dirnames if d != CONFIG_DIR]
        for d in dirnames:
            dirs.add(f"{rel_dir}/{d}".lstrip("/"))
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            files[f"{rel_dir}/{name}".lstrip("/")] = (st.st_size, st.st_mtime_ns)
    return State(files, dirs)


def _within(rel: str, container: Optional[str]) -> bool:
    return container is not None and (rel == container or rel.startswith(container + "/"))


@dataclass
class Trial:
    ok: bool  # exit code 0 within the timeout
    seconds: float
    after: object = None  # Fingerprint of the candidate after the run
    changed: list = field(default_factory=list)  # pre-existing files whose content changed or vanished
    tail: str = ""


class RecipeLab:
    """Runs recipes in one sandbox and keeps the sandbox equal to the original."""

    def __init__(self, root: Path, sb: Sandbox, cfg: Config, env: dict,
                 run: Callable, candidates=(), hasher_cache: Optional[dict] = None):
        self.root = Path(root)
        self.sb = sb
        self.cfg = cfg
        self.env = env
        self.run = run  # run_command(command, cwd, log_path, timeout, env) -> (ok, seconds, tail)
        self.candidates = list(candidates)
        self.base = snapshot(sb.project)
        self.python = project_python(sb.project)
        self._orig_hash: dict = hasher_cache if hasher_cache is not None else {}
        self._n = 0

    # -- state ------------------------------------------------------------
    def _original_digest(self, rel: str) -> Optional[str]:
        if rel not in self._orig_hash:
            fp = fingerprint(self.root / rel, "full")
            self._orig_hash[rel] = fp.entries.get(".", (None, None))[1]
        return self._orig_hash[rel]

    def _sandbox_digest(self, rel: str) -> Optional[str]:
        fp = fingerprint(self.sb.project / rel, "full")
        return fp.entries.get(".", (None, None))[1]

    def changed_files(self, exclude: Optional[str] = None) -> list:
        """Pre-existing, non-volatile files (outside ``exclude``) whose bytes changed or vanished."""
        now = snapshot(self.sb.project)
        changed = []
        for rel, meta in self.base.files.items():
            if _within(rel, exclude) or is_volatile(rel):
                continue
            current = now.files.get(rel)
            if current is None:
                changed.append(rel)
            elif current != meta and self._sandbox_digest(rel) != self._original_digest(rel):
                changed.append(rel)
        return sorted(changed)

    def resync(self, exclude: Optional[str] = None) -> None:
        """Make the sandbox equal to the original again (outside ``exclude``)."""
        now = snapshot(self.sb.project)
        for rel in sorted(set(now.files) - set(self.base.files), reverse=True):
            if not _within(rel, exclude):
                force_remove(self.sb.target(rel))
        for rel in sorted(now.dirs - self.base.dirs, key=len, reverse=True):
            if not _within(rel, exclude) and os.path.isdir(self.sb.project / rel):
                force_remove(self.sb.target(rel))
        for rel, meta in self.base.files.items():
            if _within(rel, exclude):
                continue
            if now.files.get(rel) != meta:
                dst = self.sb.target(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(dst):
                    force_remove(dst)
                shutil.copy2(self.root / rel, dst)

    # -- runs ---------------------------------------------------------------
    def run_recipe(self, recipe: Recipe, tag: str) -> tuple:
        self._n += 1
        cwd = self.sb.project / recipe.cwd if recipe.cwd else self.sb.project
        command = render(recipe.command, self.sb.project, self.python)
        return self.run(command, cwd, self.sb.logs / f"recipe-{self._n}{tag}.log", self.cfg.timeout, self.env)

    def _in_candidate(self, rel: str) -> bool:
        return any(_within(rel, c) for c in self.candidates)

    def probe(self, recipe: Recipe) -> tuple:
        """(ok, warm_seconds, reason). Runs twice on the untouched copy.

        A recipe that changes an existing file outside every candidate is
        never used. Rewriting a candidate (e.g. "fixing" a stale output) is
        allowed here; each trial then decides for its own candidate.
        """
        seconds = 0.0
        for attempt in ("cold", "warm"):
            ok, seconds, tail = self.run_recipe(recipe, f"-probe-{attempt}")
            changed = self.changed_files()
            self.resync()
            if not ok:
                return False, seconds, f"fails on the untouched project: {tail.strip()[-200:]}"
            outside = [c for c in changed if not self._in_candidate(c)]
            if outside:
                return False, seconds, f"modifies existing files ({outside[0]})"
        return True, seconds, ""

    def trial(self, recipe: Recipe, candidate: str, tag: str) -> Trial:
        """Remove ``candidate``, run ``recipe``. Any change to another existing file is
        reported: a recipe cannot tell a stale output it refreshes from user data it
        overwrites, so neither is accepted."""
        token = self.sb.set_aside(candidate)
        try:
            ok, seconds, tail = self.run_recipe(recipe, tag)
            after = fingerprint(self.sb.target(candidate), "full")
            changed = self.changed_files(exclude=candidate)
        finally:
            self.sb.restore(candidate, token)
            self.resync(exclude=candidate)
        return Trial(ok, seconds, after, changed, tail[-_TAIL:])


@dataclass
class Outcome:
    verdict: str  # proven / stale / inconclusive / not-regenerated
    detail: str
    recipe: Optional[Recipe] = None
    identity: Optional[str] = None
    rebuild_seconds: Optional[float] = None
    run_seconds: Optional[float] = None
    tried: list = field(default_factory=list)


def prove_candidate(lab: RecipeLab, candidate: str, original, recipes: list) -> Outcome:
    """Try ``recipes`` (already probed, best first) until one proves ``candidate``.

    The rebuild cost is the full run time of the recipe: unlike a declared
    workflow, nobody runs a discovered recipe anyway, so nothing is subtracted.
    """
    tried = []
    for n, recipe in enumerate(recipes, 1):
        trial = lab.trial(recipe, candidate, f"-{n}")
        label = shorten(recipe.command, 50)
        if not trial.ok:
            tried.append(f"{label}: failed without it")
            continue
        identity, detail = compare(original, trial.after)
        if identity not in (IDENTICAL, RECREATED):
            tried.append(f"{label}: did not recreate it ({detail})")
            continue
        if trial.changed:
            tried.append(f"{label}: recreates it but also changes {trial.changed[0]}, "
                         "so running it would alter other data")
            continue
        rebuild = round(trial.seconds, 3)
        if identity == IDENTICAL:
            return Outcome("proven", f"`{label}` recreates it byte-for-byte and changes nothing else "
                                     f"({recipe.origin})", recipe, IDENTICAL, rebuild, round(trial.seconds, 3), tried)
        second = lab.trial(recipe, candidate, f"-{n}-repeat")
        stale, varying = split_differences(original, trial.after, second.after)
        if second.ok and stale:
            return Outcome("stale", f"`{label}` deterministically produces different bytes than your "
                                    "copy: stale or corrupt output, or hand edits. The current bytes are "
                                    "not reproducible.", recipe, "stale", rebuild, round(trial.seconds, 3), tried)
        return Outcome("inconclusive", f"`{label}` recreates it, but with different bytes on every run; "
                                       "declare a verify command (`builddiet init`) to prove it works",
                       recipe, RECREATED, None, round(trial.seconds, 3), tried)
    detail = "no discovered recipe recreated it"
    if tried:
        detail += f" (tried {len(tried)}: " + "; ".join(tried[:2]) + ("; ..." if len(tried) > 2 else "") + ")"
    return Outcome("not-regenerated", detail, tried=tried)
