# BuildDiet

**Prove what's disposable. Keep what matters.**

BuildDiet runs removal experiments in a sandbox copy of a workspace to find out which files and directories your own workflow can regenerate. It measures what each one costs to rebuild, then plans the cheapest way to reclaim disk space and verifies that plan by removing everything in it together. It is report-only: it never deletes anything of yours.

> *Non indovina cosa puoi cancellare. Lo verifica.*

## Why

Workspaces fill up with build trees, caches, generated datasets, model conversions and old outputs. Deleting them by hand is scary for two reasons: you don't know what is still needed, and you don't know how long it will take to get it back.

Disk cleaners decide from a **catalog**: they know that `node_modules/` and `target/` are disposable. Cost-aware systems such as Nectar, lineage-based workflow managers and build caches decide from **lineage they recorded themselves**. Neither helps with `experiments/arena/` or `layout_v2.tmp`, directories that are specific to your project, that no catalog knows about, and that no system recorded when they were created.

BuildDiet finds out by experiment:

```
copy the workspace into a sandbox
fingerprint every candidate (your bytes, SHA-256)
run your workflow (regenerate + verify) twice      -> must PASS
for each candidate file or directory:
    remove it (in the sandbox only), run the workflow, time it
    did verify pass?  did it come back?  are the bytes yours?
    if they differ: regenerate again -> stale copy, or nondeterministic output?
```

## Real result (BD-REAL gate)

This is a small real workspace: a make-style build doing real work, with canonical inputs, derived outputs, a stale output, a truncated one, and notes that nothing needs. The full write-up is in [docs/BD-REAL.md](docs/BD-REAL.md).

```
  Total workspace           12.2 MB
  PROVEN REQUIRED            3.2 MB
  PROVEN REGENERABLE         8.3 MB
  NOT REGENERATED (keep)     3.3 KB
  STALE COPY (review)      660.8 KB

SPACE YOU CAN PROVABLY RECLAIM
  path                         size   rebuild   identity
  derived/build-info.json     132 B      0.0s   recreated
  derived/shards/            1.6 MB      0.0s   identical
  cache/ngrams.json          5.4 MB      0.6s   identical
  derived/features.bin       1.2 MB      0.4s   identical
  derived/corpus.idx.json   41.9 KB      0.1s   identical

REQUIRED:         data/corpus.txt, data/weights.f32, build.py, verify.py
NOT REGENERATED:  notes/meeting-2025-11.md
STALE:            derived/weights.q8 (inputs changed), derived/corpus.z (truncated)
```

All 12 verdicts matched the expected ones, and they were identical across 4 runs. The rebuild times BuildDiet measured matched the build's own timings. The original workspace was byte-identical afterwards.

```
$ builddiet plan --free 2MB --sandbox-dir D:/sandboxes
CANDIDATE PLAN #1
  derived/features.bin   1.2 MB   0.5s
  derived/shards/        1.6 MB   0.0s
  -> joint check PASSED: with all 2 items removed together, everything was recreated and verify passed
================================================================
JOINTLY VERIFIED PLAN
  derived/features.bin   1.2 MB   0.5s
  derived/shards/        1.6 MB   0.0s
  Frees 2.8 MB. Measured joint rebuild 0.4s (individual estimates sum to 0.5s).
  An unverified biggest-first choice would cost 0.6s.
```

Individual proofs are leave-one-out, so `plan` treats the cheapest set as a **candidate** only. It copies the workspace into a fresh sandbox, removes every item in the candidate at once, and applies the same invariants as PROVEN. A candidate that fails is never presented as verified. BuildDiet then tries the next-cheapest candidate, or reports that no jointly verified plan exists for that target. Two artifacts that regenerate each other are both PROVEN individually, but a plan that removes both is rejected (`tests/test_joint.py`).

## Verdicts

| Verdict | Meaning | Offered for reclaiming? |
|---|---|---|
| **PROVEN** | Removed in the sandbox, the workflow recreated it, and verify passed. `identical` means byte-for-byte. `recreated` means the bytes differ on every regeneration (timestamps). | Yes, at the measured cost |
| **REQUIRED** | Without it, regenerate or verify fails. | No |
| **NOT REGENERATED** | The workflow passes without it, but nothing recreates it. | **No.** Your build doesn't need `family_photos/` either. |
| **STALE** | The workflow recreates it, the same way twice, but *differently from your copy*. That means a stale or corrupt output, or hand edits. Your current bytes are not reproducible. | No, review it |
| **KNOWN** | Matches an ecosystem catalog (`node_modules`, `target`, ...) but was not tested. | No |
| **UNKNOWN** | Not tested (excluded, too small, metadata). | No |

