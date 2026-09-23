# BuildDiet

**Prove what's disposable. Keep what matters.**

Point BuildDiet at your projects folder. In sandbox copies, it works out which files and directories can be brought back byte-for-byte, how, and what that costs. When the disk runs low, it frees the cheapest jointly verified set, and `restore` brings anything back.

```bash
pip install -e .
builddiet watch D:/Projects          # and that's it (asks before deleting anything)
```

> *Non indovina cosa puoi cancellare. Lo verifica.*

## Automatic mode: `watch`

```
builddiet watch D:/Projects
```

`watch` finds the projects under the folder itself (git repos, `package.json`, `pyproject.toml`, `Cargo.toml`, `.sln`, `.uproject`, ...), keeps a "market" of what each one can give back, and follows the disk:

| Free space | What `watch` does |
|---|---|
| ≥ 15% | keeps the analyses up to date; nothing else |
| < 15% | computes and **jointly verifies** the cheapest plan that gets back to 20% free |
| < 10% | shows it and asks, with a desktop dialog or the terminal: *Disk space is low. BuildDiet can safely reclaim 47.3 GB. Expected worst-case rebuild cost: 2m18s. Joint verification: PASS. Reclaim?* |
| < 5% | the same, with a larger rebuild budget |

With `--auto` it reclaims by itself, within the limits: `--keep-free 20`, `--max-penalty 5m`, and `--aggressive-max-penalty 1h` when the disk is critical. Sandboxes go to the local drive with the most free space, so verification still works when the watched drive is full. You can pin that location with `--sandbox-dir` or `$BUILDDIET_SANDBOX_DIR`.

```
$ builddiet market D:/Projects
  item                              size   rebuild   value    how to get it back
  corpus/workspace/derived/shards 1.6 MB      0.0s   LOW      your workflow
  ml/workspace/features           1.3 MB      0.0s   LOW      copy of backups/features-2026-09-01
  corpus/.../cache/ngrams.json    5.4 MB      0.6s   MEDIUM   your workflow
  ml/workspace/models           327.7 KB      1.2s   HIGH     run: python scripts/train.py
```

**What gets deleted, and how to undo it.**
* **Only jointly verified, byte-identical items:** only items of a JOINTLY VERIFIED plan are deleted, and only those that come back byte-for-byte (outputs with timestamps need `--allow-nondeterministic` in `reclaim`).
* **Re-hashed right before deletion:** each item is hashed again just before it is deleted. If it isn't exactly what was proven, nothing in that project is deleted.
* **Logged:** every deletion goes to `.builddiet/reclaimed.json`.
* **Restorable:** `builddiet restore <project>` brings everything back and checks the hashes.

Manual equivalents: `builddiet plan DIR --free 20GB` shows the plan, and `builddiet reclaim DIR --free 20GB` deletes it after you type `reclaim`.

## Protected paths

```bash
builddiet protect D:/Projects/important-project   # persistent, global
builddiet protected                               # list
builddiet unprotect D:/Projects/important-project
builddiet watch D:/Projects --exclude D:/Projects/tmp-experiment   # this run only
```

A protected path is never read, sized, copied into a sandbox, analyzed, planned, deleted or restored into, by any command, including `watch --auto`. The check happens before anything inside the path is looked at.

* **Precedence.** Protection overrides config `include`, `--include-git`, earlier analyses and plans that were already verified: `reclaim` re-reads the list right before deleting.
* **No bypass by spelling.** Paths are compared after resolving symlinks, junctions, `..` and letter case (Windows).
* **No bypass through links.** A link that leads into a protected area is never followed.
* **Parent/child both count.** A folder that *contains* a protected path is never deleted as a whole.
* **Fail closed.** If a path cannot be resolved, it is treated as protected. If the protection list cannot be read, BuildDiet refuses to run.

## Why

Workspaces fill up with build trees, datasets, model files, exports, caches and copies of all of those. Deleting them by hand is scary for two reasons: you don't know what is still needed, and you don't know how long it will take to get it back.

Disk cleaners decide from a **catalog of names** (`node_modules`, `target`). Duplicate finders decide from hashes but know nothing about builds. Cost-aware systems such as Nectar and workflow managers decide from **lineage they recorded themselves**. None of them helps with `experiments/arena/` or `layout_v2.tmp`: directories that are specific to your project and that no tool recorded when they were created.

## How it proves things

You don't tell BuildDiet which commands you use. It looks for evidence itself and then checks it.

