# BD-REAL: release gate on a real workspace

**Result: passed.** 12 of 12 verdicts were as expected. `plan --free` chose by measured cost, and every plan was **jointly verified** (all items removed together in a sandbox). The original workspace was not modified.

The gate was re-run on 2026-09-23 after joint-plan verification was added; the results below are from that run.

**Re-run with the final v0.1 code** (levels 0/1 added, the workflow mode here is level 2): the result was again 12/12. Plans for 1 MB, 2 MB and 6 MB were jointly verified, with joint rebuild 0.0s / 0.4s / 0.7s against sums of 0.0s / 0.4s / 0.6s. The original was unchanged and no sandbox was left behind. Absolute sizes vary slightly between runs, because the generator draws a new random corpus each time. This run could prove at most 8.1 MB, compared with 8.3 MB below.

Run on 2026-09-23 with Windows 10 (19045) and Python 3.10.2. The workspace was isolated in `D:\builddiet-bdreal\`, and the sandboxes were on the same drive, in `D:\builddiet-bdreal\sandboxes\`. No other project was touched.

## Reproduce

```bash
python benchmarks/bd_real/make_workspace.py D:/builddiet-bdreal
builddiet analyze D:/builddiet-bdreal/workspace --sandbox-dir D:/builddiet-bdreal/sandboxes
python benchmarks/bd_real/check.py D:/builddiet-bdreal
builddiet plan D:/builddiet-bdreal/workspace --free 2MB --sandbox-dir D:/builddiet-bdreal/sandboxes
```

## The workspace

The workspace is small (12.2 MB) but real. It uses a make-style build (`build.py`): a target is rebuilt only when it is missing or older than its inputs. The work it does is real: quantization, word index, zlib, sharding, SHA-256 features, trigram counts. `verify.py` checks the products that the project depends on. Just as in many real projects, it does *not* check the distribution archive `corpus.z`.

The two problem cases were produced the way they happen in practice:

* **Stale:** the build ran, then `data/weights.f32` was updated and nobody rebuilt. `derived/weights.q8` is now older than its input.
* **Corrupt:** `derived/corpus.z` was truncated by an interrupted write. It is *newer* than its input, so make-style builds consider it up to date.

## Verdicts

| path | role | expected | BuildDiet | rebuild |
|---|---|---|---|---|
| `data/corpus.txt` | canonical input | REQUIRED | REQUIRED (regenerate fails) | - |
| `data/weights.f32` | canonical input | REQUIRED | REQUIRED (regenerate fails) | - |
| `build.py` | code | REQUIRED | REQUIRED | - |
| `verify.py` | code | REQUIRED | REQUIRED (verify fails) | - |
| `notes/meeting-2025-11.md` | not needed, not derived | NOT REGENERATED | NOT REGENERATED | - |
| `derived/weights.q8` | derived, **stale** | STALE | STALE | 0.20s |
| `derived/corpus.z` | derived, **corrupt** | STALE | STALE | 0.15s |
| `cache/ngrams.json` | derived (file) | PROVEN | PROVEN identical | 0.59s |
| `derived/features.bin` | derived (file) | PROVEN | PROVEN identical | 0.42s |
| `derived/corpus.idx.json` | derived (file) | PROVEN | PROVEN identical | 0.09s |
| `derived/shards/` | derived (**directory**) | PROVEN | PROVEN identical | 0.01s |
| `derived/build-info.json` | derived, timestamped | PROVEN | PROVEN recreated (nondeterministic) | 0.00s |

The run covers both directory and single-file candidates.

## Cost measurement

BuildDiet measures rebuild time from outside, as run time minus the warm baseline. `build.py` independently prints how long each rule took. The two agree:

| target | build.py self-reported | BuildDiet measured |
|---|---|---|
| `cache/ngrams.json` | 0.59s | 0.59 to 0.63s |
| `derived/features.bin` | 0.42s | 0.42 to 0.45s |
| `derived/corpus.idx.json` | 0.10s | 0.09 to 0.11s |

## Stability

The analysis was run 4 times. The verdicts were identical every time, and the largest rebuild times varied by at most ±0.03s. SHA-256 hashes of every file in the original workspace were identical before and after the repeated runs, and the built-in write detection reported nothing. Every sandbox was removed afterwards.

## Planner and joint gate

Every plan below is the **JOINTLY VERIFIED PLAN** printed by `plan`. For each target, the first candidate was copied into a fresh sandbox, all of its items were removed at once, and the workflow was run. The same invariants as PROVEN applied: the baseline passes twice, the workflow passes, every file comes back byte-identical, and the original is untouched.

| target | jointly verified plan | joint check | measured joint rebuild | sum of individual estimates | biggest-first (unverified) |
|---|---|---|---|---|---|
| 1 MB | `derived/shards/` | passed on candidate #1 | 0.0s | 0.0s | 0.6s |
| 2 MB | `features.bin` + `shards/` (2.8 MB) | passed on candidate #1 | 0.4s | 0.5s | 0.6s |
| 3 MB | `ngrams.json` | passed on candidate #1 | 0.6s | 0.6s | same set |
| 6 MB | `ngrams.json` + `shards/` | passed on candidate #1 | 0.6s | 0.6s | same set |
| 8.29 MB | `ngrams.json` + `corpus.idx.json` + `features.bin` + `shards/` | passed on candidate #1 | 1.1s | 1.2s | - |
| 20 MB | none: at most 8.3 MB can be reclaimed with individual proofs (exit 3, nothing verified) | - | - | - | - |

The STALE items (`weights.q8`, `corpus.z`) never appear in a plan. Measured joint rebuild times agree with the sum of individual estimates to within 0.1s, so the cost model's additivity assumption held on this workspace. SHA-256 hashes of every file in the original workspace were identical before and after the whole run, and all sandboxes were removed.

In BD-REAL every first candidate passed, because its derived items only depend on canonical inputs. The rejection path is covered by `tests/test_joint.py`. That test builds two artifacts that regenerate each other (one irreproducible asset cached twice) and one independent derived file:

* Individually, both mirrors are PROVEN `identical`.
* `plan --free 15KB`: candidate #1 {mirror_a, mirror_b} **fails** the joint check (verify fails with both removed). Candidate #2 {c.bin, one mirror} passes and becomes the JOINTLY VERIFIED PLAN.
* `plan --free 30KB` needs all three items. The joint check fails, no alternative reaches the target, and the output is `NO JOINTLY VERIFIED PLAN` with exit code 4.

## What BD-REAL changed in BuildDiet

Preparing this gate exposed a correctness bug in the first V1. Fingerprints were taken **after** the baseline run. A stale file that the baseline rebuilt was then compared with its refreshed copy and reported as `identical`. A truncated file was reported as PROVEN `recreated`, which looked the same as a timestamped output. In both cases V1 would have offered the user's non-reproducible bytes for deletion.

Fixes:

* Fingerprints now describe the user's original bytes and are taken before the baseline.
* A candidate whose regeneration differs from the original is regenerated a second time. If both regenerations agree, the verdict is **STALE**. If they differ, the output is nondeterministic and the verdict is PROVEN `recreated`.
* STALE is excluded from plans and counted as "must back up".

Regression tests cover both cases (`tests/test_end_to_end.py`). Against the old code they fail.

## Final gate: the three options

Re-run from scratch with the final v0.1 code; 12/12 verdicts. With the default boundaries (1s / 5m), all four PROVEN outputs rebuild in under a second, so all three options are the same set (8.0 MB, 1.1s) and all PASS.

With the boundaries tightened on purpose (`--light-max 0.05s --normal-max 0.5s`, printed with the options), the three options differ:

| option | frees | rebuild | items |
|---|---|---|---|
| LIGHT | 1.5 MB | 0.0s | derived/shards |
| NORMAL | 2.7 MB | 0.5s | + derived/features.bin (0.4s), derived/corpus.idx.json (0.1s) |
| EXTREME | 8.0 MB | 1.1s | + cache/ngrams.json (0.6s) |

`reclaim --option extreme --yes` freed 8.0 MB. `restore` brought all 4 items back byte-identical. It also reported, as always here, that the declared build refreshed the stale `derived/weights.q8`.
