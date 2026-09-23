# Experiment model

## Inputs

* **regenerate**: a command that (re)creates derived data. Optional if verify does the regeneration itself (for example `make test`).
* **verify**: a command whose exit code defines "the project still works". Optional, but strongly recommended.
* **candidates**: non-overlapping directories chosen by `scanner.scan`. By default these are the top-level directories. `include` splits a parent so that a subdirectory can be tested on its own, `exclude` removes directories from consideration, and `min_size` skips small ones.

## Procedure

```
sandbox <- copy(project)                 (without .builddiet/)
cold  <- run(regenerate; verify)         must pass
warm  <- run(regenerate; verify)         must pass: the workflow is repeatable
for each candidate C:
    before <- fingerprint(C)             in the sandbox, after the warm run
    move C aside
    r      <- run(regenerate; verify)    timed
    after  <- fingerprint(C)
    restore C (discard whatever was recreated)
    verdict(r, before, after)
```

Restoring after every experiment makes each experiment start from the same state: the one after the warm baseline.

## Verdict

| run result | identity(before, after) | verdict |
|---|---|---|
| timeout | - | INCONCLUSIVE |
| a step failed | - | REQUIRED |
| pass | identical or recreated | PROVEN |
| pass | partial or absent | NOT REGENERATED |

The identity levels are:

* `identical`: every file came back with the same SHA-256 (or the same size, with `hash = "meta"`).
* `recreated`: every file came back, but some with different bytes (timestamps, nondeterministic builds). The report shows this, and `plan --strict` excludes these.
* `partial`: some files never came back.
* `absent`: nothing came back.

## Cost

```
rebuild_seconds  = run_seconds(without C) - warm_seconds
expected_seconds = reuse_probability(C) x rebuild_seconds
```

The warm baseline is subtracted, so a long test suite does not inflate every directory's cost. `reuse_probability` defaults to 1 and can be declared per path in `[reuse]`.

## Planner

Given a target `T` in bytes, the planner looks for a subset `S` of PROVEN items that minimises the sum of `expected_seconds(i)` subject to the sum of `bytes(i)` being at least `T`.

This is a min-cost covering knapsack. `planner.solve` solves it with dynamic programming over sizes quantised to `T/4000`. Sizes are rounded down, so the result is guaranteed to reach `T` and is optimal up to that resolution. The tests check it against brute force. The report also shows what a naive biggest-first choice would have cost.

## Known limitations

* **Incremental workflows hide inputs.** If the build skips work because its outputs already exist, removing an *input* won't fail anything. The input is then reported as NOT REGENERATED instead of REQUIRED. Both are "keep", so this is safe. To separate them, make the regenerate command a clean build.
* **Interactions between directories are not explored.** Each candidate is removed on its own. Removing two PROVEN directories together is assumed to cost roughly the sum of their individual costs. For derived-from-derived chains this can underestimate the cost.
* **Full copy.** The sandbox is a full copy, so you need free space on some drive about equal to the project size (`--sandbox-dir`).
* **Timing noise.** Rebuild times come from single runs.