## Quick start

```bash
pip install -e .
builddiet init path/to/project                 # suggests a workflow for Make/CMake/Cargo/npm/Python
builddiet analyze path/to/project --dry-run    # list what would be tested
builddiet analyze path/to/project --sandbox-dir D:/sandboxes
builddiet plan path/to/project --free 20GB --sandbox-dir D:/sandboxes   # jointly verified
builddiet backup-plan path/to/project          # which bytes are irreproducible
builddiet scan C:/Projects                     # summary across analyzed projects
```

`init` writes `<project>/.builddiet/config.toml`:

```toml
[commands]
regenerate = 'cmake -S . -B build && cmake --build build'
verify = 'ctest --test-dir build --output-on-failure'

[candidates]
depth = 1                          # directories and files (>= min_size) at this depth
include = ['experiments/cache', 'models/layout.tmp']   # test these individually
exclude = ['family_photos']        # never touched, not even in the sandbox
min_size = '10MB'

[reuse]                            # optional: planner minimises p(reuse) x rebuild time
'old-results' = 0.05
```

## Limits (read before trusting a plan)

* **Joint verification has a budget.** `plan` verifies up to `--max-attempts` candidates (default 5). Each attempt needs a full sandbox copy and three workflow runs. Candidates are explored cheapest-first by excluding failing items; supersets of a failed set are never tried. If no candidate passes, `plan` says so and exits with code 4. `--no-verify` shows only the candidate, clearly labelled as NOT jointly verified. A verified plan is verified as a whole: if you delete only part of it, only the individual proofs cover that part.
* **A proof is only as strong as your verify command.** PROVEN means "recreated, and your verify command still passes".
* **Incremental builds can hide inputs.** An input that an up-to-date build never reads shows up as NOT REGENERATED rather than REQUIRED. Both mean "keep", so this errs on the safe side.
* **Stale detection** needs a deterministic generator. A stale file produced by a nondeterministic generator is reported as PROVEN `recreated`.
* **Commands run for real** inside the sandbox. Side effects outside it (network, databases, absolute paths) are not contained. Writes to the original project are detected and reported, but not prevented.
* **The sandbox is a full copy.** You need free space about equal to the workspace size, on some drive (`--sandbox-dir`).
* **Rebuild times come from single runs** and assume a warm machine. Remote inputs that could disappear later are not modelled.
* **Tested scale:** unit tests and BD-REAL (12 MB, Windows). It has not been validated on large real workspaces yet.

See [docs/EXPERIMENT_MODEL.md](docs/EXPERIMENT_MODEL.md), [docs/SAFETY.md](docs/SAFETY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Related work

Cost-aware reclamation of derived data is well established. Nectar (OSDI 2010) deletes derived datasets by a cost/benefit ratio until a space target is met. Minimum-cost store-or-regenerate strategies for workflow data date from 2010 onwards. Hitachi patents delete regenerable data using recorded regeneration time. Build caches such as kache are adding rebuild-cost-aware eviction. All of these know what is regenerable from lineage they recorded, from declarations, or from catalogs.

BuildDiet instead establishes regenerability of existing, unmanaged data by removal experiments, and feeds the measured costs into the plan. We are not aware of prior work that does this, but the audit in [docs/BD-PRIOR.md](docs/BD-PRIOR.md) has stated limits.

## Layout

```
builddiet/
  scanner.py      non-overlapping partition into candidate directories and files
  sandbox.py      isolated copy; the only place anything is removed
  experiment.py   baseline, then remove / regenerate / verify (and re-verify when bytes differ)
  verifier.py     fingerprints; identical / recreated / partial / absent / stale
  cost.py         rebuild penalty = run time - warm baseline; x reuse probability
  planner.py      exact min-cost covering knapsack (DP) + joint-verified candidate search
  manifest.py     persisted proofs + environment fingerprint / staleness
  adapters/       Make, CMake, Cargo, Node, Python: workflow hints + KNOWN catalogs
tests/            unit + end-to-end tests (python -m unittest)
benchmarks/       demo generator; bd_real/ release-gate workspace + checker
docs/             SAFETY, EXPERIMENT_MODEL, THREAT_MODEL, BD-REAL, BD-PRIOR
```

## Requirements

Python 3.9+, no dependencies. Tested on Windows 10 with Python 3.10.

## License

MIT
