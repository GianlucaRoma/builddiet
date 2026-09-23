# Experiment model

## Inputs

* **regenerate**: a command that (re)creates derived data. Optional if verify does the regeneration itself (for example `make test`).
* **verify**: a command whose exit code defines "the project still works". Optional, but strongly recommended.
* **candidates**: non-overlapping directories *and files* chosen by `scanner.scan`.
  * At depth `depth`, every directory is a candidate, and so is every file of at least `min_size`.
  * `include` names extra directories or files to test on their own. Their parent directories are split so that nothing is counted twice.
  * `exclude` removes paths from consideration.
  * Remaining small files are grouped and reported, not tested.

## Procedure

```
sandbox   <- copy(project)                  (without .builddiet/)
original  <- fingerprint(every candidate)   the user's bytes, BEFORE any run
cold      <- run(regenerate; verify)        must pass
warm      <- run(regenerate; verify)        must pass: the workflow is repeatable
for each candidate C:
    move C aside; r1 <- run(); R1 <- fingerprint(C); restore C
    if R1 has every file and some bytes differ from original:
        move C aside; r2 <- run(); R2 <- fingerprint(C); restore C
    verdict
```

Fingerprints are taken **before** the baseline on purpose. The baseline run may refresh a stale file in the sandbox, but BuildDiet's claim is about the bytes the user has on disk. Comparing against the refreshed copy would call a stale file `identical`. The first V1 had exactly that bug; BD-REAL found it.

## Verdict

| run r1 | R1 vs original | second run | verdict |
|---|---|---|---|
| timeout | - | - | INCONCLUSIVE |
| a step failed | - | - | REQUIRED |
| pass | nothing or only some files came back | - | NOT REGENERATED |
| pass | identical | - | PROVEN `identical` |
| pass | same files, some bytes differ | fails | INCONCLUSIVE (flaky workflow) |
| pass | same files, some bytes differ | R2 = R1 on those files | **STALE**: the workflow deterministically produces other bytes than the user's copy |
| pass | same files, some bytes differ | R2 ≠ R1 on all of them | PROVEN `recreated` (nondeterministic output) |

Only PROVEN items are ever considered by `plan`. STALE counts as "must back up": the current bytes are not reproducible, even if they are probably garbage.

## Cost

```
rebuild_seconds  = r1.seconds - warm.seconds
expected_seconds = reuse_probability(C) x rebuild_seconds
```

The warm baseline is subtracted so that a long test suite does not inflate every candidate's cost. In BD-REAL the costs measured this way matched the build's own per-target timings to within 0.03s.

## Planner

Given a target `T` in bytes, the planner looks for a subset `S` of PROVEN items that minimises the sum of `expected_seconds(i)` subject to the sum of `bytes(i)` being at least `T`.

This is a min-cost covering knapsack, solved by dynamic programming over sizes quantised to `T/4000`. Sizes are rounded down, so the target is always met. The tests check the planner against brute force.

The result is only a **CANDIDATE PLAN**, because each item was proven leave-one-out. Before a plan is presented as safe, `plan` runs a **joint check** for each project in it:

```
sandbox <- copy(project); fingerprint the plan's items
run() twice                          must pass (otherwise fatal: re-analyze)
move ALL plan items aside at once
run()                                must pass
every item: all files back; identical (or recreated, if it was proven nondeterministic)
original project unchanged
```

If the check fails, the search removes each failing item in turn and solves the knapsack again (Lawler-style branching). The resulting alternatives are explored cheapest-first, until one passes (the **JOINTLY VERIFIED PLAN**), no alternative reaches the target, or `--max-attempts` candidates have been examined. Supersets of a failed set are not explored. Each distinct item set is verified at most once. Exit codes: 0 verified, 3 not enough proven space, 4 no jointly verified plan.

## Known limitations

* **Joint checks cover the plan as a whole.** Removing only part of a verified plan is covered by the individual proofs only. The search is bounded (`--max-attempts`), and each attempt costs a sandbox copy plus three runs.
* **Incremental workflows hide inputs.** An input that an up-to-date build never reads is reported as NOT REGENERATED instead of REQUIRED. Both mean "keep".
* **Stale detection** needs deterministic generators. A stale output of a nondeterministic generator looks like PROVEN `recreated`.
* **`hash = "meta"`** compares sizes only, so stale files of the same size are not detected.
* **Cost additivity.** The planner picks candidates by the sum of individual costs. The joint check measures the real joint rebuild time and reports it next to that sum, but it does not re-rank candidates by it.
* **Full copy.** The sandbox is a full copy of the project.
* **Timing noise.** Costs come from a single run each.
