# Experiment model

BuildDiet assigns every candidate (a directory or a file of at least `min_size`, non-overlapping; see `scanner.py`) one verdict, using up to three levels of evidence. Each level only looks at what the previous levels left unproven.

## Level 0: the bytes exist elsewhere (runs nothing)

| Method | Check | Restore |
|---|---|---|
| duplicate | Another file or directory in the project holds exactly the same files. Every file's SHA-256 must match, and so must the directory's file list. | copy |
| archive | A `.zip` / `.tar*` in the project (outside the candidate) contains exactly these files, under some prefix. The members are **decompressed** and hashed. | extract |
| git | Every file is tracked and clean, and `git cat-file --filters HEAD:<path>` produces the same SHA-256. | `git checkout HEAD -- <path>` |

Two copies never point at each other: the first in path order points at the second, which is kept. The source of a recovery is marked "keep".

Git-recoverable candidates get the verdict **IN GIT**. They are not planned unless you pass `--include-git`, because they are usually source code.

## Level 1: a discovered recipe recreates it (no configuration)

**Discovery** (`recipes.py`). Recipes are hypotheses with a strength:

| Source | Strength |
|---|---|
| agent log: a command whose run started just before the candidate was last written (opt-in) | 5 |
| agent log: a command that mentions the candidate's name | 4 |
| `Makefile` target named like the candidate; lockfile → `npm ci` for `node_modules/`; `Cargo.toml` → `cargo build` for `target/` | 4 |
| a project script (`.py .sh .ps1 .bat .cmd .js .mjs`) whose text mentions the name | 3 |
| a build command in `README`/`AGENTS.md`/`CLAUDE.md`/CI that mentions the name | 3 |
| generic build commands (default `make`, `npm run build`, other doc/CI commands) | 1-2 |

Commands are normalised: `python ...` runs with the project's own interpreter (`.venv`) if there is one, and absolute paths to the project become `{project}`, which is rewritten to the sandbox copy at run time. Commands that are only read-only (`ls`, `git status`, ...) are dropped. Commands in dangerous categories are **refused and never run**:

* git state changes (`push`, `commit`, `reset`, `checkout`, ...)
* deleting files
* network transfer
* publishing
* package installs (they change the environment outside the project)
* containers and cloud tools
* system administration
* absolute paths outside the project

The user approves the remaining list once.

**Probe.** Each recipe runs twice on the untouched sandbox copy. It must exit 0, and must not change any existing file outside the candidates. Otherwise it is never used.

**Trial.** For candidate `C` and recipe `R`:

```
move C aside (sandbox), run R
-> C must come back with every file, and
-> no other existing file may change content or disappear
   (volatile files are ignored: *.log, logs/, __pycache__/, *.pyc, tool caches)
restore the sandbox to the original state
```

| Result of the trial | Verdict |
|---|---|
| C byte-identical to the user's copy | **PROVEN** (`run: R`) |
| C recreated with different bytes, identical on a second run | **STALE** |
| C recreated with different bytes, different on a second run | **INCONCLUSIVE** (nondeterministic) |
| R fails, C does not come back, or R changes another file | try the next recipe (max `--max-tries`, default 3), else **NOT PROVEN** |

**Why "no other file may change".** A recipe that also rewrites another file might be refreshing a stale output, or it might be overwriting canonical data. From outside the two are indistinguishable (a script that truncates `data/raw.csv` "recreates" it too). So neither is accepted. The candidate that the recipe rewrites is reported on its own trial, usually as STALE. `tests/test_zeroconfig.py` covers both cases.

**Cost.** The rebuild cost of a recipe is its **full run time**. Nobody runs a discovered recipe anyway, so there is nothing to subtract.

## Level 2: your declared workflow (optional)

With `builddiet init`, you declare `regenerate` and `verify` commands. Then:

```
baseline: regenerate + verify pass twice on the untouched copy
per candidate: remove it, regenerate + verify
  fails                                  -> REQUIRED
  passes, identical to the user's copy   -> PROVEN
  passes, differs, same twice            -> STALE
  passes, differs, different twice       -> PROVEN (recreated, nondeterministic)
  passes, not recreated                  -> NOT REGENERATED (keep)
cost = run time - warm baseline time (you run this workflow anyway)
```

The candidates' fingerprints are taken **before** the baseline, because the baseline may refresh a stale file and the claim is about the user's bytes. The first V1 got this wrong; BD-REAL found it.

## Planner and joint verification

The planner looks for a subset `S` of PROVEN items that minimises the sum of `p(reuse) x rebuild_seconds` subject to the sum of `bytes` being at least the target. It solves this with dynamic programming over sizes quantised to `target/4000`, rounded down so the target is always met.

The solution is only a **CANDIDATE PLAN**, because every proof above is leave-one-out. For each project in it, the **joint check**:

1. **Level-0 items:** the recovery source must not be in the plan and must be unchanged since the analysis. If all items are level 0, that is the whole check.
2. **Otherwise, a fresh sandbox:** (baseline if there is a workflow), remove **all** plan items, restore level-0 items from their sources, run every recipe (two passes), then run the workflow (if any).
3. **Every item must be byte-identical** to the user's copy. Workflow items individually proven nondeterministic only need to be fully recreated.
4. **No other existing file may change**, and the original must be untouched.

