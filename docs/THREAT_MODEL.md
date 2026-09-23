# Threat model

## Assets

1. **The user's original data**, above all the irreproducible source of truth.
2. **The correctness of a PROVEN verdict**, because users will delete based on it.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| A BuildDiet bug deletes original files | v0.1 has no delete command. Removal happens only through `Sandbox.target()`, which checks paths. The sandbox can't be inside the project. |
| Path traversal in config (`include = ["../x"]`) | `normalize_rel` rejects absolute paths, drive letters and `..`. |
| A workflow command writes to the original through an absolute path | The original's metadata is snapshotted before and after the analysis, and any change triggers a warning in the report. This detects the write but can't prevent it. |
| A workflow command has external side effects (network, registries, databases, other directories) | Out of scope for v0.1. The user runs their own commands. The docs say this clearly. Future work: run the workflow in a container or under a restricted user. |
| False PROVEN: the workflow passes without the directory but never recreated it | Identity check: every file must come back, otherwise the verdict is NOT REGENERATED. |
| False PROVEN: recreated with wrong content that verify doesn't catch | `identity` distinguishes `identical` from `recreated`, and `plan --strict` uses only byte-identical items. The strength of a proof depends on the verify command. |
| A stale proof after the toolchain or repo changes | Environment fingerprint in the manifest. `plan` refuses stale manifests unless `--allow-stale`. |
| A malicious `.builddiet/config.toml` in a cloned repo runs commands | `analyze` runs the configured commands, which is the same trust level as running `make` in that repo. Review the config of repositories you don't trust before analyzing them. |
| Runaway or hung commands | Per-run timeout, with the process tree killed (`taskkill /T` on Windows, process group on POSIX). The verdict is INCONCLUSIVE. |
| Full disk during the sandbox copy | A free-space check before copying (`--force` to override). The sandbox is removed even on errors or Ctrl+C. |
