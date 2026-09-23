# BuildDiet

**Prove what's disposable. Keep what matters.**

BuildDiet runs experiments to find out which parts of a development workspace are regenerable. It measures what each part costs to rebuild. Then it finds the cheapest *proven* way to reclaim disk space.

> It doesn't guess what you can delete. It proves it.
> *Non indovina cosa puoi cancellare. Lo dimostra.*

## Why

Workspaces fill up with build trees, caches, generated datasets, model conversions, intermediate layouts and old outputs. Deleting them by hand is scary for two reasons: you don't know what is still needed, and you don't know how long it will take to get it back.

Existing cleaners work from a **catalog**. They know that `node_modules/`, `target/` and `.venv/` are disposable. That is useful, but it has a limit. No catalog knows what `experiments/arena/`, `layout_v2.tmp/` or `weird_stuff/foo_2026/` are, and in real projects those are usually the biggest directories.

BuildDiet takes an experimental approach and needs no catalog:

```
copy the project into a sandbox
run your workflow (regenerate + verify)       -> baseline must PASS
for each directory:
    remove it (in the sandbox only)
    run the workflow again, timed
    did the directory come back?  did verify pass?  are the bytes the same?
```

## What you get

```
BUILDDIET - demo

  Total workspace          13.0 MB
  PROVEN REQUIRED              0 B
  PROVEN REGENERABLE        9.5 MB
  NOT REGENERATED (keep)    3.5 MB
  KNOWN, NOT PROVEN            0 B
  UNKNOWN / NOT TESTED      2.2 KB

SPACE YOU CAN PROVABLY RECLAIM
  path             size   rebuild   identity
  build/         3.0 MB      0.2s   identical
  weird_stuff/   4.0 MB      0.5s   identical     <- no catalog knows this one
  arena/         2.5 MB      3.0s   identical
```

And the part that matters most:

```
$ builddiet plan --free 3MB

  path       size   rebuild   p(reuse)   expected
  build/   3.0 MB      0.2s       1.00       0.2s

  Frees 3.0 MB; expected rebuild penalty 0.2s.
  Deleting biggest-first would cost 0.5s instead.
```

You say how much space you need. BuildDiet solves a min-cost covering knapsack over the *proven* items and gives you the set with the lowest rebuild cost that frees at least that much. With real numbers the difference is "wait 25 seconds" versus "wait 3 hours".

## Verdicts

| Verdict | Meaning | Deletable? |
|---|---|---|
| **PROVEN** | Removed in the sandbox, the workflow recreated it, and verify passed. `identity` says whether it came back byte-for-byte (`identical`) or with different bytes (`recreated`, e.g. timestamps). | Yes, at the measured cost |
| **REQUIRED** | Removing it makes regenerate or verify fail. | No |
| **NOT REGENERATED** | The workflow passes without it, but nothing recreates it. | **No.** `family_photos/` is not needed by your build either. |
| **KNOWN** | Matches an ecosystem catalog (`node_modules`, `target`, ...) but was not tested. | By convention only |
| **UNKNOWN** | Not tested (excluded, too small, loose files, metadata). | No |

The key rule: *"the project still works without it"* is **not** the same as *"it is disposable"*. BuildDiet only calls a directory PROVEN when it has watched the workflow recreate those bytes.

## Quick start

```bash
pip install -e .
builddiet init path/to/project          # detects Make/CMake/Cargo/npm/Python and asks for the workflow
builddiet analyze path/to/project --dry-run   # what would be tested
builddiet analyze path/to/project       # run the experiments (sandboxed)
builddiet report path/to/project
builddiet plan path/to/project --free 20GB
builddiet backup-plan path/to/project   # which bytes are irreproducible
builddiet scan C:/Projects              # summary over all analyzed projects
builddiet plan C:/Projects --free 100GB # cheapest plan across projects
```

To try it on a synthetic workspace:

```bash
python benchmarks/make_demo.py /tmp/bd
```

## Configuration

`init` writes `<project>/.builddiet/config.toml`:

```toml
[commands]
regenerate = 'cmake -S . -B build && cmake --build build'
verify = 'ctest --test-dir build --output-on-failure'
timeout = 3600

[candidates]
auto = true            # every top-level directory is a candidate
depth = 1
include = ['experiments/cache']   # also test this one on its own
exclude = ['family_photos']       # never touched, not even in the sandbox
min_size = '10MB'

[identity]
hash = 'full'          # or 'meta' (paths + sizes) for huge trees

[reuse]                # optional: probability the data is needed again soon
'old-results' = 0.05   # planner minimises probability x rebuild time
```

Results go to `.builddiet/manifest.json`, together with an environment fingerprint: platform, git HEAD, top-level project files, and config. If any of these change, `report` warns and `plan` refuses to use the old proofs unless you pass `--allow-stale`.

## Safety

* **Report-only.** v0.1 never deletes anything outside its own sandbox.
* **The original is only read.** The project is copied into a temporary sandbox, and every destructive step is path-checked to stay inside it.
* **Writes to the original are detected.** If a command writes outside the sandbox (for example through an absolute path), the report says so.
* **The baseline must pass twice.** If the workflow fails, or isn't repeatable on an untouched copy, nothing gets proven.

Read [docs/SAFETY.md](docs/SAFETY.md), [docs/EXPERIMENT_MODEL.md](docs/EXPERIMENT_MODEL.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) before pointing it at anything important.

## Layout

```
builddiet/
  scanner.py      partition the tree into non-overlapping regions, pick candidates
  sandbox.py      isolated copy; the only place anything is removed
  experiment.py   baseline + remove/regenerate/verify loop
  verifier.py     fingerprints and identity (identical / recreated / partial / absent)
  cost.py         rebuild penalty = run time - warm baseline; x reuse probability
  planner.py      exact min-cost covering knapsack (DP) + size-first comparison
  manifest.py     persisted proofs + environment fingerprint / staleness
  report.py       text output
  adapters/       Make, CMake, Cargo, Node, Python: workflow hints + KNOWN catalogs
tests/            unit + end-to-end tests (python -m unittest)
benchmarks/       demo workspace generator
```

## Roadmap

* **BD0 to BD4 (this release):** user-declared workflow, sandbox experiments, cost measurement, planner, multi-project scan.
* Copy-on-write sandboxes (reflinks, ReFS/Btrfs/APFS clones) so large workspaces don't need a full copy.
* Inferring the workflow from CI definitions (`.github/workflows`, `AGENTS.md`).
* Observing which files a command writes (file tracing) to find finer-grained regenerable paths.
* Reuse probability learned from access history instead of declared.
* Daemon mode: reclaim the cheapest proven data when free space drops below a threshold.
* Build-farm mode: cost-aware eviction across many repositories, measured instead of configured.

## Requirements

Python 3.9+. No dependencies.

## License

MIT