If the check fails, each failing item is excluded in turn and the knapsack is solved again. Alternatives are tried cheapest first, up to `--max-attempts`. Exit codes of `plan`: 0 jointly verified, 3 not enough proven space, 4 no jointly verified plan.

## Known limitations

* **Recipes need evidence.** Nothing is guessed: no evidence means NOT PROVEN.
* **Recipes run for real in the sandbox.** Only the listed dangerous categories are refused. External effects of the rest (for example a script that calls an API) are not contained.
* **Interactions between recipes.** A recipe that needs another candidate's output fails when that output is removed. In a plan, recipes are run in two passes, and the joint check decides.
* **Partial plans.** A verified plan is verified as a whole: deleting only part of it is covered by the individual proofs only.
* All proof levels compare SHA-256 content hashes. The former `hash = "meta"` setting compared only paths and sizes, so it can no longer be used for a byte-identical proof.
* **Full copies.** Each analysis, and each joint check that runs anything, copies the whole project.
* **Timing.** Costs come from a single measured run.

## The three options (LEGGERO / NORMALE / ESTREMO)

This is the everyday interface: `analyze <folder>`, `plan <folder>` and `reclaim <folder>` present these options, and `watch` proposes one of them. Nobody types an amount.

**Eligible items:** PROVEN, byte-identical (`identity == identical`), still on disk, and neither protected nor excluded. Workflow outputs that differ on every run are never offered.

**Placement** of an eligible item `i` with measured rebuild time `t(i)` (`options.TierRule`):

```
LEGGERO  if i is restored by copying existing bytes (duplicate, archive, git)  or  t(i) <= light_max
NORMALE  if t(i) <= normal_max
ESTREMO  otherwise
defaults: light_max = 1s, normal_max = 5m  (--light-max, --normal-max; always printed with the options)
```

**Options** are cumulative: `LEGGERO = {i : tier(i) = LEGGERO}`, `NORMALE = LEGGERO ∪ {tier = NORMALE}`, `ESTREMO = everything eligible`. For each option and each project:

1. Verify the whole set jointly.
2. If that fails, retry without one item at a time, smallest first, up to `--max-attempts` sets.
3. The first set that passes is the option for that project. The items left out are listed with the reason.
4. A fatal failure (for example out of disk space) leaves the project out of that option.

`frees` is the sum of the bytes. `rebuild` is the sum of the individual measured rebuild times (the joint check also measures the actual joint time). NORMALE is the recommended option; if it is empty and LEGGERO is not, LEGGERO is.

## Automatic mode (`watch`) and deletion (`reclaim`)

`watch DIR` runs cycles (default every 10 minutes):

1. **Discover** the projects under `DIR`: folders with `.git`, `.builddiet`, `package.json`, `pyproject.toml`, `Cargo.toml`, `CMakeLists.txt`, `Makefile`, `go.mod`, `*.sln`, `*.uproject`, ... Nested projects are not listed twice. Protected and excluded folders are neither listed nor entered.
2. **Refresh** analyses that are missing or stale. Sandboxes go to the local drive with the most free space. Discovered recipes run unattended only with `--allow-recipes`.
3. **Measure free space** and pick a level:

| Level | Condition (defaults) | Options that may be proposed |
|---|---|---|
| ok | ≥ 15% free | - |
| prepare | < 15% | LEGGERO, NORMALE (computed and reported, never deleted) |
| reclaim | < 10% | LEGGERO, NORMALE |
| aggressive | < 5% | LEGGERO, NORMALE, ESTREMO |

4. **Propose** the smallest allowed option that frees what is needed to get back to `--keep-free` (20%). If none is enough, propose the largest allowed one.
5. **Act.**
   * `reclaim` and `aggressive` ask, via a desktop dialog or the terminal. An unanswered question is a "no".
   * With `--auto`, the proposal is reclaimed without asking only if it is not above `--auto-max` (default NORMALE); otherwise `watch` asks.

`reclaim`, per project:

* refuses if the analysis is stale, or if the project, an item or a recovery source is protected or excluded;
* re-hashes every item against its proven signature;
* **writes the restore record before deleting**, and deletes nothing if the record cannot be written.

`restore` brings items back in dependency order (copies, archives and git first, then recipes, then the workflow), in two passes. It then checks the bytes and reports any other file a declared workflow rewrote.

## Running out of disk space

* **Copying a project into a sandbox:** `ENOSPC` / `ERROR_DISK_FULL` becomes `OutOfSpace`, with the message "out of disk space in X while ... Nothing in your project was changed or deleted".
* **A failed run on a drive with less than 16 MB free:** also `OutOfSpace`, because the failure says nothing about the project and must not become a verdict.
* **`analyze`:** fails explicitly. When analyzing a folder of projects, that project is skipped and listed.
* **Joint verification:** returns a fatal, explicit result, so the option is not verified. It is never reported as verified.
* **Before copying:** a free-space check (project size × 1.1 + 32 MB) refuses early (`--sandbox-dir` to use another drive).
