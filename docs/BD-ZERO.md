# BD-ZERO: release gate for zero-configuration use

**Result: passed.** BuildDiet was given only a folder: no config, no commands. All 12 verdicts matched the expected ones, identically across 3 repeated runs. Every plan was jointly verified, and the original workspace stayed byte-identical.

Run on 2026-09-23, Windows 10 (19045), Python 3.10.2, git 2.55. The workspace was isolated in `D:\builddiet-bdzero\`, with sandboxes on the same drive.

## Reproduce

```bash
python benchmarks/bd_zero/make_workspace.py D:/builddiet-bdzero
builddiet analyze D:/builddiet-bdzero/workspace --min-size 1KB \
    --agent-logs-dir D:/builddiet-bdzero/agent-logs --sandbox-dir D:/builddiet-bdzero/sandboxes --yes
python benchmarks/bd_zero/check.py D:/builddiet-bdzero
builddiet plan D:/builddiet-bdzero/workspace --free 2MB --sandbox-dir D:/builddiet-bdzero/sandboxes
```

`--min-size 1KB` is needed only because the workspace is small (6 MB). `--yes` replaces the interactive confirmation. `--agent-logs-dir` points at **one fabricated, clearly synthetic** Codex-style session log created by the generator. No real agent logs were read.

## The workspace

The workspace is a git repository shaped like a small data/ML project:

* **Tracked in git:** `src/`, `scripts/`, `README.md`.
* **Untracked data:** raw data, notes, features, a backup of the features, a trained model, metrics, a timestamped report, a release zip and its extracted copy, and a cache written by an inline `python -c` command that appears only in the agent log.
* **Traps:**
  * the on-disk metrics come from an older model;
  * the report embeds the build time;
  * the README documents `python scripts/publish.py && git push`.

## Verdicts

| path | truth | BuildDiet | how / why | cost |
|---|---|---|---|---|
| `src/`, `scripts/` | tracked source | IN GIT | `git cat-file --filters` matches every byte | 0.12s / 0.38s |
| `data/` | canonical | NOT PROVEN | the recipes that read it fail without it | - |
| `notes/` | irreproducible | NOT PROVEN | no recipe | - |
| `features/` | derived, copy in `backups/` | PROVEN | copy of `backups/features-2026-09-01` | 0.01s (est.) |
| `backups/` | holds that copy | NOT PROVEN | "keep: features is restored from it" | - |
| `models/` | made by `scripts/train.py` | PROVEN | `run: python scripts/train.py` (script mentions it) | 1.24-1.26s |
| `exports/` | older copy of `eval.py` output | **STALE** | `eval.py` reproduces different bytes twice | - |
| `reports/` | timestamped | INCONCLUSIVE | different bytes on every run | - |
| `release/` | extracted from `dist/app-1.0.zip` | PROVEN | members decompressed and hashed | 0.001s |
| `dist/` | holds the zip | NOT PROVEN | "keep: release is restored from it" | - |
| `cache/` | inline command, only in the agent log | PROVEN | agent log: ran right before it was written | 0.21s |
| `publish.py && git push` | dangerous | never run | "changes git state" | - |

Recipe costs are full run times. `train.py`'s ~1.25s is the real cost of getting the model back.

## Jointly verified plans

| target | jointly verified plan | measured joint rebuild | sum of individual estimates |
|---|---|---|---|
| 500 KB | `features/` (copy) | 0.0s (sources checked, nothing to run) | 0.0s |
| 1.5 MB | `cache/` + `features/` | 0.3s | 0.2s |
| 2 MB | `cache/` + `features/` + `models/` | 1.6s | 1.5s |
| 2.3 MB, 3 MB | none: only 2.29 MB can be proven (exit 3) | - | - |

The 2 MB joint check copied the workspace into a fresh sandbox, removed all three items, and then:

* restored `features/` from its backup;
* ran the agent-log command and `train.py`;
* compared every item byte-for-byte with the user's copy;
* confirmed that no other file changed.

## What the gate changed in BuildDiet

* **Safety.** An intermediate design tolerated recipes that "rewrite" other candidates, so that stale outputs would not block proofs. That design is gone: a script that truncates canonical data "recreates" it too, and from outside the two cases are indistinguishable. Now any change to another existing file disqualifies the recipe (`tests/test_zeroconfig.py::SideEffectTest`).
* **Usefulness.** The demo project's `build.py` writes `logs/last.log` on every run, which the strict rule rejected. A short, explicit list of volatile files (logs, bytecode, tool caches) is now ignored as a side effect (`VolatileSideEffectTest`).
* **Cost.** Recipes first used "run time minus warm run time", as for a declared workflow. That makes a script that always does its full work look free: `models/` showed 0.0s. Recipe cost is now the full run time.
* **Robustness.** A Windows console (cp1252) crashed on a non-ASCII character in a tool's error message. CLI output is now written with replacement characters.

## Final gate: the three options (LIGHT / NORMAL / EXTREME)

Re-run from scratch with the final v0.1 code: `builddiet analyze <workspace>`, with no amount given. Results:

* 12/12 verdicts, and the original workspace was unchanged.
* `analyze` showed the options:

| option | frees | rebuild | items | joint verification |
|---|---|---|---|---|
| LIGHT | 1.9 MB | 0.2s | features (copy), release (archive), cache (agent-log recipe, 0.2s) | PASS |
| **NORMAL** (recommended) | 2.3 MB | 1.5s | + models (`train.py`, 1.3s) | PASS |
| EXTREME | 2.3 MB | 1.5s | nothing more costs above 5 minutes | PASS |

* `reclaim --option normal --yes` freed 2.3 MB (4 items).
* `restore` brought all 4 back, and every file of the workspace was byte-identical to before.
