# BuildDiet

**Prove what's disposable. Keep what matters.**

Point BuildDiet at a project folder. It works out, by experiment and in a sandbox copy, which files and directories can be brought back byte-for-byte, what that costs, and how. Then it gives you the cheapest set to delete to free the space you asked for. It is report-only: it never deletes anything of yours.

```bash
pip install -e .
builddiet analyze path/to/project        # no configuration needed
builddiet plan path/to/project --free 20GB
```

> *Non indovina cosa puoi cancellare. Lo verifica.*

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

* **Report-only.** Nothing outside BuildDiet's own sandboxes is ever deleted.
* **Sandbox.** The project is copied, and every destructive step is path-checked to stay inside the copy. Absolute paths to the project in discovered commands are rewritten to point at the sandbox.
* **You approve the commands.** Discovered recipes are listed, and nothing runs until you say yes. Dangerous categories are refused outright.
* **Side effects disqualify.** A recipe that changes any other existing file proves nothing. From the outside, refreshing a stale output and overwriting your data look the same. The only exception is a short, explicit list of volatile files (`*.log`, `logs/`, `__pycache__/`, `*.pyc`, tool caches).
* **Agent logs are opt-in and local.** `--agent-logs` reads `~/.codex/sessions` and `~/.claude/projects` on your machine, keeps only commands whose working directory is inside the analyzed project, and sends nothing anywhere.
* **The original is watched.** If anything in it changes during an analysis, the report says so.

Details: [docs/SAFETY.md](docs/SAFETY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Options you may need

```bash
builddiet analyze DIR --sandbox-dir E:/scratch   # the sandbox is a full copy: put it on a drive with space
builddiet analyze DIR --min-size 100MB --depth 2  # candidate granularity
builddiet analyze DIR --agent-logs                # also learn from Codex / Claude Code sessions
builddiet analyze DIR --no-recipes                # hash proofs only; runs nothing
builddiet plan DIR --free 50GB --include-git
builddiet backup-plan DIR                         # which bytes are irreproducible
builddiet scan C:/Projects                        # summary across analyzed projects
builddiet init DIR                                # optional: declare build + test commands
```

## Limits

* **Scale.** Validated on unit tests and two small real workspaces ([BD-ZERO](docs/BD-ZERO.md), [BD-REAL](docs/BD-REAL.md)), both on Windows. It has not been validated on large workspaces yet, and every experiment needs a full copy of the project.
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
