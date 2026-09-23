# Threat model

## Assets

1. **The user's original data**, above all the irreproducible source of truth.
2. **The correctness of a PROVEN verdict and of a JOINTLY VERIFIED PLAN**, because users will delete based on them.
3. **The user's privacy**, now that BuildDiet can read agent session logs.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| A BuildDiet bug deletes original files | v0.1 has no delete command. Removal happens only through `Sandbox.target()`, which checks paths. The sandbox can't be inside the project. |
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