| Level | What BuildDiet looks for | What counts as proof |
|---|---|---|
| **0: bytes exist elsewhere** (runs nothing) | an identical copy in the project, a `.zip`/`.tar*` holding the same files, or git | SHA-256 of every file matches (archive members are decompressed; git content is rendered through `git cat-file --filters`) |
| **1: a recipe recreates it** | scripts that mention the name, `Makefile` targets, lockfiles, commands in `README`/`AGENTS.md`/CI, and optionally Codex / Claude Code session logs | in a sandbox copy: remove it, run the recipe, and it comes back **byte-for-byte**, and **no other existing file changes** |
| **2: your declared workflow** (optional, `builddiet init`) | your build and test commands | the build recreates it and the tests pass |

Level 1 needs no test command, because the bar is higher than "tests pass": after regeneration the project is byte-for-byte what it was.

Before anything runs, BuildDiet shows you the recipes it found and asks once (`--yes` skips the question). Commands that push, publish, install, delete, touch the network, or use paths outside the project are **never** run:

```
Found 5 possible recipes. They run ONLY inside a sandbox copy;
a file counts as proven only if a recipe recreates it byte-for-byte
without changing anything else.
  python -c "import hashlib, os; os.makedirs('cache'...   <- agent log: ran right before it was written
  python scripts/train.py                                 <- script scripts/train.py mentions it
  ...
  never run: python scripts/publish.py && git push        <- changes git state
Try them in the sandbox? [Y/n]
```

## Real result (BD-ZERO gate, no configuration)

The workspace is a small data/ML-shaped git repository. BuildDiet was given only the folder ([docs/BD-ZERO.md](docs/BD-ZERO.md)):

```
SPACE YOU CAN PROVABLY RECLAIM
  path            size   rebuild   how to get it back
  features/     1.3 MB      0.0s   copy of backups/features-2026-09-01
  release/     22.7 KB      0.0s   extract from dist/app-1.0.zip
  cache/      640.0 KB      0.2s   run: python -c "import hashlib, os; ... (from an agent log)
  models/     327.7 KB      1.3s   run: python scripts/train.py

NOT PROVEN (keep):  data/ (canonical), notes/, backups/ and dist/ (sources of the above)
STALE (review):     exports/  (the recipe produces different bytes than the copy on disk)
IN GIT:             src/, scripts/  (restorable with git checkout; not planned by default)
NOT TESTED:         reports/  (different bytes on every run: timestamped)
```

All 12 verdicts were as expected, and identical across 3 repeated runs. The original workspace stayed byte-identical.

```
$ builddiet plan --free 2MB
JOINTLY VERIFIED PLAN
  cache/      640.0 KB   0.2s   run: python -c "import hashlib, os; os.makedi...
  features/     1.3 MB   0.0s   copy of backups/features-2026-09-01
  models/     327.7 KB   1.3s   run: python scripts/train.py
  Frees 2.2 MB. Removed together, every item came back byte-for-byte.
  Measured joint rebuild 1.6s; individual estimates sum to 1.5s.
```

`plan` solves a min-cost covering knapsack over the proven items. It then **removes the whole candidate plan at once** in a fresh sandbox and restores everything (copies, archives, recipes). A plan is only called verified if every item comes back byte-for-byte. If it doesn't, the next-cheapest plan is tried; if none passes, `plan` says so.

## Verdicts

| Verdict | Meaning | Planned? |
|---|---|---|
| **PROVEN** | Comes back byte-for-byte from a copy, an archive, a recipe or your workflow | yes |
| **STALE** | A recipe recreates it deterministically but *differently* from your copy: a stale or corrupt output, or hand edits | no, review it |
| **IN GIT** | Tracked and clean; `git checkout` restores it (usually source code) | only with `--include-git` |
| **NOT PROVEN** | Nothing recreates it: canonical data, notes, sources of other proofs | no, keep it |
| **INCONCLUSIVE** | Recreated, but with different bytes every run (timestamps); declare a workflow to prove it | no |
| **REQUIRED** | (workflow mode) without it the build or tests fail | no |
| **KNOWN** / **UNKNOWN** | Catalog match, too small, excluded, or metadata; not tested | no |

## Safety

* **Deletion is narrow, checked and logged.**
  * `analyze` and `plan` never delete anything of yours.
  * `reclaim` (after you type `reclaim`) and `watch` (after you approve, or with `--auto` within your limits) delete only jointly verified, byte-identical items, each re-hashed right before deletion.
  * Every deletion is logged, and `restore` brings it back and checks it.
