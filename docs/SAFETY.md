# Safety

BuildDiet exists because people are afraid to delete the wrong thing. It must never be the tool that deletes the wrong thing.

## Invariants

1. **The original project is read-only to analysis.** `analyze`, `plan` and joint verification write only `.builddiet/` (config and manifest). Only `reclaim`/`watch` delete, and only under invariant 13. If anything else in the original changes during an analysis or a joint check, the report warns, and a joint check fails.
2. **Destruction happens only in the sandbox.** `Sandbox.target()` normalises every path, rejects `..`, absolute paths and the sandbox root itself, and checks that the result resolves inside the sandbox copy. Every removal and every restore goes through it.
3. **The sandbox is never inside the project.** This is checked when the sandbox is created and again before it is removed.
4. **Hashes, not names.** Level-0 proofs compare SHA-256 of real bytes: archive members are decompressed, and git content is rendered through the checkout filters.
5. **"Not needed" is not "disposable".** Something the project runs fine without, but that nothing recreates, is NOT PROVEN / NOT REGENERATED and is never planned.
6. **"Regenerable" is not "your bytes are regenerable".** Every comparison is against the user's original bytes. A deterministic regeneration that differs from them is STALE and is never planned.
7. **Recipes must not touch anything else.** A discovered recipe proves nothing if it changes any other existing file. The only exception is volatile files (`*.log`, `logs/`, `__pycache__/`, `*.pyc`, tool caches).
8. **Dangerous commands are never run.** Discovered commands that change git state, delete files, move data over the network, publish, install packages, drive containers or cloud tools, administer the system, or use absolute paths outside the project are refused, whatever their source.
9. **The user approves discovered commands.** They are listed, with their evidence, before anything runs. In non-interactive use nothing runs without `--yes`.
10. **Agent logs are opt-in and local.** Nothing reads `~/.codex` or `~/.claude` unless you pass `--agent-logs` (or `--agent-logs-dir`). Only commands whose working directory is inside the analyzed project are kept. Nothing is sent anywhere.
11. **Plans are verified jointly.** A set is presented as a JOINTLY VERIFIED PLAN only after all of its items have been removed together in a sandbox and restored byte-for-byte. `--no-verify` output is labelled NOT JOINTLY VERIFIED.
12. **Proofs expire.** The manifest records the platform, git HEAD, a hash of the top-level project files, and the config. `plan` refuses stale analyses unless you pass `--allow-stale`. Level-0 sources are re-checked when a plan is verified.
13. **Deletion is narrow, checked and reversible.**
    * `analyze`, `plan`, `market` and `report` never delete anything.
    * `reclaim` and `watch` delete only the items of a JOINTLY VERIFIED option (or `--free` plan), and only those that come back byte-for-byte.
    * Just before deletion each item is re-hashed and must match the signature recorded when it was proven. Its recovery source must be unchanged, and the analysis must not be stale. If any check fails, nothing in that project is deleted.
    * The record needed to restore an item is written to `.builddiet/reclaimed.json` *before* the item is deleted. If it cannot be written, nothing more is deleted.
    * `restore` brings items back and checks the hashes.
14. **Nobody is surprised.**
    * `reclaim` asks you to type `reclaim`.
    * `watch` asks through a dialog or the terminal, or with `--auto` acts only within `--keep-free` and `--max-penalty`.
    * An unanswered dialog is a "no".
15. **Protected paths have absolute precedence.**
    * `builddiet protect` stores canonical paths globally (`~/.builddiet/protected.json`). Canonical means resolved through symlinks, junctions, `..` and 8.3 names, and case-normalised on Windows.
    * Every command checks a path before reading anything inside it: `analyze`, `plan`, `market`, `scan`, `watch` (re-read every cycle), `reclaim` (re-read right before deleting) and `restore`.
    * A folder that contains a protected path is split during analysis and never deleted as a whole.
    * Unresolvable paths count as protected. An unreadable protection list stops BuildDiet.
    * `--exclude` adds temporary exclusions and can never remove a protection.
16. **Links are never followed.** Every walk, copy and deletion goes through `builddiet/fs.py`, which treats symlinks, junctions and other reparse points as opaque entries. On Windows with Python 3.10, `os.walk` and `shutil.copytree` do follow junctions (verified), which would read and copy whatever a junction points to.
17. **Running out of space fails explicitly and safely.** A full disk during a sandbox copy or a run raises "out of disk space ... Nothing in your project was changed or deleted", instead of a generic error or a wrong verdict. A joint verification that cannot run leaves its option unverified.

## What BuildDiet can't protect you from

* **Side effects outside the project.** Recipes and workflow commands run for real in the sandbox directory. BuildDiet refuses the obvious dangerous categories and detects writes to the original project. It cannot see a script that, say, calls a web API or writes to a database. Read the recipe list before you approve it.
* **A weak verify command** (level 2). There, a PROVEN verdict means "recreated, and your verify command passed".
* **Hidden inputs.** If a recipe downloads something today, the proof assumes it will still be downloadable tomorrow.
* **Deleting part of a plan.** A verified plan is verified as a whole.
* **A declared workflow restore runs your build** in the project, which may also rewrite other files (e.g. refresh a stale output). `restore` lists them. Level-0 restores and level-1 recipes are proven not to.
* **Sandboxes need space somewhere.** They go to the local drive with the most free space. If no drive can hold a copy of a project, that project is simply not analyzed.
