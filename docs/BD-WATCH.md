# BD-WATCH: automatic mode on a real, nearly full drive

**Result: passed.**

* `watch` found two projects by itself and analyzed them.
* It jointly verified a plan across both.
* Without approval it deleted nothing. With `--auto` it deleted 7 items.
* `restore` brought all 7 back byte-for-byte.
* 55 of 56 files are identical to before. The one exception is explained below.

Run on 2026-09-23 with Windows 10 and Python 3.10.2. The drive `D:` was genuinely nearly full: **48.2 GB free of 1.2 TB (3.9%)**, the "aggressive" level, with no simulation. `watch` was pointed only at `D:\builddiet-bdwatch`, which contains two generated projects:

* `ml/workspace`: the BD-ZERO project (zero configuration);
* `corpus/workspace`: the BD-REAL project (declared workflow).

Sandboxes went to `D:\builddiet-sandboxes` automatically (the local drive with the most free space) and were all removed.

## Reproduce

```bash
python benchmarks/bd_zero/make_workspace.py D:/builddiet-bdwatch/ml
python benchmarks/bd_real/make_workspace.py D:/builddiet-bdwatch/corpus
builddiet watch D:/builddiet-bdwatch --once --no-dialog --min-size 1KB --allow-recipes   # asks
builddiet market D:/builddiet-bdwatch
builddiet watch D:/builddiet-bdwatch --once --no-dialog --min-size 1KB --allow-recipes --auto
builddiet restore D:/builddiet-bdwatch/ml/workspace
builddiet restore D:/builddiet-bdwatch/corpus/workspace
```

`--min-size 1KB` is needed only because the projects are tiny. `--no-dialog` was used because the run was not attended.

## What happened

**Cycle 1: no `--auto`, nobody to answer (22s).**

```
disk      3.9% free (48.2 GB of 1.2 TB) -> aggressive
market    analyzing D:\builddiet-bdwatch\corpus\workspace
market    analyzing D:\builddiet-bdwatch\ml\workspace
BuildDiet can safely reclaim 9.8 MB.
Expected worst-case rebuild cost: 2.4s.
Joint verification: PASS
(needed 201.5 GB to get back to 20% free; only 9.8 MB is proven reclaimable within 1h00m of rebuild)
Reclaim 9.8 MB now? [y/N] reclaim   not approved; nothing deleted
```

**Market.**

| item | size | rebuild | value | how to get it back |
|---|---|---|---|---|
| corpus/workspace/derived/shards | 1.6 MB | 0.0s | LOW | your workflow |
| ml/workspace/features | 1.3 MB | 0.0s | LOW | copy of backups/features-2026-09-01 |
| ml/workspace/release | 22.7 KB | 0.0s | MEDIUM | extract from dist/app-1.0.zip |
| corpus/workspace/cache/ngrams.json | 5.4 MB | 0.7s | MEDIUM | your workflow |
| corpus/workspace/derived/features.bin | 1.2 MB | 0.4s | HIGH | your workflow |
| corpus/workspace/derived/corpus.idx.json | 42 KB | 0.1s | HIGH | your workflow |
| ml/workspace/models | 328 KB | 1.2s | HIGH | run: python scripts/train.py |

**Cycle 2: `--auto` (5s).** The cycle re-used the fresh analyses, verified the joint plan again, re-hashed each item, then deleted 7 items (9.8 MB) and logged each deletion. `derived/build-info.json` (a timestamp) and the STALE items were **not** deleted: `watch` only deletes what comes back byte-for-byte.

**Restore.** All 7 items came back byte-identical:

* `ml/workspace`: copy, archive extraction and `train.py`.
* `corpus/workspace`: one run of the declared workflow, which recreated all 4 items.

The SHA-256 of all 56 files was compared before and after. 55 were identical. The one difference is `corpus/workspace/derived/weights.q8`. That project's declared build (level 2) refreshes this stale output whenever it runs, including when `restore` runs it. `restore` reported it:

```
note: your declared workflow (builddiet init) also rewrote these existing files:
          derived/weights.q8
```

Level-0 restores and level-1 recipes are proven not to touch other files. A declared workflow is your build, and it does what your build does.

## Re-run with protected paths

The gate was re-run from scratch after protected paths were added. `BUILDDIET_HOME` pointed at a gate-only folder, so the real `~/.builddiet` was never written.

* **Protected:** `builddiet protect D:\builddiet-bdwatch\ML\workspace\models`, deliberately with different letter case.
* **Excluded for this run:** `--exclude D:\builddiet-bdwatch\corpus\workspace\cache`.
* **Cycle:** `watch --auto` reclaimed 5 items (4.1 MB). `models/` (reported as "protected ... never read, never touched", 0 B) and `cache/` were left intact.
* **Restore:** all 5 items came back byte-identical. 55 of 56 files are identical to before; the exception is again `weights.q8`, refreshed by the declared build and reported.

## What the gate changed in BuildDiet

* **Nondeterministic outputs are no longer deleted by default.** The first run deleted `build-info.json` (a timestamp, PROVEN only in workflow mode), which by nature cannot come back byte-identical. `reclaim` and `watch` now delete only byte-identical items; `reclaim --allow-nondeterministic` opts in.
* **Restore reports workflow side effects**, instead of silently leaving other files changed.
* **Labels.** `watch` and `market` show paths relative to the watched folder: both projects are named `workspace`.
* **Planner.** A plan that exactly fills the target could be lost to size rounding, and a costlier item was added. The planner now never returns a plan costlier than the greedy one, and `watch` refuses any plan above its budget.

## Final gate: `watch` with the three options

Re-run from scratch on the real D: drive (3.9% free, "aggressive"). The setup was: `ml/workspace/models` protected (given as `ML\...`), `corpus/workspace/cache` excluded with `--exclude`, and a gate-only `BUILDDIET_HOME`.

* **Cycle 1: `--auto`, default boundaries.**
  * 201.5 GB were needed; the three options were identical (4.2 MB, 5 items, all PASS). The proposal was LEGGERO, the smallest of the equal options.
  * Within `--auto-max NORMALE`, so it was reclaimed automatically: 5 items.
  * `models` and `cache` were untouched.
* **Restore:** all 5 items came back byte-identical.
* **Cycle 3: `--auto`, with declared boundaries `--light-max 0.05s --normal-max 0.3s`.**
  * The options were LEGGERO 3.0 MB, NORMALE 3.0 MB and ESTREMO 4.2 MB, and ESTREMO was proposed (the disk is critical).
  * The log said "ESTREMO is above --auto-max NORMALE: asking instead", and with nobody to answer, **nothing was deleted**.
* **Cycle 4: the same with `--auto-max estremo`.** ESTREMO was reclaimed (5 items, 4.2 MB).
* **Restore:** everything came back byte-identical. Of 56 files, the only difference is the stale `weights.q8`, refreshed by the declared build and reported by `restore`. No sandbox was left behind.