* **Sandbox.** The project is copied, and every destructive step is path-checked to stay inside the copy. Absolute paths to the project in discovered commands are rewritten to point at the sandbox.
* **You approve the commands.** Discovered recipes are listed, and nothing runs until you say yes. Dangerous categories are refused outright.
* **Side effects disqualify.** A recipe that changes any other existing file proves nothing. From the outside, refreshing a stale output and overwriting your data look the same. The only exception is a short, explicit list of volatile files (`*.log`, `logs/`, `__pycache__/`, `*.pyc`, tool caches).
* **Protected paths win over everything** (see above). Symlinks and junctions are never followed by any walk, copy or deletion.
* **Agent logs are opt-in and local.** `--agent-logs` reads `~/.codex/sessions` and `~/.claude/projects` on your machine, keeps only commands whose working directory is inside the analyzed project, and sends nothing anywhere.
* **The original is watched.** If anything in it changes during an analysis, the report says so.
* **Restoring through a declared workflow runs your build** in the project. If the build also rewrites other files (e.g. refreshes a stale output), `restore` lists them. Level-0 restores (copy/extract/git) and level-1 recipes are proven to touch nothing else.

Details: [docs/SAFETY.md](docs/SAFETY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Options you may need

```bash
builddiet watch DIR --auto --max-penalty 10m     # reclaim by itself, within limits
builddiet watch DIR --allow-recipes --agent-logs  # let unattended analyses use discovered recipes
builddiet market DIR                              # what every project can give back, cheapest first
builddiet reclaim DIR --free 20GB                 # delete a verified plan now (asks first)
builddiet restore PROJECT [path ...]              # bring reclaimed items back, byte-checked
builddiet analyze DIR --sandbox-dir E:/scratch   # default: the local drive with the most free space
builddiet analyze DIR --min-size 100MB --depth 2  # candidate granularity
builddiet analyze DIR --agent-logs                # also learn from Codex / Claude Code sessions
builddiet analyze DIR --no-recipes                # hash proofs only; runs nothing
builddiet plan DIR --free 50GB --include-git
builddiet backup-plan DIR                         # which bytes are irreproducible
builddiet scan C:/Projects                        # summary across analyzed projects
builddiet init DIR                                # optional: declare build + test commands
```

## Limits

* **Scale.** Validated on unit tests and small real workspaces ([BD-ZERO](docs/BD-ZERO.md), [BD-REAL](docs/BD-REAL.md), [BD-WATCH](docs/BD-WATCH.md)), all on Windows. BD-WATCH ran on a real, nearly full drive. It has not been validated on large workspaces yet, and every experiment needs a full copy of the project (on the drive with the most space).
* **`watch` is a foreground loop.** To start it at login, register it yourself with your OS scheduler. BuildDiet does not install services.
* **The desktop dialog** uses Windows Forms (PowerShell), `osascript` on macOS, or `zenity` on Linux. Without a desktop, `watch` asks in the terminal, or just reports.
* **Recipes are only as good as the evidence.** If no script, doc, Makefile or log shows how something was made, BuildDiet says NOT PROVEN; it does not guess.
* **Recipes run for real** in the sandbox. External side effects (network, databases) are not contained, although the obvious categories are refused.
* **Rebuild time is a single measured run.** For recipes it is the full run time; for a declared workflow it is the time above a warm build.
* **Nondeterministic outputs** can only be PROVEN with a declared workflow (level 2).
* **Joint verification is bounded** (`--max-attempts`, default 5). A verified plan is verified as a whole: if you delete only part of it, only the individual proofs cover that part.

## Related work

Every ingredient exists somewhere. Duplicate finders (rmlint, czkawka, fclones) find identical files and directories. Reproducible-build tools rebuild and compare hashes. Nectar (OSDI 2010) and Hitachi patents reclaim derived data by recomputation cost, using lineage they recorded. Sciunit and ReproZip recover provenance by tracing execution. We are not aware of prior work that establishes regenerability of existing, unmanaged files by removal experiments with discovered recipes and plans the cheapest jointly verified reclaim. The audit and its limits are in [docs/BD-PRIOR.md](docs/BD-PRIOR.md).

## Layout

```
builddiet/
  level0.py       hash proofs: duplicates, archives, git (+ restore)
  recipes.py      recipe discovery and the safety filter
  agentlogs.py    opt-in reader for Codex / Claude Code session logs
  autoprove.py    level 1: byte-identical regeneration with no side effects
  experiment.py   orchestration, level 2 workflow experiments, joint verification
  planner.py      exact min-cost covering knapsack + jointly verified search
  scanner.py      candidates (directories and files), non-overlapping
  sandbox.py      the only place anything is removed
  verifier.py, manifest.py, report.py, cli.py, adapters/
tests/            unit + end-to-end tests (python -m unittest)
benchmarks/       bd_zero/ and bd_real/ release gates, demo generator
docs/             SAFETY, EXPERIMENT_MODEL, THREAT_MODEL, BD-ZERO, BD-REAL, BD-PRIOR
```

## Requirements

Python 3.9+ with no dependencies. `git` is optional (used for the IN GIT proofs). Tested on Windows 10 with Python 3.10.

## License

MIT
