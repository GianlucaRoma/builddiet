# Changelog

## 0.2.0 (unreleased)

* Show reclaim options as LIGHT, NORMAL and EXTREME in the CLI, website and documentation. The previous Italian option names are no longer accepted as command arguments.
* Explain the project workflow with a visual sample chart and simpler setup steps on the GitHub Pages site. The comparison with other tools remains in the README rather than on the site.
* Use the repository owner's existing GitHub `noreply` address for new local commits and remove the local home path from the publishing guide.

## 0.1.0 (2026-09-24)

First public version.

* macOS validation: BD-ZERO and BD-REAL each matched 12/12 expected verdicts; a four-item reclaim restored all 70 original files byte-for-byte. See [docs/MAC-VALIDATION.md](docs/MAC-VALIDATION.md).
* Metadata-only identity checks are no longer accepted as byte-identical proofs. Nondeterministic workflow restores now require every original file path and type to return; a merely nonempty output is insufficient.
* Recipe probes run progressively and stop when a candidate is already proven. The CLI output now distinguishes sandbox joint checks from the content-checked shortcut for copy/archive/git-only plans.
* Reclaim records are flushed to disk before removal, and a newly protected restore log prevents deletion.
* Sandbox and reclaim paths now reject linked parent directories, including links introduced after analysis that could redirect a nested item outside its root.

* Recheck duplicate and archive recovery sources by content before reclaiming, including when size and modification time are unchanged. Previously, an archive with unchanged metadata could pass the quick source check.
* Keep individually protected files out of sandbox copies, duplicate indexes, recipe discovery and manifest input hashes. Directories containing links are not given a level-0 proof because that restore method cannot recreate those links.
* Recognize both canonical and non-canonical project paths in discovered commands (for example `/var` and `/private/var` on macOS).

* `builddiet analyze DIR` works with **no configuration**:
  * **Level 0** proves by SHA-256 that bytes exist elsewhere: identical copies, `.zip`/`.tar*` archives (members decompressed), git (`cat-file --filters`). Files tracked in git are reported as IN GIT and are not planned unless you pass `--include-git`.
  * **Level 1** discovers recipes from scripts, Makefiles, lockfiles, README/AGENTS.md/CI and, opt-in, Codex / Claude Code session logs. It shows them for approval, refuses dangerous commands, and proves a candidate only if a recipe recreates it byte-for-byte without changing any other existing file.
* **Level 2** (optional, `builddiet init`): a declared build + verify workflow, with REQUIRED / PROVEN / STALE / NOT REGENERATED verdicts.
* **STALE** verdict: a deterministic regeneration that differs from the user's copy is never planned.
* **Three options instead of an amount.** `analyze <folder>` / `plan <folder>` show **LIGHT / NORMAL / EXTREME**:
  * LIGHT: restore by copy, or rebuild ≤ 1s;
  * NORMAL (recommended): rebuild ≤ 5m;
  * EXTREME: everything proven.

  Each option is jointly verified, with space, measured rebuild, item types and left-out items. `reclaim` lets you choose one and confirm; `watch` proposes the smallest sufficient one (`--auto` up to `--auto-max`, default NORMAL). The boundaries are documented and adjustable.
* Out-of-space during verification is an explicit, safe error (`OutOfSpace`). `reclaim` writes each restore record before deleting.
* Tests run in an isolated test area on the drive with the most free space, and refuse to start without 1 GB free. The earlier intermittent failure was an external process filling C: to 0 MB; see [docs/TEST-ISOLATION.md](docs/TEST-ISOLATION.md).
* Advanced / CI: `plan --free SIZE`: an exact min-cost covering knapsack, then **joint verification**: the whole candidate plan is removed in a sandbox and must come back byte-for-byte. Otherwise the next plan is tried.
* **`watch DIR`**: finds the projects under a folder and keeps a market of proofs. It prepares and jointly verifies a plan below 15% free, asks (desktop dialog / terminal) below 10%, and allows a bigger rebuild budget below 5%. `--auto` reclaims within `--keep-free` / `--max-penalty`.
* **`reclaim`** deletes a jointly verified plan: byte-identical items only by default, each re-hashed before deletion and logged. **`restore`** brings items back and checks the bytes.
* **`market DIR`**: every provably reclaimable item across projects with a LOW/MEDIUM/HIGH value.
* **Protected paths**: `protect` / `unprotect` / `protected` (persistent, global) and `--exclude` (per run). Protection has absolute precedence in analyze, plan, market, scan, reclaim, restore and `watch --auto`. It resists case / `..` / 8.3 spelling differences and symlinks / junctions, and fails closed.
* No walk, copy or deletion follows symlinks or junctions (`builddiet/fs.py`).
* Sandboxes default to the local drive with the most free space (`$BUILDDIET_SANDBOX_DIR` to pin it).
* `report`, `backup-plan`, `scan` across projects.
* Release gates: BD-ZERO (zero-configuration), BD-REAL (declared workflow), BD-WATCH (automatic mode on a real near-full drive), BD-PRIOR (prior-art audit).
