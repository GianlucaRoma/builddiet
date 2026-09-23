"""Reclaim (really delete) a jointly verified plan, and restore it later.

This is the only module that deletes anything outside a sandbox. Rules:

* only items of a JOINTLY VERIFIED plan, from analyses that are not stale;
* every item is re-hashed right before deletion and must be byte-for-byte
  what was proven (the manifest signature); level-0 sources must be unchanged;
  if anything differs, nothing of that project is deleted;
* every deletion is appended to <project>/.builddiet/reclaimed.json with
  everything needed to bring it back, and `restore` does that and re-checks
  the bytes.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import level0
from . import manifest as manifest_mod
from .config import CONFIG_DIR, normalize_rel
from .experiment import _workflow_env, run_command, run_workflow
from .recipes import project_python, render
from .sandbox import force_remove, is_within
from .protect import Guard
from .service import project_config
from .verifier import fingerprint, signature, snapshot, snapshot_diff

LOG_FILE = "reclaimed.json"


class ReclaimError(RuntimeError):
    pass


@dataclass
class ReclaimResult:
    deleted: list = field(default_factory=list)  # (project, path, bytes)
    refused: list = field(default_factory=list)  # (project, reason)

    @property
    def freed(self) -> int:
        return sum(b for _, _, b in self.deleted)


def log_path(root: Path) -> Path:
    return Path(root) / CONFIG_DIR / LOG_FILE


def read_log(root: Path) -> list:
    path = log_path(root)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_log(root: Path, records: list) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _target(root: Path, rel: str) -> Path:
    rel = normalize_rel(rel)
    path = Path(os.path.abspath(root / rel))
    if path == root or not is_within(path, root) or rel.split("/")[0] == CONFIG_DIR:
        raise ReclaimError(f"refusing to touch {path}")
    return path


def _preflight(root: Path, entries: list, guard: Guard) -> Optional[str]:
    """Why this project's items must not be deleted now, or None. Protection is
    checked first, before anything inside an item is read."""
    for e in entries:
        path = _target(root, e["path"])
        reason = guard.delete_verdict(path)
        if reason:
            return reason
        if not os.path.lexists(path):
            return f"{e['path']} no longer exists"
        if not e.get("signature"):
            return f"{e['path']} has no recorded signature; re-run `builddiet analyze`"
        if signature(fingerprint(path, "full")) != e["signature"]:
            return f"{e['path']} changed since it was proven; re-run `builddiet analyze`"
        source = (e.get("recovery") or {}).get("source")
        if source and guard.status(root / source) != "clear":
            return f"the source of {e['path']} ({source}) is protected or excluded"
        if e.get("recovery") and not level0.source_unchanged(root, e["recovery"]):
            return f"the source of {e['path']} changed since the analysis"
    return None


def reclaim(search, manifests: list, log: Callable[[str], None] = lambda _m: None,
            excludes=()) -> ReclaimResult:
    """Delete the items of ``search.verified``. Never deletes anything else.
    The protection list is re-read here, right before deleting."""
    result = ReclaimResult()
    if search is None or search.verified is None:
        raise ReclaimError("only a JOINTLY VERIFIED plan can be reclaimed")
    guard = Guard(excludes)
    by_root = {str(Path(m["project"])): m for m in manifests}
    groups: dict = {}
    for item in search.verified.plan.items:
        groups.setdefault(item.root, []).append(item)
    for root_str, items in groups.items():
        m = by_root[root_str]
        root = Path(root_str).resolve()
        name = m.get("name", root.name)
        if guard.status(root) != "clear":
            result.refused.append((name, "the project is protected or excluded"))
            continue
        stale = manifest_mod.staleness(m)
        if stale:
            result.refused.append((name, "analysis is stale: " + "; ".join(stale)))
            continue
        by_path = {e["path"]: e for e in m["entries"]}
        entries = [by_path[i.path] for i in items]
        reason = _preflight(root, entries, guard)
        if reason:
            result.refused.append((name, reason))
            log(f"reclaim   {name}: refused, {reason}")
            continue
        records = read_log(root)
        for e in entries:
            path = _target(root, e["path"])
            force_remove(path)
            records.append({
                "path": e["path"], "kind": e["kind"], "bytes": e["bytes"], "signature": e["signature"],
                "method": e.get("method"), "recipe": e.get("recipe"), "recipe_cwd": e.get("recipe_cwd", ""),
                "recovery": e.get("recovery"), "rebuild_seconds": e.get("rebuild_seconds"),
                "identity": e.get("identity"),
                "deleted_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            _write_log(root, records)  # after every deletion, so the log is never behind
            result.deleted.append((name, e["path"], e["bytes"]))
            log(f"reclaim   deleted {path}")
    return result


def _matches(target: Path, rec: dict) -> bool:
    """Byte-identical to what was deleted; for items proven nondeterministic
    (a declared workflow recreates them with new bytes every run), it is enough
    that every file came back."""
    if signature(fingerprint(target, "full")) == rec["signature"]:
        return True
    return rec.get("identity") == "recreated" and bool(fingerprint(target, "meta").entries)


@dataclass
class RestoreResult:
    restored: list = field(default_factory=list)  # path
    failed: list = field(default_factory=list)  # (path, reason)
    also_changed: list = field(default_factory=list)  # other files a declared workflow rewrote


def restore(root: Path, paths: Optional[list] = None,
            log: Callable[[str], None] = lambda _m: None) -> RestoreResult:
    """Bring reclaimed items back in the project itself and check their bytes."""
    root = Path(root).resolve()
    # copies / archives / git first: recipes and workflows may need them
    records = sorted(read_log(root), key=lambda r: 0 if r.get("recovery") else 1 if r.get("method") == "recipe" else 2)
    wanted = {normalize_rel(p) for p in paths} if paths else None
    try:  # older log records lack the identity; the manifest has it
        known = {e["path"]: e.get("identity") for e in manifest_mod.load(root)["entries"]}
    except manifest_mod.ManifestError:
        known = {}
    for rec in records:
        rec.setdefault("identity", known.get(rec["path"]))
    result = RestoreResult()
    state = {"cfg": None}
    guard = Guard()
    before = snapshot(root, skip_top=(CONFIG_DIR,), guard=guard)

    def bring_back(rec: dict) -> None:
        if rec.get("recovery"):
            level0.restore(rec["recovery"], root, rec["path"])
            return
        log_dir = root / CONFIG_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if rec.get("method") == "recipe":
            cwd = root / rec["recipe_cwd"] if rec.get("recipe_cwd") else root
            command = render(rec["recipe"], root, project_python(root))
            ok, _seconds, tail = run_command(command, cwd, log_dir / "restore.log", 24 * 3600, _workflow_env())
            if not ok:
                raise ReclaimError(f"`{command}` failed: {tail.strip()[-200:]}")
            return
        if state["cfg"] is None:
            state["cfg"] = project_config(manifest_mod.load(root))
        res = run_workflow(state["cfg"], root, log_dir / "restore.log", _workflow_env())
        if not res.ok:
            raise ReclaimError(f"the workflow failed: {res.log_tail.strip()[-200:]}")

    pending = [r for r in records if wanted is None or r["path"] in wanted]
    keep = [r for r in records if not (wanted is None or r["path"] in wanted)]
    # two passes: a recipe may need another reclaimed item that comes back later
    for attempt in range(2):
        retry = []
        for rec in pending:
            target = _target(root, rec["path"])
            source = (rec.get("recovery") or {}).get("source")
            if guard.status(target) != "clear" or guard.contains_guarded(target) or (
                    source and guard.status(root / source) != "clear"):
                result.failed.append((rec["path"], "protected or excluded: not touched"))
                keep.append(rec)
                continue
            if os.path.lexists(target):
                if _matches(target, rec):
                    result.restored.append(rec["path"])  # already back (e.g. rebuilt with another item)
                else:
                    result.failed.append((rec["path"], "something else now exists at that path"))
                    keep.append(rec)
                continue
            log(f"restore   {rec['path']} ...")
            try:
                bring_back(rec)
            except Exception as exc:  # report, keep the record, continue with the others
                if attempt == 0:
                    retry.append(rec)
                else:
                    result.failed.append((rec["path"], str(exc)))
                    keep.append(rec)
                continue
            if os.path.lexists(target) and _matches(target, rec):
                result.restored.append(rec["path"])
            else:
                result.failed.append((rec["path"], "came back with different bytes than were deleted"))
                keep.append(rec)
        pending = retry
        if not pending:
            break
    _write_log(root, keep)
    if state["cfg"] is not None:  # a declared workflow ran in the project: say what else it touched
        restored = set(result.restored)
        for key in snapshot_diff(before, snapshot(root, skip_top=(CONFIG_DIR,), guard=guard)):
            rel = key.replace(os.sep, "/")
            if key in before and not any(rel == p or rel.startswith(p + "/") for p in restored):
                result.also_changed.append(rel)
    return result
