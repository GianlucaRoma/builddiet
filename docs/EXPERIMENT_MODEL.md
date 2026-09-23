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
* **`hash = "meta"`** compares sizes only (level 2 only); level 0 and level 1 always hash.
* **Full copies.** Each analysis, and each joint check that runs anything, copies the whole project.
* **Timing.** Costs come from a single measured run.
