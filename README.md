# BuildDiet

[Project website](https://gianlucaroma.github.io/builddiet/) · [Source on GitHub](https://github.com/GianlucaRoma/builddiet)

**Prove what's disposable. Keep what matters.**

**Beta:** start with `analyze` and a manual `reclaim` on a backed-up project. Automatic reclaim is opt-in.

BuildDiet is a command-line tool for developer workspaces that are running out of disk space. It looks for large outputs, checks whether each one can be restored, measures how long that takes, and offers a reviewed list of items to remove. It leaves anything it cannot prove alone.

For example, in the [BD-ZERO sample project](docs/BD-ZERO.md), it finds a copy of `features/` in `backups/`, rebuilds `models/` from a project script, and restores `release/` from a zip. It leaves `data/` alone because no tested recovery can bring its original bytes back. On macOS, the sample analysis proved 2.3 MB reclaimable; after reclaim and restore, all 70 project files matched their original SHA-256 hashes.

**Who is it for?** Developers, data scientists and teams with build artifacts, generated datasets or model outputs mixed with source data. It is useful when you do not know which large folders are safe to delete or how expensive they are to rebuild. If you already know you want to discard all ignored build files, `git clean` is simpler. For photos and ordinary duplicate files, use a duplicate finder.

**Scope:** BuildDiet can analyze several projects in a folder on one disk, but it is not a whole-disk cleaner. It discovers projects only a few folders deep and leaves unrelated files alone. Point it at a specific project or a folder containing projects.

BuildDiet discovers possible recovery methods from identical copies, archives, Git, scripts and project workflows. Commands are tried in a temporary project copy; their effects are checked before an item is offered. A discovered script is still a real program and can have effects outside that copy, so review it before approving a run.

From the source directory on macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/builddiet analyze ~/Projects   # inspect projects and proposed savings
.venv/bin/builddiet reclaim ~/Projects   # choose an option and confirm
.venv/bin/builddiet restore ~/Projects/app
```

On Windows, install into a virtual environment and use `Scripts\builddiet.exe`; the examples below use `D:/Projects` as a sample workspace path. `restore` targets the individual project that was reclaimed.

> *Non indovina cosa puoi cancellare. Lo verifica.*

## How it differs from other tools

| Tool | What it decides from | Where BuildDiet adds value |
|---|---|---|
| [`git clean`](https://git-scm.com/docs/git-clean) | Whether files are untracked or ignored | Tests whether the *current bytes* can be recovered, and measures the rebuild cost. |
| [Czkawka](https://github.com/qarmin/czkawka) and other duplicate finders | Matching copies of files | Also tests outputs recreated by a script or workflow, even without a duplicate. |
| [Nix garbage collection](https://releases.nixos.org/nix/nix-2.26.3/manual/command-ref/nix-store/gc.html) and workflow managers | References or outputs their system already knows | Examines an existing project without requiring its files to have been created by BuildDiet. |

The techniques individually have prior art. BuildDiet's useful combination is recovery testing on an existing workspace, measured rebuild time, and a checked plan for reclaiming space. See the [prior-art audit](docs/BD-PRIOR.md) for the narrower research claim.

## Three options, computed for you

Real output ([BD-ZERO](docs/BD-ZERO.md): a small data/ML-shaped git repository, given only the folder):

```
$ builddiet analyze D:/builddiet-bdzero/workspace
BUILDDIET - what D:uilddiet-bdzero\workspace can give back

  option                     frees   rebuild   items                             joint verification
  LEGGERO                   1.9 MB      0.2s   3: 1 recipe, 1 copy, 1 archive    PASS
  NORMALE  <- recommended   2.3 MB      1.5s   4: 2 recipes, 1 copy, 1 archive   PASS
  ESTREMO                   2.3 MB      1.5s   4: 2 recipes, 1 copy, 1 archive   PASS

LEGGERO:
  features     1.3 MB   0.0s  copy of backups/features-2026-09-01
  cache      640.0 KB   0.2s  run: python -c "import hashlib, os; ...   (found in an agent log)
  release     22.7 KB   0.0s  extract from dist/app-1.0.zip
NORMALE adds:
  models     327.7 KB   1.3s  run: python scripts/train.py

$ builddiet reclaim D:/builddiet-bdzero/workspace
Which option? [leggero / normale / estremo / nothing] normale
NORMALE: delete 4 items and free 2.3 MB (rebuild if needed: 1.5s)? Type 'reclaim' to confirm: reclaim
NORMALE: freed 2.3 MB (4 items).
```

(The interactive prompts of `reclaim` are shown as the code prints them; the gate itself ran the non-interactive form `--option normale --yes`, and the result line is real.)

On such a small project ESTREMO adds nothing, because nothing costs more than 5 minutes to rebuild. On the BD-REAL project, with the boundaries tightened to `--light-max 0.05s --normal-max 0.5s`, the three options differ: 1.5 MB, 2.7 MB and 8.0 MB.

Every item goes into an option according to the **measured cost of getting that item back**:

| Option | An item belongs here if | In practice |
|---|---|---|
| **LEGGERO** | it is restored by copying bytes that already exist (identical copy, archive, git), or its measured rebuild takes ≤ `--light-max` (1s) | practically free |
| **NORMALE** (recommended) | its measured rebuild takes ≤ `--normal-max` (5 min) | cheap derived data |
| **ESTREMO** | it is PROVEN, whatever its rebuild time | everything that can provably come back |

The options are nested (LEGGERO ⊆ NORMALE ⊆ ESTREMO). Each option shows:

* the space it frees;
* the measured rebuild time;
* how many items it contains, and of which kind (copies, archives, recipes, workflow);
* whether its **joint verification** passed: recipe and workflow items are removed together in a sandbox and must come back byte-for-byte; options containing only copy/archive/git proofs use checked source hashes and dependency checks without making a full sandbox copy;
* which protected or excluded paths were left out.

If an option fails its joint verification, it is retried without one item at a time, and the items left out are listed. Only items that come back byte-for-byte are ever offered.

`reclaim` asks which option you want, then asks you to type `reclaim`. For scripts, use `--option normale --yes`.

## Automatic mode: `watch`

```
builddiet watch D:/Projects
```

`watch` uses the same three options. It finds the projects under the folder (git repos, `package.json`, `pyproject.toml`, `Cargo.toml`, `.sln`, `.uproject`, ...), keeps their analyses fresh, and follows the disk:

| Free space | What `watch` does |
|---|---|
| ≥ 15% | keeps the analyses up to date; nothing else |
| < 15% | computes and jointly verifies the three options; picks the smallest that gets back to 20% free (LEGGERO or NORMALE) |
| < 10% | proposes it, through a desktop dialog or the terminal, and waits for your yes |
| < 5% | ESTREMO may be proposed too |

With `--auto` it reclaims the proposed option by itself, but never one above `--auto-max` (default **NORMALE**); above that it asks. Sandboxes go to the local drive with the most free space, so verification still works when the watched drive is full (`--sandbox-dir` or `$BUILDDIET_SANDBOX_DIR` to pin it).

**What gets deleted, and how to undo it.**

* Only the items of the option you chose (or `watch` proposed), jointly verified and byte-identical.
* Each item is re-hashed right before deletion. If it isn't exactly what was proven, or if it has become protected, nothing in that project is deleted.
* The record needed to restore an item is written *before* the item is deleted. If it cannot be written (e.g. the disk is full), nothing is deleted.
* `builddiet restore <project>` brings everything back and checks the hashes.

**Advanced / CI.** `builddiet plan DIR --free 20GB` and `builddiet reclaim DIR --free 20GB --yes` solve for a fixed amount instead: the cheapest jointly verified plan that frees at least that much. This is not the everyday path.

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

Before anything runs, BuildDiet shows you the recipes it found and asks once (`--yes` skips the question). It rejects command lines containing recognizable push, publish, install, delete, network and outside-path operations. It cannot determine everything a script will do internally:

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

All 12 verdicts were as expected, and identical across repeated runs. The original workspace stayed byte-identical. The options BuildDiet then offered for this workspace are shown in [docs/BD-ZERO.md](docs/BD-ZERO.md).

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
  * `reclaim` (after you choose an option and type `reclaim`) and `watch` (after you approve, or with `--auto` up to `--auto-max`) delete only jointly verified, byte-identical items, each re-hashed right before deletion.
  * The restore record is written before each deletion, and `restore` brings items back and checks them.
* **Running out of space is an explicit, safe failure.** If a sandbox copy or a run hits a full disk, BuildDiet stops with "out of disk space ... Nothing in your project was changed or deleted". A joint verification that cannot run makes its option unavailable, never "verified".
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
builddiet watch DIR --auto                       # reclaim the proposed option (up to NORMALE) by itself
builddiet watch DIR --auto --auto-max estremo     # allow ESTREMO too when it is the proposal
builddiet watch DIR --allow-recipes --agent-logs  # let unattended analyses use discovered recipes
builddiet market DIR                              # what every project can give back, cheapest first
builddiet reclaim DIR --option normale --yes      # non-interactive choice
builddiet plan DIR --details                      # every item of every option
builddiet plan DIR --light-max 5s --normal-max 30m  # move the option boundaries (always shown)
builddiet restore PROJECT [path ...]              # bring reclaimed items back, byte-checked
builddiet analyze DIR --sandbox-dir E:/scratch   # default: the local drive with the most free space
builddiet analyze DIR --min-size 100MB --depth 2  # candidate granularity
builddiet analyze DIR --agent-logs                # also learn from Codex / Claude Code sessions
builddiet analyze DIR --no-recipes                # hash proofs only; runs nothing
builddiet plan DIR --free 50GB                    # advanced / CI: a fixed amount instead of options
builddiet backup-plan DIR                         # which bytes are irreproducible
builddiet scan C:/Projects                        # summary across analyzed projects
builddiet init DIR                                # optional: declare build + test commands
```

## Limits

* **Scale.** Validated on small representative workspaces on Windows and macOS ([BD-ZERO](docs/BD-ZERO.md), [BD-REAL](docs/BD-REAL.md), [BD-WATCH](docs/BD-WATCH.md), [macOS validation](docs/MAC-VALIDATION.md)). A 201 MB synthetic duplicate workspace also passed on macOS. Multi-GB projects and trees with very many small files are not yet validated. Recipe and workflow experiments need a full copy of the project on a drive with enough space.
* **`watch` is a foreground loop.** To start it at login, register it yourself with your OS scheduler. BuildDiet does not install services.
* **The desktop dialog** uses Windows Forms (PowerShell), `osascript` on macOS, or `zenity` on Linux. Without a desktop, `watch` asks in the terminal, or just reports.
* **Recipes are only as good as the evidence.** If no script, doc, Makefile or log shows how something was made, BuildDiet says NOT PROVEN; it does not guess.
* **Recipes run for real** in the sandbox. External side effects (network, databases) are not contained, although the obvious categories are refused.
* **Rebuild time is a single measured run.** For recipes it is the full run time; for a declared workflow it is the time above a warm build.
* **Nondeterministic outputs** can only be PROVEN with a declared workflow (level 2).
* **Joint verification is bounded** (`--max-attempts`, default 5, per option and project). An option is verified as a whole: if you delete only part of it by hand, only the individual proofs cover that part.
* **Option boundaries are a choice.** 1s and 5 min are defaults, shown with every option list and changeable with `--light-max` / `--normal-max`. Within an option everything is deleted, even if you needed less.

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
docs/             SAFETY, EXPERIMENT_MODEL, THREAT_MODEL, TEST-ISOLATION, BD-ZERO, BD-REAL, BD-WATCH, BD-PRIOR
```

## Install

From a source checkout or archive:

```bash
python -m pip install .
```

After the repository and its `v0.1.0` tag are public at the URLs in `pyproject.toml`, users can also install with `python -m pip install git+https://github.com/GianlucaRoma/builddiet@v0.1.0`. For development use `python -m pip install -e .` (see [CONTRIBUTING.md](CONTRIBUTING.md)). The package is not on PyPI yet.

## Documentation

| document | what it covers |
|---|---|
| [EXPERIMENT_MODEL](docs/EXPERIMENT_MODEL.md) | verification levels, verdicts, joint verification, the three options, `watch` |
| [SAFETY](docs/SAFETY.md) | the invariants every deletion and restore obeys |
| [THREAT_MODEL](docs/THREAT_MODEL.md) | what can go wrong and what stops it |
| [BD-ZERO](docs/BD-ZERO.md) | release gate: zero configuration |
| [BD-REAL](docs/BD-REAL.md) | release gate: a real make-style workspace |
| [BD-WATCH](docs/BD-WATCH.md) | release gate: automatic mode on a real, nearly full disk |
| [BD-PRIOR](docs/BD-PRIOR.md) | prior-art audit: how existing tools compare |
| [TEST-ISOLATION](docs/TEST-ISOLATION.md) | how the tests are isolated, and the out-of-space investigation |
| [MAC-VALIDATION](docs/MAC-VALIDATION.md) | macOS tests, packaging and limits |
| [PUBLISHING](docs/PUBLISHING.md) | how to upload the changes and enable GitHub Pages |
| [CHANGELOG](CHANGELOG.md) | changes by version |

## Requirements

Python 3.9+ with no runtime dependencies. `git` is optional (used for the IN GIT proofs). Tested on Windows 10 with Python 3.10 and on macOS with Python 3.9, 3.12 and 3.14.

## License

MIT
