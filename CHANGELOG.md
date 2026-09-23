# Changelog

## 0.1.0 (unreleased)

First public version. Report-only: BuildDiet never deletes anything of yours.

* `builddiet analyze DIR` works with **no configuration**:
  * **Level 0** proves by SHA-256 that bytes exist elsewhere: identical copies, `.zip`/`.tar*` archives (members decompressed), git (`cat-file --filters`). Files tracked in git are reported as IN GIT and are not planned unless you pass `--include-git`.
  * **Level 1** discovers recipes from scripts, Makefiles, lockfiles, README/AGENTS.md/CI and, opt-in, Codex / Claude Code session logs. It shows them for approval, refuses dangerous commands, and proves a candidate only if a recipe recreates it byte-for-byte without changing any other existing file.
* **Level 2** (optional, `builddiet init`): a declared build + verify workflow, with REQUIRED / PROVEN / STALE / NOT REGENERATED verdicts.
* **STALE** verdict: a deterministic regeneration that differs from the user's copy is never planned.
* `plan --free SIZE`: an exact min-cost covering knapsack, then **joint verification**: the whole candidate plan is removed in a sandbox and must come back byte-for-byte. Otherwise the next plan is tried.
* `report`, `backup-plan`, `scan` across projects.
* Release gates: BD-ZERO (zero-configuration), BD-REAL (declared workflow), BD-PRIOR (prior-art audit).
