# Threat model

## Assets

1. **The user's original data**, above all the irreproducible source of truth.
2. **The correctness of a PROVEN verdict and of a JOINTLY VERIFIED PLAN**, because users will delete based on them.
3. **The user's privacy**, now that BuildDiet can read agent session logs.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| A BuildDiet bug deletes the wrong original files | Only `reclaim.py` deletes outside sandboxes, and only paths of a JOINTLY VERIFIED plan. Each is normalised, checked to be inside the project and not `.builddiet/`, and re-hashed against its proven signature right before deletion. |
| An item changes between analysis and reclaim (the user edited it) | The signature check fails and nothing in that project is deleted (`tests/test_reclaim_watch.py`). |
| The recovery source of an item was changed or deleted | `source_unchanged` is checked at joint verification and again right before deletion. |
| The disk fills up during a sandbox copy or verification | `OutOfSpace` stops the analysis or verification explicitly: "Nothing in your project was changed or deleted". A joint check that runs out of space is fatal, so nothing is verified and nothing is offered (`tests/test_options.py::OutOfSpaceTest`). |
| The disk fills up during reclaim | The restore log is written *before* each deletion. If it cannot be written, that item is not deleted and reclaim stops. |
| Deleted data turns out to be needed right away | `restore` brings it back from the logged recovery (copy / archive / git / recipe / workflow) and checks the bytes. |
| `watch --auto` deletes too much | Only down to `--keep-free`, only within `--max-penalty` of expected rebuild, only byte-identical items. Without `--auto` it always asks. |
| A dialog is left unanswered or the session is non-interactive | Treated as "no". |
| A path the user protected gets read or deleted | Global protection list with absolute precedence, checked before reading, again before deleting, and every `watch` cycle. `--include`, `--include-git`, stale plans and `--auto` cannot override it (`tests/test_protect.py`). |
| The protection is bypassed by another spelling (case, `..`, trailing separator, 8.3 name) | Paths are compared canonically: `realpath` plus `normcase`. |
| The protection is bypassed through a symlink or junction | Links resolving into a protected area count as protected. No walk, copy or delete ever follows a link (`builddiet/fs.py`). Junctions are covered by tests; symlinks too where the OS allows creating them. |
| A parent of a protected path is deleted | `delete_verdict` refuses any path that contains a protected path, and analysis splits such folders. |
| The protection status cannot be determined | Fail closed: treated as protected. An unreadable protection list stops every command. |
| Path traversal in config or archives (`../x`, `..` members) | `normalize_rel` rejects absolute paths, drive letters and `..`. Archive restores skip members containing `..`. |
| A discovered command is dangerous (`git push`, `rm -rf`, `curl`, `pip install`, `docker`, ...) | It is refused and listed as "never run" (`recipes.check_safe`), whatever its source. |
| A discovered command uses an absolute path to the original project, so it would write to the original | Paths to the project are rewritten to `{project}`, which points at the sandbox. Any other absolute path is refused. |
| A recipe overwrites user data as a side effect (e.g. truncates `data/raw.csv`) | Any change to another existing file disqualifies the recipe for that proof: probes reject recipes that change non-candidate files, and trials reject recipes that change anything outside the tested candidate. Covered by `tests/test_zeroconfig.py`. |
| False PROVEN: nothing recreated it, but the workflow passes | Identity check: every file must come back. Otherwise the verdict is NOT REGENERATED / NOT PROVEN. |
| False PROVEN: the user's copy is stale or corrupt, but a good one is regenerated | Comparisons are against the user's bytes, fingerprinted before any run. Differing regenerations are repeated, and if they agree the verdict is STALE, which is never planned. |
| False PROVEN from a same-size, different-content copy or archive | Level 0 compares SHA-256 of every file (archive members decompressed), not sizes or names. |
| Two copies are each other's source, and a plan deletes both | Copies never point at each other. The joint check rejects any plan that removes a recovery's source. |
| Items that recreate each other are both planned | Joint check: the whole plan is removed at once and must be restored byte-for-byte, or the plan is rejected and the next candidate is tried. Covered by `tests/test_joint.py`. |
| A recovery source changes after the analysis | The source is re-checked when a plan is verified (size/mtime, or a re-hash for directories). |
| Agent logs expose other projects' commands | Logs are read only with `--agent-logs`. Only commands whose working directory is inside the analyzed project are kept. Nothing leaves the machine. The logs are read, never modified. |
| A malicious `.builddiet/config.toml` or README in a cloned repo | Workflow commands from a config run as they would with `make`, so review untrusted configs. Discovered commands are shown for approval and pass the refusal list. |
| A stale proof after the repo or toolchain changes | Environment fingerprint in the manifest. `plan` refuses stale manifests unless `--allow-stale`. |
| Runaway or hung commands | A per-run timeout kills the process tree. The verdict is INCONCLUSIVE, or the recipe is skipped. |
| Full disk during a sandbox copy | A free-space check runs before copying (`--sandbox-dir` to use another drive, `--force` to override). Sandboxes are removed even on errors or Ctrl+C. |
